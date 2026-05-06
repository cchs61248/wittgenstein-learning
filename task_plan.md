# 任務計畫：多資料源支援（仿 NotebookLM）

## 目標
讓 UploadModal 支援加入多個資料源（檔案上傳、URL 擷取、純文字），合併後送入現有 chunking 流程

## 目前階段
階段 1（完成）→ 階段 2（規劃中）

## 各階段

### 階段 1：架構探索與需求釐清
- [x] 閱讀 backend/routers/upload.py — 單一檔案上傳，回傳 file_id
- [x] 閱讀 backend/main.py — start_session 只接受 uploaded_file_id 或 content（二擇一）
- [x] 閱讀 backend/files/upload_store.py — 本地 .bin/.meta.json 儲存
- [x] 閱讀 frontend/src/components/UploadModal.tsx — 目前 UI 一次只能有一個來源
- [x] 閱讀 frontend/src/App.tsx — handleStart 傳遞 uploadedFileId + content 給 WS
- **狀態：** complete

### 階段 2：方案設計與決策
- [x] 確定合併策略（見下方「已做決策」）
- [x] 確定後端 API 改動範圍
- [x] 確定前端 UI 設計
- **狀態：** complete

### 階段 3：後端實作
- [x] 新增 `POST /upload/url` — 接收 URL，抓取網頁/YouTube 字幕轉文字，回傳 file_id
- [x] 修改 `POST /upload` 確認可多次呼叫（現已支援）
- [x] 修改 `start_session` WebSocket payload — 改為接受 `sources` 陣列（見下方格式）
- [x] 修改 main.py 的 start_session 處理邏輯 — 每來源獨立 chunking，全域重新編號，附來源 metadata
- [x] 新增 URL 擷取工具 `backend/utils/url_fetcher.py`
- [x] 修改 upload_store.py 支援 extra_meta 參數
- [x] 更新 ContentSplitter prompt 加入跨來源聚合原則
- [x] 更新 ContentSplitter agent 依來源分組顯示 chunks
- **狀態：** complete

### 階段 4：前端實作
- [x] 重新設計 UploadModal — 改為「資料源清單」模式
- [x] 支援：拖曳/點擊上傳多個檔案（每個都呼叫 /upload 取得 file_id）
- [x] 支援：輸入 URL 並加入清單（呼叫 /upload/url 取得 file_id）
- [x] 支援：輸入純文字並加入清單（inline text source）
- [x] 每個資料源顯示名稱、類型圖示、刪除按鈕
- [x] 修改 handleStart 傳遞 sources 陣列
- [x] 新增 uploadUrl API 函式
- [x] 新增 CSS 樣式
- **狀態：** complete

### 階段 5：測試與驗證
- [ ] 測試：多檔案上傳合併
- [ ] 測試：URL 擷取（公開網頁）
- [ ] 測試：純文字 + 檔案混合
- [ ] 測試：錯誤情境（無效 URL、超大檔案）
- **狀態：** pending（待使用者測試）

## 已做決策

| 決策 | 理由 |
|------|------|
| **合併策略：串接文字再統一 chunking** | 最小改動現有 orchestrator/chunking 流程；source_chunks 表不需改 schema |
| **URL 擷取在後端執行** | 前端無法繞過 CORS；後端統一處理安全性與錯誤 |
| **每個 source 先獨立上傳取得 file_id** | 後端已有 upload_store，重複使用；純文字也轉成 text source 統一格式 |
| **start_session payload 改為 sources 陣列** | 向後相容：若 sources 為空則 fallback 到舊的 uploaded_file_id/content |
| **純文字直接在 sources 陣列中傳遞，不上傳** | 避免為純文字建立磁碟檔案；text source 格式 `{type:"text", content:"..."}` |

## start_session payload 新格式（草稿）

```json
{
  "type": "start_session",
  "payload": {
    "provider": "claude",
    "model": "claude-sonnet-4-6",
    "target_depth": "intermediate",
    "question_mode": "short_answer",
    "sources": [
      { "type": "file", "file_id": "upl_abc123" },
      { "type": "file", "file_id": "upl_def456" },
      { "type": "url",  "file_id": "upl_ghi789" },
      { "type": "text", "content": "直接貼上的文字..." }
    ]
  }
}
```

## /upload/url 端點（草稿）

```
POST /upload/url
Body: { "url": "https://example.com/article" }
Headers: Authorization: Bearer <token>
Response: { "file_id": "upl_xxx", "title": "文章標題", "char_count": 3200 }
```

後端處理：
1. requests.get(url) → BeautifulSoup 抽取正文（readability-lxml）
2. YouTube URL → youtube_transcript_api 抓字幕
3. 儲存純文字為 .bin（UTF-8）+ .meta.json（type=url, original_url=...）
4. 回傳 file_id

## URL 擷取限制（與 NotebookLM 對齊）
- 只支援公開、無需登入的網頁
- YouTube 只支援有字幕的影片
- 不支援動態渲染（SPA）→ 僅靠 requests 靜態抓取
- 單次最大 500,000 字（截斷）

## 遇到的錯誤
| 錯誤 | 嘗試次數 | 解決方案 |
|------|---------|---------|
| — | — | — |

## 備註
- 現有 /upload 不需改動（已支援多次呼叫）
- text_extractor.py 已支援 .txt 格式，URL 擷取結果存成純文字即可重用
- 前端 UploadModal 是最大改動點
