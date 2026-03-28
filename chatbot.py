"""
School Affairs Chatbot — Core Logic
- Fetches PDF list from a GitHub repository
- Extracts text from PDFs
- Builds an in-memory full-text chunk index so queries work even when
  filenames don't reflect content (e.g. monthly meeting minutes that
  contain many different topics)
- Uses Qwen (Alibaba Cloud International) to answer questions
"""

import io
import re
import json
import requests
from typing import Optional, Generator
from openai import OpenAI
import pypdf

# ── Constants ──────────────────────────────────────────────────────────────────
QWEN_BASE_URL   = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL   = "qwen-plus"
CHUNK_SIZE      = 800    # characters per chunk (smaller = more precise retrieval)
CHUNK_OVERLAP   = 150    # overlap between chunks
TOP_K_CHUNKS    = 6      # top-N chunks across ALL files sent to AI
MAX_CTX_CHARS   = 6000   # hard cap on total context characters in the prompt


class ChatbotError(Exception):
    pass


# A single indexed chunk
class Chunk:
    __slots__ = ("text", "source", "page_hint", "tokens_cjk", "tokens_latin")

    def __init__(self, text: str, source: str, page_hint: str):
        self.text        = text
        self.source      = source      # PDF filename
        self.page_hint   = page_hint   # e.g. "[第 3 頁]" prefix, may be empty
        # Pre-tokenise for fast scoring
        self.tokens_cjk   = {c for c in text if "\u4e00" <= c <= "\u9fff"}
        self.tokens_latin = set(re.findall(r"[a-zA-Z0-9]+", text.lower()))


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

        self.client = OpenAI(api_key=qwen_api_key, base_url=QWEN_BASE_URL)
        self.github_repo  = github_repo.strip("/") if github_repo else ""
        self.github_path  = github_path.strip("/") if github_path else ""
        self.github_token = github_token
        self.model        = model

        self._pdf_text_cache: dict[str, str] = {}
        self._pdf_list_cache: Optional[list[dict]] = None
        # Full-text chunk index: built by build_index() or ingest_uploaded_pdf()
        self._chunk_index: list[Chunk]  = []
        self._indexed_pdfs: set[str]    = set()   # filenames indexed from GitHub
        self._uploaded_pdfs: set[str]   = set()   # filenames added via upload

    # ── GitHub helpers ─────────────────────────────────────────────────────────

    @property
    def index_ready(self) -> bool:
        return len(self._chunk_index) > 0

    @property
    def has_content(self) -> bool:
        """True if there is any content available (GitHub PDFs or uploads)."""
        return bool(self._pdf_list_cache or self._uploaded_pdfs)

    def _gh_headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            h["Authorization"] = f"token {self.github_token}"
        return h

    def get_pdf_list(self, force_refresh: bool = False) -> list[dict]:
        """Return list of PDF dicts from the configured GitHub path."""
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

        pdfs = [
            {
                "name":         item["name"],
                "download_url": item["download_url"],
                "path":         item["path"],
                "size":         item.get("size", 0),
                "html_url":     item.get("html_url", ""),
            }
            for item in items
            if item.get("type") == "file" and item["name"].lower().endswith(".pdf")
        ]

        self._pdf_list_cache = pdfs
        return pdfs

    # ── PDF text extraction ────────────────────────────────────────────────────

    def extract_pdf_text(self, pdf: dict) -> str:
        """Download a PDF and return its extracted text (with page headers). Cached."""
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

    # ── Full-text chunk index ──────────────────────────────────────────────────

    def _make_chunks(self, text: str, source: str) -> list[Chunk]:
        """Split text into overlapping Chunk objects."""
        # Detect page-header lines like "[第 N 頁]"
        page_pattern = re.compile(r"\[第\s*\d+\s*頁\]")
        current_page = ""
        chunks: list[Chunk] = []
        start = 0

        while start < len(text):
            end   = min(start + CHUNK_SIZE, len(text))
            piece = text[start:end]

            # Track the latest page header seen before this chunk
            for m in page_pattern.finditer(text, 0, end):
                current_page = m.group()

            chunks.append(Chunk(piece, source, current_page))
            if end >= len(text):
                break
            start += CHUNK_SIZE - CHUNK_OVERLAP

        return chunks

    def ingest_uploaded_pdf(self, name: str, data: bytes) -> int:
        """
        Add a user-uploaded PDF (raw bytes) to the text cache and chunk index.
        Can be called without GitHub being configured at all.
        Returns the number of new chunks added.
        """
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

        # Remove old chunks for this file if re-uploaded
        self._chunk_index = [c for c in self._chunk_index if c.source != name]
        self._pdf_text_cache[name] = text

        is_unreadable = "無法提取文字" in text or "無法讀取" in text
        new_chunks: list[Chunk] = []
        if not is_unreadable:
            new_chunks = self._make_chunks(text, name)
            self._chunk_index.extend(new_chunks)
            self._uploaded_pdfs.add(name)

        return len(new_chunks)

    def build_index(
        self,
        progress_callback=None,
    ) -> int:
        """
        (Re-)build the full-text chunk index from all PDFs in the GitHub repo.

        progress_callback(current, total, filename) is called for each PDF
        if provided — useful for driving a Streamlit progress bar.

        Returns the total number of chunks indexed.
        """
        self._chunk_index.clear()
        self._indexed_pdfs.clear()

        pdfs  = self.get_pdf_list()
        total = len(pdfs)

        for i, pdf in enumerate(pdfs):
            if progress_callback:
                progress_callback(i, total, pdf["name"])

            text = self.extract_pdf_text(pdf)
            is_unreadable = (
                text.startswith("[無法讀取")
                or ("無法提取文字" in text)
            )
            if not is_unreadable:
                self._chunk_index.extend(self._make_chunks(text, pdf["name"]))
                self._indexed_pdfs.add(pdf["name"])

        if progress_callback:
            progress_callback(total, total, "完成")

        return len(self._chunk_index)

    def _score_chunk(self, chunk: Chunk, q_cjk: set, q_latin: set) -> float:
        """Return a relevance score [0, 1] for a chunk against a query."""
        if q_cjk:
            cjk_score = len(q_cjk & chunk.tokens_cjk) / len(q_cjk)
        else:
            cjk_score = 0.0
        if q_latin:
            lat_score = len(q_latin & chunk.tokens_latin) / len(q_latin)
        else:
            lat_score = 0.0
        return max(cjk_score, lat_score)

    def search_index(self, query: str, top_k: int = TOP_K_CHUNKS) -> list[Chunk]:
        """
        Search the chunk index for the most relevant chunks.
        Returns up to top_k chunks sorted by relevance score (best first).
        """
        q_cjk   = {c for c in query if "\u4e00" <= c <= "\u9fff"}
        q_latin = set(re.findall(r"[a-zA-Z0-9]+", query.lower()))

        scored = [
            (chunk, self._score_chunk(chunk, q_cjk, q_latin))
            for chunk in self._chunk_index
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Deduplicate: keep at most 2 chunks per source file so a single
        # large PDF does not crowd out all others.
        seen_sources: dict[str, int] = {}
        results: list[Chunk] = []
        for chunk, score in scored:
            if score <= 0:
                break
            count = seen_sources.get(chunk.source, 0)
            if count < 2:
                results.append(chunk)
                seen_sources[chunk.source] = count + 1
            if len(results) >= top_k:
                break

        return results

    # ── Fallback: filename-based search (used when index not built) ────────────

    def _find_pdfs_by_filename(self, question: str, pdf_list: list[dict]) -> list[dict]:
        """
        Ask Qwen which PDFs are relevant based on filenames only.
        Falls back to keyword overlap on filenames if AI call fails.
        Used only when the full-text index has not been built yet.
        """
        if len(pdf_list) == 1:
            return pdf_list

        names_str = "\n".join(f"- {p['name']}" for p in pdf_list)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是學校文件分析助手。根據用戶問題，從以下PDF文件列表中選出"
                            "最相關的文件（最多3個）。"
                            "只返回 JSON 格式，不要其他文字：{\"files\": [\"filename.pdf\", ...]}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"問題：{question}\n\n可用文件：\n{names_str}",
                    },
                ],
                max_tokens=300,
                temperature=0.1,
            )
            content = resp.choices[0].message.content.strip()
            match = re.search(r'\{[^{}]*"files"[^{}]*\}', content, re.DOTALL)
            if match:
                data     = json.loads(match.group())
                selected = data.get("files", [])
                relevant = [p for p in pdf_list if p["name"] in selected]
                if relevant:
                    return relevant
        except Exception:
            pass

        # Keyword fallback on filenames
        q_cjk   = {c for c in question if "\u4e00" <= c <= "\u9fff"}
        q_latin = set(re.findall(r"[a-zA-Z0-9]+", question.lower()))
        scored  = []
        for p in pdf_list:
            n_cjk   = {c for c in p["name"] if "\u4e00" <= c <= "\u9fff"}
            n_latin = set(re.findall(r"[a-zA-Z0-9]+", p["name"].lower()))
            score   = len(q_cjk & n_cjk) + len(q_latin & n_latin)
            scored.append((p, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = [p for p, s in scored[:3] if s > 0]
        return top if top else pdf_list[:3]

    # ── Public API ─────────────────────────────────────────────────────────────

    def chat(
        self,
        question: str,
        chat_history: Optional[list[dict]] = None,
    ) -> tuple[str, list[str]]:
        """
        Answer a question using relevant PDF content as grounding context.

        Strategy:
          • If the full-text index has been built → search_index() to find the
            best chunks across ALL PDFs regardless of filename.
          • Otherwise → fallback to filename-based PDF selection, then chunk
            just the selected PDFs on the fly.

        Returns (answer_text, [source_filenames]).
        """
        pdf_list = self.get_pdf_list() if self.github_repo else []
        if not pdf_list and not self._uploaded_pdfs:
            return "目前沒有任何 PDF 文件可供查閱，請上傳文件或設定 GitHub 倉庫。", []

        context_parts: list[str] = []
        source_files:  list[str] = []

        # ── Path A: full-text index ────────────────────────────────────────────
        if self.index_ready:
            top_chunks = self.search_index(question, top_k=TOP_K_CHUNKS)

            if not top_chunks:
                context_parts = ["（索引中未找到相關內容，請確認文件已正確載入）"]
            else:
                # Group chunks by source for a readable prompt layout
                groups: dict[str, list[Chunk]] = {}
                for chunk in top_chunks:
                    groups.setdefault(chunk.source, []).append(chunk)

                total_chars = 0
                for fname, chunks in groups.items():
                    if total_chars >= MAX_CTX_CHARS:
                        break
                    lines = []
                    for ch in chunks:
                        prefix = f"{ch.page_hint} " if ch.page_hint else ""
                        lines.append(prefix + ch.text)
                        total_chars += len(ch.text)
                        if total_chars >= MAX_CTX_CHARS:
                            break
                    context_parts.append(f"【{fname}】\n" + "\n…\n".join(lines))
                    source_files.append(fname)

        # ── Path B: no index — on-the-fly filename search ──────────────────────
        else:
            # Combine GitHub PDFs + uploaded PDFs into a unified search list
            combined_names = list({c.source for c in self._chunk_index})  # uploaded
            if pdf_list:
                combined_names += [p["name"] for p in pdf_list if p["name"] not in combined_names]

            # For on-the-fly ranking we need text; uploaded files are already cached
            candidates = []
            for name in combined_names:
                if name in self._pdf_text_cache:
                    candidates.append({"name": name, "_cached": True})
                else:
                    match = next((p for p in pdf_list if p["name"] == name), None)
                    if match:
                        candidates.append(match)

            relevant = self._find_pdfs_by_filename(question, candidates) if candidates else []
            for pdf in relevant:
                name = pdf["name"]
                # Use cached text (uploaded) or download from GitHub
                if pdf.get("_cached") or name in self._pdf_text_cache:
                    text = self._pdf_text_cache.get(name, "")
                else:
                    text = self.extract_pdf_text(pdf)
                is_unreadable = (
                    text.startswith("[無法讀取")
                    or "無法提取文字" in text
                )
                if is_unreadable:
                    continue

                # Quick chunk + rank on the fly (old behaviour)
                q_cjk   = {c for c in question if "\u4e00" <= c <= "\u9fff"}
                q_latin = set(re.findall(r"[a-zA-Z0-9]+", question.lower()))
                ch_list = self._make_chunks(text, pdf["name"])
                scored  = sorted(
                    ch_list,
                    key=lambda c: self._score_chunk(c, q_cjk, q_latin),
                    reverse=True,
                )
                top3    = scored[:3]
                snippet = "\n…\n".join(c.text for c in top3)[:4500]
                context_parts.append(f"【{pdf['name']}】\n{snippet}")
                source_files.append(pdf["name"])

            if not context_parts:
                context_parts = ["（相關文件未能提取文字，可能為掃描圖片版）"]

        context = ("\n\n" + "─" * 40 + "\n\n").join(context_parts)

        # ── Build prompt ───────────────────────────────────────────────────────
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
            resp   = self.client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=2000, temperature=0.7
            )
            answer = resp.choices[0].message.content
            return answer, source_files
        except Exception as e:
            raise ChatbotError(f"AI 回答失敗：{e}")
