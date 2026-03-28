"""
School Affairs Chatbot — Core Logic (RAG)
Strategy:
  1. On startup: download ALL PDFs, split into large overlapping chunks,
     build an in-memory keyword index (full-text, not just summaries).
  2. Per query: score every chunk by CJK character + Latin word overlap;
     pick the top-K most relevant chunks from across all documents.
  3. Send only those chunks to Qwen — accurate and efficient.
"""

import io
import re
import base64
import requests
from typing import Optional
from openai import OpenAI
import pypdf

# ── Constants ──────────────────────────────────────────────────────────────────
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL  = "qwen-plus"
CHUNK_SIZE     = 3000   # large chunks = more context per hit
CHUNK_OVERLAP  = 500    # overlap ensures topics at chunk boundaries are captured
TOP_K          = 8      # chunks sent to the model per query
MAX_CTX_CHARS  = 24_000 # hard cap on total context chars sent to model


class ChatbotError(Exception):
    pass


class Chunk:
    """A text chunk from a PDF with pre-tokenised keyword sets for fast scoring."""
    __slots__ = ("text", "source", "page_tag", "tok_cjk", "tok_latin")

    def __init__(self, text: str, source: str, page_tag: str):
        self.text      = text
        self.source    = source
        self.page_tag  = page_tag
        self.tok_cjk   = {c for c in text if "\u4e00" <= c <= "\u9fff"}
        self.tok_latin = set(re.findall(r"[a-zA-Z0-9]+", text.lower()))


class SchoolChatbot:
    def __init__(
        self,
        qwen_api_key: str,
        github_repo: str,
        github_path: str = "",
        github_token: Optional[str] = None,
        model: str = DEFAULT_MODEL,
    ):
        if not qwen_api_key:
            raise ChatbotError("缺少 Qwen API Key")

        self.client       = OpenAI(api_key=qwen_api_key, base_url=QWEN_BASE_URL)
        self.github_repo  = github_repo.strip("/") if github_repo else ""
        self.github_path  = github_path.strip("/") if github_path else ""
        self.github_token = github_token
        self.model        = model

        self._pdf_text_cache: dict[str, str]       = {}
        self._pdf_list_cache: Optional[list[dict]] = None
        self._uploaded_pdfs:  set[str]             = set()
        self._chunk_index:    list[Chunk]           = []
        self._indexed_pdfs:   set[str]             = set()

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def has_content(self) -> bool:
        return bool(self._pdf_list_cache or self._uploaded_pdfs)

    @property
    def index_ready(self) -> bool:
        return bool(self._chunk_index)

    # ── GitHub helpers ─────────────────────────────────────────────────────────

    def _gh_headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            h["Authorization"] = f"token {self.github_token}"
        return h

    def get_pdf_list(self, force_refresh: bool = False) -> list[dict]:
        if self._pdf_list_cache is not None and not force_refresh:
            return self._pdf_list_cache

        path = self.github_path or ""
        url  = f"https://api.github.com/repos/{self.github_repo}/contents/{path}"

        try:
            resp = requests.get(url, headers=self._gh_headers(), timeout=15)
        except requests.RequestException as e:
            raise ChatbotError(f"GitHub 連接失敗：{e}")

        if resp.status_code == 404:
            raise ChatbotError(f"找不到倉庫或路徑：{self.github_repo}/{path}")
        if resp.status_code == 403:
            raise ChatbotError("GitHub API 速率限制，請稍後再試或提供 Personal Access Token")
        if not resp.ok:
            raise ChatbotError(f"GitHub API 錯誤 {resp.status_code}")

        items = resp.json()
        if not isinstance(items, list):
            raise ChatbotError("指定路徑不是目錄，請確認 PDF 文件夾路徑")

        self._pdf_list_cache = [
            {
                "name":         item["name"],
                "download_url": item["download_url"],
                "path":         item["path"],
                "size":         item.get("size", 0),
            }
            for item in items
            if item.get("type") == "file" and item["name"].lower().endswith(".pdf")
        ]
        return self._pdf_list_cache

    def push_pdf_to_github(self, filename: str, data: bytes) -> str:
        if not self.github_repo:
            raise ChatbotError("未設定 GitHub 倉庫，無法儲存文件")
        if not self.github_token:
            raise ChatbotError("需要 GitHub Token（具備 repo 寫入權限）才能儲存文件")

        file_path = f"{self.github_path}/{filename}" if self.github_path else filename
        api_url   = f"https://api.github.com/repos/{self.github_repo}/contents/{file_path}"
        headers   = {**self._gh_headers(), "Content-Type": "application/json"}

        sha: Optional[str] = None
        check = requests.get(api_url, headers=headers, timeout=10)
        if check.status_code == 200:
            sha = check.json().get("sha")

        payload: dict = {
            "message": f"上傳 PDF：{filename}",
            "content": base64.b64encode(data).decode(),
        }
        if sha:
            payload["sha"] = sha

        resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            self._pdf_list_cache = None
            return resp.json().get("content", {}).get("html_url", "")
        elif resp.status_code == 401:
            raise ChatbotError("GitHub Token 無效或已過期，請重新填寫")
        elif resp.status_code == 403:
            raise ChatbotError("GitHub Token 沒有倉庫寫入權限，需要 repo scope")
        else:
            raise ChatbotError(
                f"上傳失敗（{resp.status_code}）：{resp.json().get('message', resp.text[:200])}"
            )

    # ── PDF extraction ─────────────────────────────────────────────────────────

    def _extract_text(self, pdf: dict) -> str:
        """Download a PDF and return its full extracted text. Result is cached."""
        name = pdf["name"]
        if name in self._pdf_text_cache:
            return self._pdf_text_cache[name]

        try:
            resp = requests.get(pdf["download_url"], timeout=30)
            resp.raise_for_status()
            reader = pypdf.PdfReader(io.BytesIO(resp.content))
            pages  = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"[第 {i + 1} 頁]\n{text.strip()}")
            result = "\n\n".join(pages)
            if not result.strip():
                result = f"[{name} 無法提取文字，可能為掃描圖片版本]"
        except Exception as e:
            result = f"[無法讀取 {name}：{e}]"

        self._pdf_text_cache[name] = result
        return result

    # ── Chunk index ────────────────────────────────────────────────────────────

    def _make_chunks(self, text: str, source: str) -> list[Chunk]:
        page_re      = re.compile(r"\[第\s*\d+\s*頁\]")
        current_page = ""
        chunks: list[Chunk] = []
        start = 0
        while start < len(text):
            end   = min(start + CHUNK_SIZE, len(text))
            piece = text[start:end]
            for m in page_re.finditer(text, 0, end):
                current_page = m.group()
            chunks.append(Chunk(piece, source, current_page))
            if end >= len(text):
                break
            start += CHUNK_SIZE - CHUNK_OVERLAP
        return chunks

    def build_index(self, progress_callback=None) -> int:
        """
        Download every PDF from GitHub, extract full text, build chunk index.
        Call once on startup; queries will be fast thereafter.
        progress_callback(done, total, filename) — optional UI progress hook.
        """
        self._chunk_index.clear()
        self._indexed_pdfs.clear()

        pdfs  = self.get_pdf_list(force_refresh=True)
        total = len(pdfs)

        for i, pdf in enumerate(pdfs):
            if progress_callback:
                progress_callback(i, total, pdf["name"])
            text = self._extract_text(pdf)
            if not text.startswith("[無法") and "無法提取文字" not in text:
                self._chunk_index.extend(self._make_chunks(text, pdf["name"]))
                self._indexed_pdfs.add(pdf["name"])

        if progress_callback:
            progress_callback(total, total, "完成")
        return len(self._chunk_index)

    def _score_chunk(self, chunk: Chunk, q_cjk: set, q_latin: set) -> float:
        cjk = len(q_cjk & chunk.tok_cjk)    / len(q_cjk)   if q_cjk   else 0.0
        lat = len(q_latin & chunk.tok_latin) / len(q_latin) if q_latin else 0.0
        return max(cjk, lat)

    def search_index(self, query: str) -> list[Chunk]:
        """Return the top-K most relevant chunks for the given query."""
        q_cjk   = {c for c in query if "\u4e00" <= c <= "\u9fff"}
        q_latin = set(re.findall(r"[a-zA-Z0-9]+", query.lower()))
        scored  = sorted(
            ((c, self._score_chunk(c, q_cjk, q_latin)) for c in self._chunk_index),
            key=lambda x: x[1],
            reverse=True,
        )
        results: list[Chunk] = []
        total_chars = 0
        for chunk, score in scored:
            if score <= 0 or total_chars >= MAX_CTX_CHARS:
                break
            results.append(chunk)
            total_chars += len(chunk.text)
            if len(results) >= TOP_K:
                break
        return results

    # ── PDF upload ─────────────────────────────────────────────────────────────

    def ingest_uploaded_pdf(self, name: str, data: bytes) -> int:
        """Extract text from an uploaded PDF and add to the chunk index."""
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            pages  = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"[第 {i + 1} 頁]\n{text.strip()}")
            text = "\n\n".join(pages)
            if not text.strip():
                text = f"[{name} 無法提取文字，可能為掃描圖片版本]"
        except Exception as e:
            text = f"[無法讀取 {name}：{e}]"

        self._pdf_text_cache[name] = text
        self._uploaded_pdfs.add(name)

        # Remove stale chunks for this file then add fresh ones
        self._chunk_index = [c for c in self._chunk_index if c.source != name]
        if not text.startswith("[無法") and "無法提取文字" not in text:
            new_chunks = self._make_chunks(text, name)
            self._chunk_index.extend(new_chunks)
            return len(new_chunks)
        return 0

    # ── Public chat API ────────────────────────────────────────────────────────

    def chat(
        self,
        question: str,
        chat_history: Optional[list[dict]] = None,
    ) -> tuple[str, list[str]]:
        """
        Answer a question using top-K chunks from the full-text index.
        """
        pdf_list = self.get_pdf_list() if self.github_repo else []
        if not pdf_list and not self._uploaded_pdfs:
            return "目前沒有任何 PDF 文件可供查閱，請上傳文件或設定 GitHub 倉庫。", []

        context_parts: list[str] = []
        source_files:  list[str] = []

        if self.index_ready:
            top_chunks = self.search_index(question)
            if not top_chunks:
                context_parts = ["（索引中未找到相關內容，請嘗試換不同詞語提問）"]
            else:
                groups: dict[str, list[Chunk]] = {}
                for c in top_chunks:
                    groups.setdefault(c.source, []).append(c)
                for fname, chunks in groups.items():
                    sections = [
                        f"{c.page_tag}\n{c.text}" if c.page_tag else c.text
                        for c in chunks
                    ]
                    context_parts.append(
                        f"【{fname}】\n" + "\n…\n".join(sections)
                    )
                    source_files.append(fname)
        else:
            context_parts = ["（索引尚未建立，請稍候或點擊「重新整理」）"]

        context = ("\n\n" + "─" * 40 + "\n\n").join(context_parts)

        system_msg = (
            "你是一位專業的學校事務助手，根據學校官方 PDF 文件回答問題。\n"
            "規則：\n"
            "• 只根據所提供的文件內容回答，不可憑空猜測。\n"
            "• 使用繁體中文，語氣親切、專業。\n"
            "• 若文件中找不到答案，請如實說明，並建議聯絡學校查詢。\n"
            "• 回答需條理清晰，適當使用列表或分段。\n"
            "• 如有引用，請說明出自哪個文件及頁數。"
        )

        messages: list[dict] = [{"role": "system", "content": system_msg}]
        if chat_history:
            for msg in chat_history[-6:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({
            "role":    "user",
            "content": f"問題：{question}\n\n相關文件內容：\n{context}",
        })

        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=2000, temperature=0.7
            )
            return resp.choices[0].message.content, source_files
        except Exception as e:
            raise ChatbotError(f"AI 回答失敗：{e}")
