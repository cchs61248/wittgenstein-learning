# 發現與決策

## 需求
- 目前只能上傳一個檔案或貼上一段純文字（二擇一）
- 目標：仿 NotebookLM，支援多個資料源（URL、檔案、純文字），可混合使用

## 架構現況發現

### 後端
- `POST /upload`：單次上傳一個檔案，存到 `data/uploads/{file_id}.bin` + `.meta.json`，回傳 `file_id`
- `upload_store.py`：`save_upload(filename, mime_type, raw)` + `load_upload(file_id)` — 純磁碟 key-value
- `main.py` start_session 處理：
  - 讀 `uploaded_file_id`（若有） → load_upload → extract_text → build_source_chunks
  - 否則讀 `content`（pure text） → build_source_chunks
  - **一次只能有一個來源**
- `text_extractor.py`：extract_text(filename, raw_bytes) → str，支援 PDF/DOCX/PPTX/MD/TXT
- `chunker.py`：build_source_chunks(text) → list[dict]，輸入純字串，無來源感知

### 前端
- `UploadModal.tsx`：
  - 上傳檔案 → setUploadedFileId（同時清空 content）
  - 輸入 textarea → setContent（同時清空 uploadedFileId）
  - **互斥設計**，無法同時有多個來源
- `App.tsx handleStart`：傳 `uploadedFileId` + `content` 給 WebSocket start_session

### DB
- `source_chunks` 表：chunk_id, session_id, text, position — 不記錄「哪個來源」
- 合併後 chunking 不影響此 schema

## 技術決策

| 決策 | 理由 |
|------|------|
| 文字串接後統一 chunking，不追蹤個別來源 | 最小後端改動；orchestrator/evaluator 全部依賴 source_chunks，不需動 |
| 新增 `POST /upload/url` 獨立端點 | 與現有 /upload 分開，職責清晰；錯誤處理不同 |
| 使用 `readability-lxml` 抽取網頁正文 | 比直接用 BeautifulSoup 簡單；主流選擇 |
| YouTube 使用 `youtube-transcript-api` | 免費、無需 API key，直接取字幕文字 |
| sources 陣列格式向後相容舊 payload | 避免書櫃現有 session 的 resume 流程受影響 |
| 純文字 source 直接在 payload 傳，不上傳磁碟 | 省磁碟空間；text source 轉換成 raw bytes 在後端 build_source_chunks 前串接 |

## 需新增的 Python 套件
```
readability-lxml   # 網頁正文抽取
youtube-transcript-api  # YouTube 字幕
requests           # HTTP 請求（通常已有）
```

## 前端 UI 設計（UploadModal 重新設計）

```
┌─────────────────────────────────────────────────────┐
│  上傳學習材料                                         │
│                                                     │
│  ┌─ 加入資料源 ───────────────────────────────────┐  │
│  │  [拖曳/點擊上傳檔案]  [輸入 URL]  [貼上文字]   │  │
│  └──────────────────────────────────────────────┘  │
│                                                     │
│  資料源清單（0/50）                                  │
│  ┌──────────────────────────────────────────────┐  │
│  │ 📄 report.pdf         3,200 字  [×]          │  │
│  │ 🔗 example.com/art... 2,100 字  [×]          │  │
│  │ 📝 貼上的文字         1,500 字  [×]          │  │
│  └──────────────────────────────────────────────┘  │
│                                                     │
│  [AI 設定...] [開始學習]                             │
└─────────────────────────────────────────────────────┘
```

## 遇到的問題

| 問題 | 解決方案 |
|------|---------|
| — | — |

## 資源
- readability-lxml PyPI: https://pypi.org/project/readability-lxml/
- youtube-transcript-api PyPI: https://pypi.org/project/youtube-transcript-api/

---
*每執行2次查看/瀏覽器/搜尋操作後更新此檔案*
