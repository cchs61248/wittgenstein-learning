# 斷線恢復測試清單（手動 + 半自動）

> 目標：確認學習流程在「刷新、切分頁、換裝置、斷線重連」後，畫面與資料可完整恢復。  
> 範圍：`session_started`、`session_snapshot`、`resume_state`、章節進度、講解內容、答題紀錄、當前題目、最新回饋。

---

## 0. 前置條件

- 後端啟動：`backend/.venv/Scripts/python.exe -m uvicorn run:app --reload --port 8000`
- 前端啟動：`npm run dev`
- 準備一組測試帳號（A 裝置、B 裝置共用）
- 完成至少 2 個 stage 的講解與答題，確保 DB 有：
  - `stage_progress.full_explanation`
  - `qa_records`
  - 一個「未完成但有當前題目」的 stage

---

## 1. 手動測試清單

### Case 1：同裝置 F5 刷新恢復

1. 在答題畫面（已有最新回饋 + 下一題未答）按 F5。  
2. 觀察是否自動重連並恢復：
   - 左側章節進度（包含動態節點）
   - 當前 stage 講解內容
   - 歷史答題紀錄
   - 當前題目與輸入狀態
   - 最新回饋卡片

**預期**
- 不回到上傳頁。
- 不遺失 `session_snapshot` 內容。
- 不重複新增 QA 紀錄。

### Case 2：分頁切換造成 WS 斷線後恢復

1. 開啟學習頁，在背景分頁停留 2~5 分鐘（模擬瀏覽器節流）。
2. 回到該分頁，確認自動 resume。

**預期**
- UI 內容一致，無「空白講解」或「題目消失」。
- 若伺服器已有新問題，顯示最新當前題。

### Case 3：雙裝置接力登入（同帳號）

1. A 裝置正在學習中。
2. B 裝置登入同帳號並進入學習頁。
3. A 應收到 kicked；B 應完整恢復狀態。

**預期**
- B 可看到完整快照（講解/歷史/當前題/回饋）。
- A 斷線後不應破壞 B 的進度。

### Case 4：邊界時點恢復（最容易出錯）

分別在以下時機刷新：
- 題目剛送出（尚未收到 feedback）
- feedback 剛回來（尚未按「下一題」）
- stage_decision 剛回來（即將切下一節）

**預期**
- 不重複評分、不重複插入 qa_records。
- 恢復後狀態與伺服器一致。

---

## 2. 自動化腳本骨架（建議先跑 smoke）

- 檔案：`backend/tools/recovery_smoke_test.py`
- 目的：驗證 resume 時至少收到：
  - `session_started`
  - `session_snapshot`
  - `resume_state`

### 執行範例

```powershell
cd backend
./.venv/Scripts/python.exe tools/recovery_smoke_test.py `
  --base-url http://localhost:8000 `
  --token "<JWT>" `
  --session-id "<session_id>" `
  --provider claude
```

---

## 3. 驗收標準（DoD）

- 所有 Case 通過。
- 重新整理/換裝置後，核心畫面可在 3 秒內恢復。
- 無重複答題記錄、無錯誤決策跳轉。
- `session_snapshot` 與 `resume_state` 在恢復流程中穩定可用。

