"""
School Affairs Chatbot — Core Logic
- Fetches PDF list from a GitHub repository
- Extracts text from PDFs
- Uses Qwen (Alibaba Cloud International) to pick relevant files and answer questions
"""

import io
import re
import json
import requests
from typing import Optional
from openai import OpenAI
import pypdf

# ── Constants ──────────────────────────────────────────────────────────────────
QWEN_BASE_URL   = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL   = "qwen-plus"
CHUNK_SIZE      = 1500   # characters per chunk
CHUNK_OVERLAP   = 200    # overlap between chunks
MAX_CHUNKS      = 3      # top-N chunks per PDF sent to AI
MAX_CTX_PER_PDF = 4500   # hard cap on characters per PDF in the prompt


class ChatbotError(Exception):
    pass


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
        if not github_repo:
            raise ChatbotError("缺少 GitHub 倉庫名稱")

        self.client = OpenAI(api_key=qwen_api_key, base_url=QWEN_BASE_URL)
        self.github_repo  = github_repo.strip("/")
        self.github_path  = github_path.strip("/")
        self.github_token = github_token
        self.model        = model

        self._pdf_text_cache: dict[str, str] = {}
        self._pdf_list_cache: Optional[list[dict]] = None

    # ── GitHub helpers ─────────────────────────────────────────────────────────

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
        """Download a PDF and return its extracted text. Results are cached."""
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

    # ── Relevance scoring ──────────────────────────────────────────────────────

    def _rank_chunks(self, query: str, text: str) -> list[tuple[str, float]]:
        """Split text into overlapping chunks and rank by query relevance."""
        # CJK character overlap + Latin word overlap
        q_cjk   = {c for c in query if "\u4e00" <= c <= "\u9fff"}
        q_latin = set(re.findall(r"[a-zA-Z0-9]+", query.lower()))

        chunks, start = [], 0
        while start < len(text):
            end = min(start + CHUNK_SIZE, len(text))
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start += CHUNK_SIZE - CHUNK_OVERLAP

        scored = []
        for chunk in chunks:
            c_cjk   = {c for c in chunk if "\u4e00" <= c <= "\u9fff"}
            c_latin = set(re.findall(r"[a-zA-Z0-9]+", chunk.lower()))
            cjk_s   = len(q_cjk   & c_cjk)   / max(len(q_cjk),   1)
            lat_s   = len(q_latin & c_latin) / max(len(q_latin), 1)
            scored.append((chunk, max(cjk_s, lat_s)))

        return sorted(scored, key=lambda x: x[1], reverse=True)

    # ── AI helpers ─────────────────────────────────────────────────────────────

    def find_relevant_pdfs(self, question: str, pdf_list: list[dict]) -> list[dict]:
        """Ask Qwen which PDFs are most relevant; fall back to keyword matching."""
        if not pdf_list:
            return []
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
            pass  # fall through to keyword fallback

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
        return top if top else [pdf_list[0]]

    # ── Public API ─────────────────────────────────────────────────────────────

    def chat(
        self,
        question: str,
        chat_history: Optional[list[dict]] = None,
    ) -> tuple[str, list[str]]:
        """
        Answer a question using relevant PDFs as grounding context.
        Returns (answer_text, [source_filenames]).
        """
        pdf_list = self.get_pdf_list()
        if not pdf_list:
            return "目前倉庫中沒有 PDF 文件，請先上傳相關文件到 GitHub。", []

        # Step 1: identify relevant PDFs
        relevant = self.find_relevant_pdfs(question, pdf_list)

        # Step 2: extract and chunk text
        context_parts: list[str] = []
        source_files:  list[str] = []

        for pdf in relevant:
            text = self.extract_pdf_text(pdf)
            if text.startswith("[無法讀取") or text.startswith("[") and "無法" in text:
                continue
            top_chunks = [c for c, _ in self._rank_chunks(question, text)[:MAX_CHUNKS]]
            snippet    = "\n…\n".join(top_chunks)[:MAX_CTX_PER_PDF]
            context_parts.append(f"【{pdf['name']}】\n{snippet}")
            source_files.append(pdf["name"])

        if not context_parts:
            context_parts = ["（相關文件未能提取文字，可能為掃描圖片版）"]

        context = ("\n\n" + "─" * 40 + "\n\n").join(context_parts)

        # Step 3: build prompt
        system_msg = (
            "你是一位專業的學校事務助手，根據學校官方 PDF 文件回答問題。\n"
            "規則：\n"
            "• 只根據所提供的文件內容回答，不可憑空猜測。\n"
            "• 使用繁體中文，語氣親切、專業。\n"
            "• 若文件中找不到答案，請如實說明，並建議聯絡學校查詢。\n"
            "• 回答需條理清晰，適當使用列表或分段。"
        )

        messages: list[dict] = [{"role": "system", "content": system_msg}]

        # Include recent conversation turns for context
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
