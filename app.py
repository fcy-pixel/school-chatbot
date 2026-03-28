"""
School Affairs Chatbot — Streamlit UI
"""

import os
import streamlit as st
from chatbot import SchoolChatbot, ChatbotError

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
    st.caption("School Affairs PDF Chatbot · Powered by Qwen")
    st.divider()

    qwen_api_key = _secret("QWEN_API_KEY")
    qwen_model   = "qwen-plus"
    github_repo  = _secret("GITHUB_REPO")
    github_path  = _secret("GITHUB_PATH")
    github_token = _secret("GITHUB_TOKEN")

    st.divider()
    st.subheader("📤 上載 PDF 文件")
    uploaded_files = st.file_uploader(
        "直接拖放 PDF（可多選）",
        type=["pdf"],
        accept_multiple_files=True,
        help="上載後加入索引即可提問。開啟「儲存到 GitHub」則永久保存。",
        key="pdf_uploader",
    )
    if uploaded_files:
        upload_btn = st.button(
            f"➕ 加入索引（{len(uploaded_files)} 個檔案）",
            use_container_width=True,
        )
    else:
        upload_btn = False

    # Show already-indexed uploaded files
    if st.session_state.get("uploaded_pdf_names"):
        st.caption(f"📁 已上載 {len(st.session_state.uploaded_pdf_names)} 個檔案：")
        for n in st.session_state.uploaded_pdf_names:
            st.caption(f"   📄 {n}")

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        load_btn = st.button("🔄 重新整理", use_container_width=True,
                         help="重新取得 PDF 列表")
    with col2:
        clear_btn = st.button("🗑️ 清除對話", use_container_width=True)

    # Show index status
    if st.session_state.get("chunk_count"):
        st.success(f"✅ 索引完成：{st.session_state.chunk_count} 個片段")
    # Show loaded PDF list
    if "pdf_list" in st.session_state and st.session_state.pdf_list:
        st.divider()
        pdfs = st.session_state.pdf_list
        st.caption(f"**已載入 {len(pdfs)} 個 PDF 文件**")
        for pdf in pdfs:
            kb = pdf["size"] // 1024 if pdf["size"] >= 1024 else "< 1"
            st.markdown(f"📄 `{pdf['name']}`  *({kb} KB)*")


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
        st.session_state.pop("pdf_list", None)
    return st.session_state.chatbot


# ── Auto-init: build full-text chunk index on first visit ───────────────────────
if qwen_api_key and github_repo and not st.session_state.get("init_attempted"):
    st.session_state.init_attempted = True
    _placeholder = st.empty()
    with _placeholder.container():
        _prog = st.progress(0, text="⏳ 正在下載並建立全文索引，請稍候…")
    try:
        _bot  = get_chatbot()
        _pdfs = _bot.get_pdf_list(force_refresh=True)
        st.session_state.pdf_list = _pdfs
        _total = len(_pdfs)

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
            pdfs = bot.get_pdf_list(force_refresh=True)
            st.session_state.pdf_list       = pdfs
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
        progress = st.sidebar.progress(0, text="正在處理上傳文件…")

        for i, uf in enumerate(uploaded_files):
            progress.progress(
                int(i / len(uploaded_files) * 100),
                text=f"正在讀取 {uf.name}…",
            )
            pdf_bytes = uf.getvalue()
            bot.ingest_uploaded_pdf(uf.name, pdf_bytes)
            names.append(uf.name)
            # Silently push to GitHub for permanent storage
            if github_repo and github_token:
                try:
                    bot.push_pdf_to_github(uf.name, pdf_bytes)
                except ChatbotError:
                    pass

        progress.empty()

        prev = st.session_state.get("uploaded_pdf_names", [])
        st.session_state.uploaded_pdf_names = list(dict.fromkeys(prev + names))

        # Refresh PDF list if files were pushed to GitHub
        if github_repo and github_token:
            try:
                st.session_state.pdf_list = bot.get_pdf_list(force_refresh=True)
            except Exception:
                pass

        st.sidebar.success(f"✅ 已加入 {len(names)} 個文件")
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
    has_uploads = bool(st.session_state.get("uploaded_pdf_names"))
    if not github_repo and not has_uploads:
        st.error("⚠️ 請上傳 PDF 文件，或填寫 GitHub 倉庫")
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
