# 🏫 學校事務助手 Chatbot

基於 GitHub PDF 文件 + Qwen AI 的學校事務智能問答系統。

## 功能

- 📄 **自動讀取 GitHub PDF** — 從你指定的 GitHub 倉庫讀取所有 PDF
- 🔍 **智能文件篩選** — 用 Qwen AI 判斷哪些 PDF 與問題相關
- 🤖 **AI 問答** — 根據 PDF 內容生成準確的繁體中文回答
- 💬 **對話記憶** — 保留最近對話上下文，支持追問
- 📌 **來源引用** — 每個回答都標注引用了哪些文件

---

## 快速開始

### 1. 安裝依賴

```bash
cd ~/school-chatbot
pip install -r requirements.txt
```

### 2. 設定環境變數（選填）

```bash
cp .env.example .env
# 用文字編輯器填入 API Key 和 GitHub 倉庫
```

### 3. 啟動應用

```bash
streamlit run app.py
```

瀏覽器會自動開啟 `http://localhost:8501`

---

## 配置說明

| 欄位 | 說明 | 是否必填 |
|------|------|----------|
| **Qwen API Key** | 通義千問國際版 API Key | ✅ 必填 |
| **GitHub 倉庫** | 存放 PDF 的倉庫，格式 `username/repo` | ✅ 必填 |
| **PDF 子目錄** | PDF 所在的目錄路徑，如 `pdfs/` | 選填 |
| **GitHub Token** | 私有倉庫需要提供 | 選填 |

### 取得 Qwen API Key

1. 前往 [Alibaba Cloud International](https://www.alibabacloud.com/product/bailian)
2. 開通 DashScope 服務
3. 在 API Keys 頁面生成 Key

---

## GitHub 倉庫結構建議

```
your-school-repo/
└── pdfs/
    ├── 2024-2025學年曆.pdf
    ├── 學校手冊.pdf
    ├── 考試時間表.pdf
    ├── 制服規定.pdf
    └── 請假申請程序.pdf
```

> **提示：** PDF 文件名越清晰，AI 越容易找到相關文件。
> 建議用中文命名，例如 `學費繳交通告.pdf` 而非 `doc001.pdf`。

---

## 系統架構

```
用戶問題
   │
   ▼
[Qwen AI] 分析問題，從文件列表中選出最相關的 PDF（最多3個）
   │
   ▼
[GitHub] 下載相關 PDF 並提取文字
   │
   ▼
[文字切片] 將 PDF 文字切成片段，取最相關的片段作為上下文
   │
   ▼
[Qwen AI] 根據文件內容生成回答
   │
   ▼
顯示回答 + 來源文件
```

---

## 常見問題

**Q: 支持多語言嗎？**  
A: 系統主要支持繁體中文，也可處理英文 PDF。

**Q: PDF 文字提取不完整怎麼辦？**  
A: 掃描版 PDF（圖片格式）無法提取文字，請上傳可搜索文字的 PDF。

**Q: 如何更新 PDF 列表？**  
A: 點擊側邊欄的「🔄 載入 PDF」按鈕即可刷新。

**Q: 私有倉庫的 PDF 可以用嗎？**  
A: 可以，在側邊欄填入 GitHub Personal Access Token。
