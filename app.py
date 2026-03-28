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

    st.subheader("🔑 Qwen API 設定")
    qwen_api_key = st.text_input(
        "Qwen API Key *",
        value=os.getenv("QWEN_API_KEY", ""),
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

    st.divider()
    st.subheader("📁 GitHub PDF 倉庫設定")
    github_repo = st.text_input(
        "GitHub 倉庫 *",
        value=os.getenv("GITHUB_REPO", ""),
        placeholder="username/repository",
        help="存放學校 PDF 文件的公開 GitHub 倉庫",
    )
    github_path = st.text_input(
        "PDF 所在子目錄",
        value=os.getenv("GITHUB_PATH", ""),
        placeholder="pdfs/（留空表示根目錄）",
        help="PDF 文件在倉庫中的目錄路徑，留空代表根目錄",
    )
    github_token = st.text_input(
        "GitHub Token（私有倉庫才需要）",
        value=os.getenv("GITHUB_TOKEN", ""),
        type="password",
        help="私有倉庫請提供 Personal Access Token",
    )

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
            github_repo=github_repo,
            github_path=github_path,
            github_token=github_token or None,
            model=qwen_model,
        )
        st.session_state.chatbot_cfg_key = cfg_key
        # Clear PDF list cache on config change
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
        "**快速開始：**\n"
        "1. 在左側填寫 **Qwen API Key**\n"
        "2. 填寫存放 PDF 文件的 **GitHub 倉庫**（格式：`username/repo`）\n"
        "3. 點擊 **🔄 載入 PDF** 取得文件列表\n"
        "4. （**建議**）點擊 **📚 建立全文索引** — 適合會議紀錄等檔名不反映內容的情況\n"
        "5. 在下方輸入問題即可！"
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
    if not github_repo:
        st.error("⚠️ 請先在左側填寫 GitHub 倉庫")
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
