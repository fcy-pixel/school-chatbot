"""
School Affairs Chatbot — Core Logic
- Fetches PDF list from a GitHub repository
- Selects the most relevant PDFs per question (AI-based, by filename)
- Reads FULL text of selected PDFs — no chunking, maximum accuracy
- Uses Qwen (Alibaba Cloud International) to answer questions
"""

import io
import re
import json
import base64
import requests
from typing import Optional
from openai import OpenAI
import pypdf

# ── Constants ──────────────────────────────────────────────────────────────────
QWEN_BASE_URL    = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL    = "qwen-plus"
MAX_PDFS_TO_READ = 4        # read at most this many full PDFs per question
MAX_CTX_CHARS    = 80_000   # total character cap (~50 k tokens)


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

        self.client = OpenAI(api_key=qwen_api_key, base_url=QWEN_BASE_URL)
        self.github_repo  = github_repo.strip("/") if github_repo else ""
        self.github_path  = github_path.strip("/") if github_path else ""
        self.github_token = github_token
        self.model        = model

        self._pdf_text_cache: dict[str, str] = {}
        self._pdf_list_cache: Optional[list[dict]] = None
        self._uploaded_pdfs: set[str] = set()

    # ── GitHub helpers ─────────────────────────────────────────────────────────

    @property
    def has_content(self) -> bool:
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

    def push_pdf_to_github(self, filename: str, data: bytes) -> str:
        """
        Upload a PDF to the configured GitHub repo+path via the Contents API.
        Requires github_token with repo write access.
        Returns the HTML URL of the committed file.
        Raises ChatbotError on failure.
        """
        if not self.github_repo:
            raise ChatbotError("未設定 GitHub 倉庫，無法儲存文件")
        if not self.github_token:
            raise ChatbotError(
                "需要 GitHub Token（具備 repo 寫入權限）才能儲存文件到倉庫\n"
                "請在左側填寫 Personal Access Token"
            )

        file_path = f"{self.github_path}/{filename}" if self.github_path else filename
        api_url   = f"https://api.github.com/repos/{self.github_repo}/contents/{file_path}"
        headers   = {**self._gh_headers(), "Content-Type": "application/json"}

        # Check if the file already exists (need its SHA to update)
        sha: Optional[str] = None
        check = requests.get(api_url, headers=headers, timeout=10)
        if check.status_code == 200:
            sha = check.json().get("sha")
        elif check.status_code not in (404,):
            raise ChatbotError(f"GitHub API 錯誤 {check.status_code}：{check.text[:200]}")

        payload: dict = {
            "message": f"上傳 PDF：{filename}",
            "content": base64.b64encode(data).decode(),
        }
        if sha:
            payload["sha"] = sha  # required when updating an existing file

        resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            html_url = resp.json().get("content", {}).get("html_url", "")
            # Invalidate the cached PDF list so next load picks up the new file
            self._pdf_list_cache = None
            return html_url
        elif resp.status_code == 401:
            raise ChatbotError("GitHub Token 無效或已過期，請重新填寫")
        elif resp.status_code == 403:
            raise ChatbotError("GitHub Token 沒有倉庫寫入權限，需要 repo scope")
        else:
            raise ChatbotError(
                f"上傳失敗（{resp.status_code}）：{resp.json().get('message', resp.text[:200])}"
            )

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

    # ── PDF upload ─────────────────────────────────────────────────────────────

    def ingest_uploaded_pdf(self, name: str, data: bytes) -> None:
        """Add a user-uploaded PDF (raw bytes) to the text cache."""
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

    # ── PDF selection ──────────────────────────────────────────────────────────

    def _select_pdfs(self, question: str, pdf_list: list[dict]) -> list[dict]:
        """
        Ask Qwen which PDFs are most relevant to the question (by filename).
        Falls back to keyword overlap if AI call fails.
        Returns at most MAX_PDFS_TO_READ entries.
        """
        if len(pdf_list) <= MAX_PDFS_TO_READ:
            return pdf_list

        names_str = "\n".join(f"- {p['name']}" for p in pdf_list)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是學校文件分析助手。根據用戶問題，從以下 PDF 文件列表中選出"
                            f"最相關的文件（最多 {MAX_PDFS_TO_READ} 個）。"
                            '只返回 JSON 格式，不要其他文字：{"files": ["filename.pdf", ...]}'
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
                selected = json.loads(match.group()).get("files", [])
                relevant = [p for p in pdf_list if p["name"] in selected]
                if relevant:
                    return relevant[:MAX_PDFS_TO_READ]
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
        top = [p for p, s in scored[:MAX_PDFS_TO_READ] if s > 0]
        return top if top else pdf_list[:MAX_PDFS_TO_READ]

    # ── Public API ─────────────────────────────────────────────────────────────

    def chat(
        self,
        question: str,
        chat_history: Optional[list[dict]] = None,
    ) -> tuple[str, list[str]]:
        """
        Answer a question by reading the FULL text of the most relevant PDFs.
        No pre-built index — every query reads complete documents for max accuracy.
        """
        pdf_list = self.get_pdf_list() if self.github_repo else []

        # Build combined entry list: uploaded PDFs + GitHub PDFs
        all_entries: list[dict] = []
        for name in self._uploaded_pdfs:
            all_entries.append({"name": name, "_cached": True})
        for p in pdf_list:
            if p["name"] not in self._uploaded_pdfs:
                all_entries.append(p)

        if not all_entries:
            return "目前沒有任何 PDF 文件可供查閱，請上傳文件或設定 GitHub 倉庫。", []

        # Select the most relevant PDFs by filename
        relevant = self._select_pdfs(question, all_entries)

        context_parts: list[str] = []
        source_files:  list[str] = []
        total_chars = 0

        for pdf in relevant:
            if total_chars >= MAX_CTX_CHARS:
                break
            name = pdf["name"]
            if pdf.get("_cached") or name in self._pdf_text_cache:
                text = self._pdf_text_cache.get(name, "")
            else:
                text = self.extract_pdf_text(pdf)

            if "無法提取文字" in text or text.startswith("[無法讀取"):
                continue

            remaining = MAX_CTX_CHARS - total_chars
            trimmed   = text[:remaining]
            context_parts.append(f"【{name}】\n{trimmed}")
            source_files.append(name)
            total_chars += len(trimmed)

        if not context_parts:
            context_parts = ["（相關文件未能提取文字，可能為掃描圖片版）"]

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
            resp   = self.client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=2000, temperature=0.7
            )
            return resp.choices[0].message.content, source_files
        except Exception as e:
            raise ChatbotError(f"AI 回答失敗：{e}")
