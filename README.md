# 🏫 學校事務助手 Chatbot

基於 GitHub DOCX 文件 + Qwen AI 的學校事務智能問答系統。

## 功能

- 📄 **自動讀取 GitHub DOCX** — 從你指定的 GitHub 倉庫讀取所有 Word 文件
- 📥 **批量上載 PDF/DOCX** — PDF 會自動轉成 DOCX 後再加入索引
- 🤖 **AI 問答** — 根據 DOCX 內容生成準確的繁體中文回答
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

### 管理員上載（已內建）

在主程式側邊欄「管理員上載文件」中可直接：
- 一次上載多個 PDF 或 DOCX
- PDF 會先轉換為 DOCX
- 轉換後的 DOCX 會加入索引供 AI 分析
- 若有 GitHub 寫入權限，會把 DOCX 儲存到倉庫

---

## 配置說明

| 欄位 | 說明 | 是否必填 |
|------|------|----------|
| **Qwen API Key** | 通義千問國際版 API Key | ✅ 必填 |
| **GitHub 倉庫** | 存放 DOCX 的倉庫，格式 `username/repo` | ✅ 必填 |
| **文件子目錄** | DOCX 所在目錄路徑，如 `docs/` | 選填 |
| **GitHub Token** | 私有倉庫需要提供 | 選填 |

### 取得 Qwen API Key

1. 前往 [Alibaba Cloud International](https://www.alibabacloud.com/product/bailian)
2. 開通 DashScope 服務
3. 在 API Keys 頁面生成 Key

---

## GitHub 倉庫結構建議

```
your-school-repo/
└── docs/
   ├── 2024-2025學年曆.docx
   ├── 學校手冊.docx
   ├── 考試時間表.docx
   ├── 制服規定.docx
   └── 請假申請程序.docx
```

> **提示：** 文件名越清晰，AI 越容易找到相關文件。
> 建議用中文命名，例如 `學費繳交通告.docx` 而非 `doc001.docx`。

---

## 系統架構

```
用戶問題
   │
   ▼
[索引檢索] 從 DOCX 片段中找出最相關內容
   │
   ▼
[GitHub/上載文件] 讀取 DOCX 並提取文字
   │
   ▼
[文字切片] 將 DOCX 文字切成片段，取最相關片段作為上下文
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

**Q: PDF 轉換失敗怎麼辦？**  
A: 掃描版 PDF（圖片格式）可能無法提取文字，建議先 OCR 後再上載，或直接上載原始 DOCX。

**Q: 如何更新文件列表？**  
A: 點擊側邊欄的「🔄 重新整理」按鈕即可刷新。

**Q: 私有倉庫的文件可以用嗎？**  
A: 可以，在側邊欄填入 GitHub Personal Access Token。
