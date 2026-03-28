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

    with st.expander("🔑 Qwen API 設定", expanded=not bool(_secret("QWEN_API_KEY"))):
        qwen_api_key = st.text_input(
            "Qwen API Key *",
            value=_secret("QWEN_API_KEY"),
            type="password",
            placeholder="sk-...",
            help="通義千問國際版 API Key（dashscope-intl）",
        )

        qwen_model = st.selectbox(
            "模型",
            ["qwen-plus", "qwen-turbo", "qwen-max"],
            index=0,
            help="qwen-plus 平衡速度與效果；qwen-max 效果最佳；qwen-turbo 最快",
        )

    with st.expander("📁 GitHub PDF 倉庫設定", expanded=not bool(_secret("GITHUB_REPO"))):
        github_repo = st.text_input(
            "GitHub 倉庫 *",
            value=_secret("GITHUB_REPO"),
            placeholder="username/repository",
            help="存放學校 PDF 文件的公開 GitHub 倉庫",
        )
        github_path = st.text_input(
            "PDF 所在子目錄",
            value=_secret("GITHUB_PATH"),
            placeholder="pdfs/（留空表示根目錄）",
            help="PDF 文件在倉庫中的目錄路徑，留空代表根目錄",
        )
        github_token = st.text_input(
            "GitHub Token",
            value=_secret("GITHUB_TOKEN"),
            type="password",
            help="上載文件到 GitHub 倉庫需要填寫（需 repo 寫入權限）；私有倉庫也需要。",
        )

    st.divider()
    st.subheader("📤 上載 PDF 文件")
    save_to_github = st.toggle(
        "儲存到 GitHub（下次自動載入）",
        value=True,
        help="開啟後上載的 PDF 會自動儲存到 GitHub 倉庫。\n"
             "需要填寫 GitHub Token（repo 寫入權限）。",
    )
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
        load_btn = st.button("🔄 載入 PDF", use_container_width=True,
                             help="取得 GitHub 上的 PDF 文件列表")
    with col2:
        clear_btn = st.button("🗑️ 清除對話", use_container_width=True)

    index_btn = st.button(
        "📚 建立全文索引",
        use_container_width=True,
        disabled="pdf_list" not in st.session_state or not st.session_state.get("pdf_list"),
        help="讀取所有 PDF 內容並建立全文搜索索引。\n"
             "適合會議紀錄等檔名不反映內容的文件。",
    )

    # Index status badge
    if st.session_state.get("index_ready"):
        chunk_count = st.session_state.get("chunk_count", 0)
        st.success(f"✅ 全文索引已建立（{chunk_count} 個片段）", icon="📚")
    elif "pdf_list" in st.session_state and st.session_state.pdf_list:
        st.info("💡 建議點擊 **建立全文索引** 以提高搜索準確度", icon="ℹ️")

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


# ── Button handlers ────────────────────────────────────────────────────────────
if load_btn:
    if not qwen_api_key or not github_repo:
        st.sidebar.error("⚠️ 請填寫 Qwen API Key 和 GitHub 倉庫")
    else:
        with st.spinner("正在連接 GitHub，載入 PDF 列表…"):
            try:
                bot  = get_chatbot()
                pdfs = bot.get_pdf_list(force_refresh=True)
                st.session_state.pdf_list   = pdfs
                st.session_state.index_ready = False   # new PDF list → index stale
                if pdfs:
                    st.sidebar.success(f"✅ 成功載入 {len(pdfs)} 個 PDF")
                else:
                    st.sidebar.warning("⚠️ 該目錄下沒有找到 PDF 文件")
                st.rerun()
            except ChatbotError as e:
                st.sidebar.error(str(e))

if upload_btn and uploaded_files:
    if not qwen_api_key:
        st.sidebar.error("⚠️ 請先填寫 Qwen API Key")
    else:
        bot = get_chatbot()
        names: list[str] = []
        saved_to_gh: list[str] = []
        failed_gh:   list[str] = []
        progress = st.sidebar.progress(0, text="正在處理上傳文件…")
        total_chunks = 0

        for i, uf in enumerate(uploaded_files):
            progress.progress(
                int(i / len(uploaded_files) * 100),
                text=f"正在讀取 {uf.name}…",
            )
            pdf_bytes = uf.getvalue()
            n = bot.ingest_uploaded_pdf(uf.name, pdf_bytes)
            total_chunks += n
            names.append(uf.name)

            # Push to GitHub for permanent storage
            if save_to_github and github_repo and github_token:
                try:
                    bot.push_pdf_to_github(uf.name, pdf_bytes)
                    saved_to_gh.append(uf.name)
                except ChatbotError as gh_err:
                    failed_gh.append(f"{uf.name}：{gh_err}")

        progress.empty()

        prev = st.session_state.get("uploaded_pdf_names", [])
        st.session_state.uploaded_pdf_names = list(dict.fromkeys(prev + names))
        st.session_state.index_ready = True
        st.session_state.chunk_count = len(bot._chunk_index)

        msg = f"✅ 已加入 {len(names)} 個文件，新增 {total_chunks} 個片段"
        if saved_to_gh:
            msg += f"\n💾 已永久儲存到 GitHub：{', '.join(saved_to_gh)}"
        st.sidebar.success(msg)
        for fe in failed_gh:
            st.sidebar.warning(f"⚠️ GitHub 儲存失敗 — {fe}")

        # Auto-refresh PDF list if files were saved to GitHub
        if saved_to_gh and github_repo:
            try:
                pdfs = bot.get_pdf_list(force_refresh=True)
                st.session_state.pdf_list = pdfs
            except Exception:
                pass
        st.rerun()

if index_btn:
    if not qwen_api_key or not github_repo:
        st.sidebar.error("⚠️ 請先填寫 Qwen API Key 和 GitHub 倉庫")
    else:
        try:
            bot   = get_chatbot()
            pdfs  = st.session_state.pdf_list
            total = len(pdfs)

            progress_bar  = st.sidebar.progress(0, text="準備讀取 PDF…")
            status_text   = st.sidebar.empty()

            def on_progress(current, total, filename):
                if total == 0:
                    return
                pct = int(current / total * 100)
                progress_bar.progress(
                    pct,
                    text=f"正在讀取 {current}/{total}：{filename}",
                )

            chunk_count = bot.build_index(progress_callback=on_progress)
            progress_bar.empty()

            st.session_state.index_ready = True
            st.session_state.chunk_count = chunk_count
            st.sidebar.success(
                f"✅ 全文索引建立完成！共 {chunk_count} 個片段，"
                f"覆蓋 {len(bot._indexed_pdfs)} 個 PDF"
            )
            st.rerun()
        except ChatbotError as e:
            st.sidebar.error(str(e))

if clear_btn:
    st.session_state.messages = []
    st.rerun()


# ── Main chat area ─────────────────────────────────────────────────────────────
st.title("🏫 學校事務助手")

if not st.session_state.messages:
    st.info(
        "👋 **歡迎使用學校事務助手！**\n\n"
        "**方法 A — 上傳 PDF（最簡單）：**\n"
        "1. 在左側填寫 **Qwen API Key**\n"
        "2. 在 **上傳 PDF 文件** 拖入文件\n"
        "3. 點擊 **➕ 加入索引** 即可開始\n\n"
        "**方法 B — GitHub 倉庫：**\n"
        "1. 在左側填寫 **Qwen API Key** + **GitHub 倉庫**\n"
        "2. 點擊 **🔄 載入 PDF** → **📚 建立全文索引**\n"
        "3. 在下方輸入問題即可！"
    )
    st.caption(
        "**常見問題示例：** 學校假期時間表？/ 如何申請請假？/ "
        "制服規定是什麼？/ 考試時間表？/ 學費繳交日期？"
    )

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

            if bot.index_ready:
                status.write("📚 全文搜索索引中，尋找相關片段…")
            else:
                status.write("🔍 分析問題，篩選相關 PDF（建議點擊「建立全文索引」以提高準確度）…")
                status.write("📥 下載並讀取相關 PDF…")
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
