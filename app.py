"""
School Affairs Chatbot — Streamlit UI
"""

import os
import streamlit as st
from chatbot import SchoolChatbot, ChatbotError
from pdf_converter import PdfConversionError, convert_pdf_bytes_to_docx_bytes

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _secret(key: str) -> str:
    """Read from st.secrets (Streamlit Cloud) first, then os.environ (.env locally)."""
    try:
        return st.secrets.get(key, os.getenv(key, ""))
    except Exception:
        return os.getenv(key, "")


def _to_docx_name(filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        return filename[:-4] + ".docx"
    return filename

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="學校事務助手",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🏫 學校事務助手")
    st.caption("School Affairs Word Chatbot · Powered by Qwen")
    st.divider()

    qwen_api_key = _secret("QWEN_API_KEY")
    qwen_model   = "qwen-plus"
    github_repo  = _secret("GITHUB_REPO")
    github_path  = _secret("GITHUB_PATH")
    github_token = _secret("GITHUB_TOKEN")

    st.divider()
    with st.expander("🔐 管理員上載文件", expanded=False):
        admin_input = st.text_input("密碼", type="password", key="admin_pw")
        admin_ok    = (admin_input == "ktps")
        if admin_input and not admin_ok:
            st.error("密碼錯誤")
        if admin_ok:
            st.success("管理員已登入 ✅")
            uploaded_files = st.file_uploader(
                "直接拖放 PDF/Word 文件（可多選）",
                type=["pdf", "docx"],
                accept_multiple_files=True,
                help="PDF 會先自動轉成 DOCX，再加入索引。",
                key="doc_uploader",
            )
            if uploaded_files:
                upload_btn = st.button(
                    f"➕ 加入索引（{len(uploaded_files)} 個檔案）",
                    use_container_width=True,
                )
            else:
                upload_btn = False

            # ── Delete management ──────────────────────────────────────────
            _gh_docs   = st.session_state.get("doc_list") or []
            _up_names  = st.session_state.get("uploaded_doc_names") or []
            _gh_names  = {d["name"] for d in _gh_docs}
            _all_names = list(_gh_names) + [n for n in _up_names if n not in _gh_names]
            if _all_names:
                st.divider()
                st.caption("📋 管理現有文件")
                for _dn in _all_names:
                    _c1, _c2 = st.columns([5, 1])
                    _c1.caption(f"📄 {_dn}")
                    if _c2.button("🗑️", key=f"del_{_dn}", help=f"刪除 {_dn}"):
                        st.session_state.pending_delete = _dn
        else:
            uploaded_files = []
            upload_btn     = False

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        load_btn = st.button("🔄 重新整理", use_container_width=True,
                         help="重新取得文件列表並重建索引")
    with col2:
        clear_btn = st.button("🗑️ 清除對話", use_container_width=True)

    # Show index status
    if st.session_state.get("chunk_count"):
        st.success(f"✅ 索引完成：{st.session_state.chunk_count} 個片段")
    # Show loaded doc list
    if "doc_list" in st.session_state and st.session_state.doc_list:
        st.divider()
        docs = st.session_state.doc_list
        st.caption(f"**已載入 {len(docs)} 個 Word 文件**")
        for doc in docs:
            kb = doc["size"] // 1024 if doc["size"] >= 1024 else "< 1"
            st.markdown(f"📝 `{doc['name']}`  *({kb} KB)*")


# ── Session state init ─────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []


def get_chatbot() -> SchoolChatbot:
    """Return (and cache) a SchoolChatbot instance for the current config."""
    cfg_key = f"{qwen_api_key}|{qwen_model}|{github_repo}|{github_path}|{github_token}"
    if (
        "chatbot" not in st.session_state
        or st.session_state.get("chatbot_cfg_key") != cfg_key
    ):
        st.session_state.chatbot = SchoolChatbot(
            qwen_api_key=qwen_api_key,
            github_repo=github_repo or "",
            github_path=github_path,
            github_token=github_token or None,
            model=qwen_model,
        )
        st.session_state.chatbot_cfg_key = cfg_key
        st.session_state.pop("doc_list", None)
    return st.session_state.chatbot


# ── Auto-init: build full-text chunk index on first visit ───────────────────────
if qwen_api_key and github_repo and not st.session_state.get("init_attempted"):
    st.session_state.init_attempted = True
    _placeholder = st.empty()
    with _placeholder.container():
        _prog = st.progress(0, text="⏳ 正在下載並建立全文索引，請稍候…")
    try:
        _bot  = get_chatbot()
        _docs = _bot.get_doc_list(force_refresh=True)
        st.session_state.doc_list = _docs
        _total = len(_docs)

        def _cb(done, total, name):
            pct = int(done / total * 100) if total else 100
            _prog.progress(pct, text=f"📥 正在索引 {done}/{total}：{name}")

        if _total > 0:
            n_chunks = _bot.build_index(progress_callback=_cb)
            st.session_state.chunk_count = n_chunks
    except ChatbotError as _e:
        _placeholder.error(f"⚠️ 初始化失敗：{_e}")
    else:
        _placeholder.empty()
    st.rerun()

# ── Button handlers ────────────────────────────────────────────────────────────
if load_btn:
    if not qwen_api_key or not github_repo:
        st.sidebar.error("⚠️ 設定未完成，請聯絡管理員")
    else:
        try:
            bot  = get_chatbot()
            docs = bot.get_doc_list(force_refresh=True)
            st.session_state.doc_list       = docs
            st.session_state.init_attempted = True
            with st.sidebar:
                _rp = st.progress(0, text="🔄 正在重新建立索引…")

            def _rcb(done, total, name):
                pct = int(done / total * 100) if total else 100
                _rp.progress(pct, text=f"📥 {done}/{total}：{name}")

            n_chunks = bot.build_index(progress_callback=_rcb)
            st.session_state.chunk_count = n_chunks
            _rp.empty()
        except ChatbotError as e:
            st.sidebar.error(str(e))
        st.rerun()

if upload_btn and uploaded_files:
    if not qwen_api_key:
        st.sidebar.error("⚠️ 請先填寫 Qwen API Key")
    else:
        bot = get_chatbot()
        names: list[str] = []
        failed: list[tuple[str, str]] = []
        progress = st.sidebar.progress(0, text="正在處理上傳文件…")

        for i, uf in enumerate(uploaded_files, start=1):
            progress.progress(
                int((i - 1) / len(uploaded_files) * 100),
                text=f"正在處理 {uf.name}…",
            )
            file_name = uf.name
            raw_bytes = uf.getvalue()

            try:
                if file_name.lower().endswith(".pdf"):
                    progress.progress(
                        int((i - 0.5) / len(uploaded_files) * 100),
                        text=f"正在轉換 PDF：{file_name}…",
                    )
                    doc_name = _to_docx_name(file_name)
                    doc_bytes = convert_pdf_bytes_to_docx_bytes(raw_bytes, file_name)
                else:
                    doc_name = _to_docx_name(file_name)
                    doc_bytes = raw_bytes

                bot.ingest_uploaded_doc(doc_name, doc_bytes)
                names.append(doc_name)

                # Silently push DOCX to GitHub for permanent storage
                if github_repo and github_token:
                    try:
                        bot.push_doc_to_github(doc_name, doc_bytes)
                    except ChatbotError:
                        pass
            except PdfConversionError as e:
                failed.append((file_name, str(e)))
            except Exception as e:
                failed.append((file_name, f"處理失敗：{e}"))

        progress.empty()

        prev = st.session_state.get("uploaded_doc_names", [])
        st.session_state.uploaded_doc_names = list(dict.fromkeys(prev + names))

        # Refresh doc list if files were pushed to GitHub
        if github_repo and github_token:
            try:
                st.session_state.doc_list = bot.get_doc_list(force_refresh=True)
            except Exception:
                pass

        if names:
            st.sidebar.success(f"✅ 已上傳並建立索引：{len(names)} 個 DOCX 文件")
        if failed:
            st.sidebar.warning(f"⚠️ 有 {len(failed)} 個檔案處理失敗")
            for file_name, reason in failed:
                st.sidebar.caption(f"• {file_name}：{reason}")
        st.rerun()

if st.session_state.get("pending_delete"):
    _del_name = st.session_state.pop("pending_delete")
    try:
        _del_bot = get_chatbot()
        _del_bot.delete_doc(_del_name)
        # Remove from session-state lists
        if "uploaded_doc_names" in st.session_state:
            st.session_state.uploaded_doc_names = [
                n for n in st.session_state.uploaded_doc_names if n != _del_name
            ]
        if "doc_list" in st.session_state and st.session_state.doc_list:
            st.session_state.doc_list = [
                d for d in st.session_state.doc_list if d["name"] != _del_name
            ]
        # Recount chunks
        st.session_state.chunk_count = len(_del_bot._chunk_index)
        st.sidebar.success(f"✅ 已刪除：{_del_name}")
    except ChatbotError as _del_err:
        st.sidebar.error(f"刪除失敗：{_del_err}")
    st.rerun()

if clear_btn:
    st.session_state.messages = []
    st.rerun()


# ── Main chat area ─────────────────────────────────────────────────────────────
st.title("🏫 學校事務助手")

if not st.session_state.messages:
    if st.session_state.get("init_attempted"):
        st.info(
            "👋 **歡迎使用學校事務助手！**\n\n"
            "直接在下方輸入問題，系統會搜尋全文索引精準找出相關內容來回答。"
        )
        st.caption(
            "**常見問題示例：** 學校假期時間表？ / 如何申請請假？ / "
            "制服規定是什麼？ / 考試時間表？ / 學費繳交日期？"
        )
    else:
        st.info("⏳ 系統正在載入文件列表，請稍候…")

# Render chat history
for msg in st.session_state.messages:
    avatar = "🏫" if msg["role"] == "assistant" else "👤"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander(f"📄 資料來源（{len(msg['sources'])} 個文件）", expanded=False):
                for s in msg["sources"]:
                    st.caption(f"• {s}")

# Chat input
if prompt := st.chat_input("請輸入您的問題…"):
    if not qwen_api_key:
        st.error("⚠️ 請先在左側填寫 Qwen API Key")
        st.stop()
    has_uploads = bool(st.session_state.get("uploaded_doc_names"))
    if not github_repo and not has_uploads:
        st.error("⚠️ 請管理員上傳 Word 文件，或填寫 GitHub 倉庫")
        st.stop()

    # Append & display user message immediately
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)

    # Generate assistant response
    with st.chat_message("assistant", avatar="🏫"):
        status = st.status("正在查找相關文件並生成回答…", expanded=True)
        try:
            bot = get_chatbot()

            history = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages[:-1]
            ]

            status.write("🔍 搜尋全文索引，找出最相關片段…")
            answer, sources = bot.chat(prompt, history)

            status.update(label="✅ 回答完成", state="complete", expanded=False)

            st.markdown(answer)
            if sources:
                with st.expander(f"📄 資料來源（{len(sources)} 個文件）", expanded=False):
                    for s in sources:
                        st.caption(f"• {s}")

            st.session_state.messages.append({
                "role":    "assistant",
                "content": answer,
                "sources": sources,
            })

        except ChatbotError as e:
            status.update(label="❌ 發生錯誤", state="error", expanded=False)
            err_msg = f"❌ **錯誤：** {e}"
            st.error(err_msg)
            st.session_state.messages.append({
                "role":    "assistant",
                "content": err_msg,
                "sources": [],
            })
        except Exception as e:
            status.update(label="❌ 未知錯誤", state="error", expanded=False)
            err_msg = f"❌ **未知錯誤：** {e}"
            st.error(err_msg)
            st.session_state.messages.append({
                "role":    "assistant",
                "content": err_msg,
                "sources": [],
            })
