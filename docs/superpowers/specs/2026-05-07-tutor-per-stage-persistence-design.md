# Tutor Q&A 按章節持久化設計

**日期**：2026-05-07  
**狀態**：已核准，待實作

---

## 背景

`ask_tutor` 問答目前以平坦陣列存於 localStorage（`wl_tutor_history`），所有章節混在一起，且只存在當前瀏覽器。需改為：

1. SQLite 為主要資料來源（跨裝置、重啟不遺失）
2. localStorage 作快取（減少重載延遲）
3. 以 `stage_id` 分章節儲存與顯示

---

## 資料模型

### 新增 `tutor_records` 表（Migration 011）

```sql
CREATE TABLE IF NOT EXISTS tutor_records (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  stage_id    INTEGER NOT NULL,
  question    TEXT NOT NULL,
  answer      TEXT NOT NULL,
  in_scope    INTEGER NOT NULL DEFAULT 1,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tutor_records_session
  ON tutor_records(session_id, stage_id);
```

`in_scope` 以 INTEGER 儲存 boolean（1=true, 0=false），與 SQLite 慣例一致。

### session_memory.py 新增函式

| 函式 | 說明 |
|------|------|
| `insert_tutor_record(session_id, stage_id, question, answer, in_scope: bool)` | 寫入一筆問答記錄 |
| `get_all_tutor_records(session_id)` → `dict[int, list[dict]]` | 以 stage_id 分組回傳全部記錄 |

`get_all_tutor_records` 回傳格式：
```python
{
  1: [{"question": "...", "answer": "...", "in_scope": True}, ...],
  3: [...],
}
```

---

## 後端流程變更

### handle_student_question（learning_orchestrator.py）

生成 `answer` 後，立即持久化：

```python
await session_memory.insert_tutor_record(
    session_id, wm.current_stage_id, question, answer, in_scope
)
```

`tutor_reply` WebSocket payload 加入 `stage_id`：

```python
{
  "type": "tutor_reply",
  "payload": {
    "question": question,
    "answer": answer,
    "in_scope": in_scope,
    "stage_id": wm.current_stage_id   # 新增
  }
}
```

### session_snapshot（resume_session）

`session_snapshot` payload 擴充 `tutor_histories`：

```python
{
  "type": "session_snapshot",
  "payload": {
    "stage_explanations": ...,
    "stage_qa_histories": ...,
    "decision_history": ...,
    "tutor_histories": session_memory.get_all_tutor_records(session_id)  # 新增
  }
}
```

---

## 前端型別變更（types/messages.ts）

```typescript
// TutorReplyPayload 加 stage_id
export interface TutorReplyPayload {
  question: string;
  answer: string;
  in_scope?: boolean;
  stage_id: number;   // 新增
}

// SessionSnapshotPayload 加 tutor_histories
export interface SessionSnapshotPayload {
  stage_explanations: Record<number, string>;
  stage_qa_histories: Record<number, QARecord[]>;
  decision_history: DecisionRecord[];
  tutor_histories?: Record<number, TutorMessage[]>;  // 新增
}

// TutorMessage 獨立型別（原本 inline，現在提取）
export interface TutorMessage {
  question: string;
  answer: string;
  in_scope?: boolean;
}
```

---

## 前端 Store 變更（sessionStore.ts）

### 狀態型別

```typescript
// 由陣列改為以 stage_id 為 key 的字典
tutorHistory: Record<number, TutorMessage[]>
```

### localStorage

- 舊 key：`wl_tutor_history`（全局，所有章節混用）
- 新 key：`wl_tutor_{sessionId}`（含 session_id，隔離不同 session）
- 格式：整個 `Record<number, TutorMessage[]>` JSON 序列化

### 新增/修改 Actions

| Action | 說明 |
|--------|------|
| `addTutorMessage(msg: TutorReplyPayload)` | 以 `msg.stage_id` 寫入對應 bucket，同步 localStorage |
| `setTutorHistories(map: Record<number, TutorMessage[]>)` | session_snapshot 批量恢復，同步 localStorage |
| `clearTutorHistory()` | 清除整個 Record，移除 localStorage key |

### localStorage 載入時機

store 初始化時 `sessionId` 尚未知，**不在 init 時讀取 localStorage**。改為：

- `setTutorHistories(map, sessionId)` — `session_snapshot` 收到時，以 SQLite 回傳的 `tutor_histories` 為準寫入 store，同時寫快取 `wl_tutor_{sessionId}`
- `addTutorMessage(msg, sessionId)` — 新增時同步更新 `wl_tutor_{sessionId}`
- localStorage 快取僅作「重載後、`session_snapshot` 尚未到達前」的預填；`session_snapshot` 到達後以後端資料覆蓋

若需在 `session_snapshot` 前預填（可選優化）：在 `setActiveSession(sessionId)` action 中讀取 `wl_tutor_{sessionId}`：

```typescript
setActiveSession: (sessionId) => {
  let cache: Record<number, TutorMessage[]> = {};
  try {
    const raw = localStorage.getItem(`wl_tutor_${sessionId}`);
    if (raw) cache = JSON.parse(raw);
  } catch { /* ignore */ }
  set({ sessionId, tutorHistory: cache });
}
```

---

## 前端 UI 變更

### AskTutorPanel.tsx

Props 加 `currentStageId: number`，元件內讀取：

```typescript
const stageHistory = tutorHistory[currentStageId] ?? [];
```

只顯示當前章節的問答，切換 stage 時自動切換。計數也改為顯示當前章節筆數。

### App.tsx

```typescript
// session_snapshot case
case 'session_snapshot':
  setTutorHistories(msg.payload.tutor_histories ?? {});
  // ... 其他 snapshot 處理
  break;

// tutor_reply case（不變，payload 已含 stage_id）
case 'tutor_reply':
  addTutorMessage(msg.payload);
  break;

// AskTutorPanel 傳入 currentStageId
<AskTutorPanel
  currentStageId={currentStageId}
  onAskTutor={handleAskTutor}
  isCollapsed={tutorCollapsed}
  onToggle={() => setTutorCollapsed(v => !v)}
  isLoading={isTutorLoading}
/>
```

---

## 錯誤處理

- `insert_tutor_record` 失敗不影響 `tutor_reply` 送出（記錄失敗只 log，不中斷對話）
- `get_all_tutor_records` 失敗時 `session_snapshot` 的 `tutor_histories` 給空 `{}`
- 前端 `setTutorHistories` 收到 `undefined` 時 fallback 為 `{}`
- 舊版 localStorage key `wl_tutor_history` 在 `clearAuth`/`clearSession` 時一併清除（向下相容清理）

---

## 遷移相容性

- 舊 session（無 `tutor_records`）resume 時 `tutor_histories` 為 `{}`，前端顯示空歷史，無異常
- 前端收到不含 `stage_id` 的舊版 `tutor_reply`（不應發生，但防禦）：丟棄不寫入

---

## 實作範圍摘要

| 層 | 檔案 | 變更類型 |
|---|------|---------|
| DB | `backend/database.py` | 新增 Migration 011 |
| Backend | `backend/memory/session_memory.py` | 新增 2 個函式 |
| Backend | `backend/orchestrator/learning_orchestrator.py` | `handle_student_question` + `resume_session` |
| Frontend | `frontend/src/types/messages.ts` | 型別擴充 |
| Frontend | `frontend/src/store/sessionStore.ts` | 狀態結構 + localStorage key 更改 |
| Frontend | `frontend/src/components/AskTutorPanel.tsx` | Props + 過濾邏輯 |
| Frontend | `frontend/src/App.tsx` | snapshot case + AskTutorPanel props |
