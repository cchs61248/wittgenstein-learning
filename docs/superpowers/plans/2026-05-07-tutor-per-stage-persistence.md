# Tutor Q&A 按章節持久化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `ask_tutor` 問答依章節（stage_id）持久化至 SQLite，前端依當前章節過濾顯示，resume 時從 `session_snapshot` 完整恢復。

**Architecture:** 新增 `tutor_records` SQLite 表（Migration 013），後端在 `handle_student_question` 生成答案後寫入 DB，`resume_session` 的 `session_snapshot` 攜帶 `tutor_histories` 全量恢復。前端 `tutorHistory` 由平坦陣列改為 `Record<number, TutorMessage[]>`，`AskTutorPanel` 依 `currentStageId` 過濾顯示。localStorage 作寫後快取（key: `wl_tutor_{sessionId}`）。

**Tech Stack:** Python/aiosqlite、`unittest.IsolatedAsyncioTestCase`（後端測試）；TypeScript/React/Zustand（前端）

---

## 檔案影響範圍

| 檔案 | 動作 | 說明 |
|------|------|------|
| `backend/db/database.py` | 修改 | 加入 Migration 013 |
| `backend/memory/session_memory.py` | 修改 | 新增 2 個函式 |
| `backend/tests/test_tutor_records.py` | 新增 | 單元測試 |
| `backend/orchestrator/learning_orchestrator.py` | 修改 | handle_student_question + resume_session |
| `frontend/src/types/messages.ts` | 修改 | TutorMessage、TutorReplyPayload、SessionSnapshotPayload |
| `frontend/src/store/sessionStore.ts` | 修改 | tutorHistory 型別 + 新增 setTutorHistories |
| `frontend/src/components/AskTutorPanel.tsx` | 修改 | currentStageId prop + 過濾邏輯 |
| `frontend/src/App.tsx` | 修改 | session_snapshot 處理 + AskTutorPanel props |

---

## Task 1：後端資料層 — Migration 013 + session_memory 函式

**Files:**
- Modify: `backend/db/database.py`（Migration 012 區塊之後，約第 118–125 行）
- Modify: `backend/memory/session_memory.py`（末尾加入）
- Create: `backend/tests/test_tutor_records.py`

- [ ] **Step 1: 撰寫測試（先讓測試失敗）**

建立 `backend/tests/test_tutor_records.py`：

```python
import tempfile
import unittest
from pathlib import Path

from backend.db.database import close_db, init_db
from backend.memory.session_memory import get_all_tutor_records, insert_tutor_record


class TestTutorRecords(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmp_dir.name) / "test.db")
        await init_db(db_path)

    async def asyncTearDown(self) -> None:
        await close_db()
        self._tmp_dir.cleanup()

    async def test_insert_and_get_single_stage(self):
        await insert_tutor_record("sess1", 2, "什麼是命題？", "命題是有真假值的陳述。", True)
        result = await get_all_tutor_records("sess1")
        self.assertEqual(list(result.keys()), [2])
        self.assertEqual(len(result[2]), 1)
        self.assertEqual(result[2][0]["question"], "什麼是命題？")
        self.assertEqual(result[2][0]["answer"], "命題是有真假值的陳述。")
        self.assertTrue(result[2][0]["in_scope"])

    async def test_get_empty_session_returns_empty_dict(self):
        result = await get_all_tutor_records("no_such_session")
        self.assertEqual(result, {})

    async def test_multiple_stages_grouped_correctly(self):
        await insert_tutor_record("sess2", 1, "問題A", "回答A", True)
        await insert_tutor_record("sess2", 3, "問題B", "回答B", False)
        await insert_tutor_record("sess2", 1, "問題C", "回答C", True)
        result = await get_all_tutor_records("sess2")
        self.assertIn(1, result)
        self.assertIn(3, result)
        self.assertEqual(len(result[1]), 2)
        self.assertEqual(len(result[3]), 1)
        self.assertEqual(result[1][0]["question"], "問題A")
        self.assertEqual(result[1][1]["question"], "問題C")
        self.assertFalse(result[3][0]["in_scope"])

    async def test_sessions_isolated(self):
        await insert_tutor_record("sessA", 1, "Q", "A", True)
        result = await get_all_tutor_records("sessB")
        self.assertEqual(result, {})

    async def test_in_scope_false_persisted(self):
        await insert_tutor_record("sess3", 1, "超出教材問題", "外部知識回答", False)
        result = await get_all_tutor_records("sess3")
        self.assertFalse(result[1][0]["in_scope"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/backend
.venv/Scripts/pip install -r requirements.txt -q
.venv/Scripts/pip install pytest -q
.venv/Scripts/python.exe -m pytest tests/test_tutor_records.py -v
```

預期：`ImportError: cannot import name 'get_all_tutor_records' from 'backend.memory.session_memory'`

- [ ] **Step 3: 加入 Migration 013**

在 `backend/db/database.py` 找到 Migration 012 結尾（約第 125 行 `except Exception: pass`），在其後加入：

```python
    # Migration 013：建立 tutor_records 表（ask_tutor 問答按章節持久化）
    await _connection.execute(
        """CREATE TABLE IF NOT EXISTS tutor_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            stage_id    INTEGER NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            in_scope    INTEGER NOT NULL DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tutor_records_session "
        "ON tutor_records(session_id, stage_id)"
    )
    await _connection.commit()
```

（Migration 006 的 `decision_records` 建立語句在 `close_db` 之前，Migration 013 放在 Migration 012 區塊之後、Migration 006 區塊之前即可。）

- [ ] **Step 4: 在 session_memory.py 末尾加入兩個函式**

```python
async def insert_tutor_record(
    session_id: str,
    stage_id: int,
    question: str,
    answer: str,
    in_scope: bool,
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO tutor_records (session_id, stage_id, question, answer, in_scope)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, stage_id, question, answer, 1 if in_scope else 0),
    )
    await db.commit()


async def get_all_tutor_records(session_id: str) -> dict[int, list[dict]]:
    """回傳 session 所有 tutor 問答，以 stage_id 分組，按插入順序排列。"""
    db = await get_db()
    async with db.execute(
        """SELECT stage_id, question, answer, in_scope
           FROM tutor_records WHERE session_id = ?
           ORDER BY id ASC""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    result: dict[int, list[dict]] = {}
    for row in rows:
        sid = row["stage_id"]
        if sid not in result:
            result[sid] = []
        result[sid].append({
            "question": row["question"],
            "answer": row["answer"],
            "in_scope": bool(row["in_scope"]),
        })
    return result
```

- [ ] **Step 5: 執行測試確認全部通過**

```bash
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/backend
.venv/Scripts/python.exe -m pytest tests/test_tutor_records.py -v
```

預期：`5 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/db/database.py backend/memory/session_memory.py backend/tests/test_tutor_records.py
git commit -m "feat: Migration 013 tutor_records 表與 session_memory 函式"
```

---

## Task 2：後端 — handle_student_question 持久化與 stage_id

**Files:**
- Modify: `backend/orchestrator/learning_orchestrator.py`（`handle_student_question` 函式，約第 1369–1447 行）

- [ ] **Step 1: 修改早期返回路徑（無教材時的 emit）**

在 `handle_student_question` 中找到約第 1385–1389 行的早期返回：

```python
# 找到：
        await emit({
            "type": "tutor_reply",
            "payload": {"question": question, "answer": "目前沒有可用教材內容，請先開始學習流程。"},
        })
        return

# 改成：
        await emit({
            "type": "tutor_reply",
            "payload": {
                "question": question,
                "answer": "目前沒有可用教材內容，請先開始學習流程。",
                "stage_id": wm.current_stage_id,
            },
        })
        return
```

- [ ] **Step 2: 修改正常路徑——加入 DB 寫入並在 payload 加 stage_id**

找到約第 1435–1447 行的 LLM 呼叫與 emit：

```python
# 找到：
        ans_resp = await self.teacher.llm.chat(
            answer_messages, system_prompt=SYSTEM_PROMPTS["tutor_reply"]
        )
        await emit(
            {
                "type": "tutor_reply",
                "payload": {
                    "question": question,
                    "answer": ans_resp.content.strip(),
                    "in_scope": in_scope,
                },
            }
        )

# 改成：
        ans_resp = await self.teacher.llm.chat(
            answer_messages, system_prompt=SYSTEM_PROMPTS["tutor_reply"]
        )
        answer = ans_resp.content.strip()
        try:
            await session_memory.insert_tutor_record(
                session_id, wm.current_stage_id, question, answer, in_scope
            )
        except Exception as e:
            _log.warning("insert_tutor_record failed: %s", e)
        await emit(
            {
                "type": "tutor_reply",
                "payload": {
                    "question": question,
                    "answer": answer,
                    "in_scope": in_scope,
                    "stage_id": wm.current_stage_id,
                },
            }
        )
```

（`session_memory` 已在 `learning_orchestrator.py` 頂部匯入，無需額外 import。）

- [ ] **Step 3: Commit**

```bash
git add backend/orchestrator/learning_orchestrator.py
git commit -m "feat: ask_tutor 問答持久化至 DB，tutor_reply payload 加入 stage_id"
```

---

## Task 3：後端 — resume_session 加入 tutor_histories

**Files:**
- Modify: `backend/orchestrator/learning_orchestrator.py`（`resume_session` 函式，`session_snapshot` emit 區塊，約第 1520–1540 行）

- [ ] **Step 1: 在 session_snapshot emit 前讀取 tutor_histories，並加入 payload**

找到 `session_snapshot` emit（約第 1520 行）：

```python
# 找到：
        await emit({
            "type": "session_snapshot",
            "payload": {
                "stage_explanations": client_explanations,
                "stage_qa_histories": {
                    str(stage_id): [
                        {
                            "question_id": r["question_id"],
                            "question_text": r["question_text"],
                            "question_type": r["question_type"],
                            "user_answer": r["user_answer"],
                            "score": r["score"],
                            "feedback_text": r["feedback"],
                        }
                        for r in records
                    ]
                    for stage_id, records in all_histories.items()
                },
                "decision_history": decision_history,
            },
        })

# 改成：
        try:
            raw_tutor = await session_memory.get_all_tutor_records(session_id)
        except Exception as e:
            _log.warning("get_all_tutor_records failed: %s", e)
            raw_tutor = {}

        await emit({
            "type": "session_snapshot",
            "payload": {
                "stage_explanations": client_explanations,
                "stage_qa_histories": {
                    str(stage_id): [
                        {
                            "question_id": r["question_id"],
                            "question_text": r["question_text"],
                            "question_type": r["question_type"],
                            "user_answer": r["user_answer"],
                            "score": r["score"],
                            "feedback_text": r["feedback"],
                        }
                        for r in records
                    ]
                    for stage_id, records in all_histories.items()
                },
                "decision_history": decision_history,
                "tutor_histories": {
                    str(stage_id): records
                    for stage_id, records in raw_tutor.items()
                },
            },
        })
```

- [ ] **Step 2: Commit**

```bash
git add backend/orchestrator/learning_orchestrator.py
git commit -m "feat: session_snapshot 加入 tutor_histories 全量恢復"
```

---

## Task 4：前端型別

**Files:**
- Modify: `frontend/src/types/messages.ts`（第 113–132 行）

- [ ] **Step 1: 新增 TutorMessage，更新 TutorReplyPayload 與 SessionSnapshotPayload**

找到第 113–132 行：

```typescript
// 找到：
export interface TutorReplyPayload {
  question: string;
  answer: string;
  in_scope?: boolean;
}

export interface SessionSnapshotPayload {
  stage_explanations: Record<string, string>;
  stage_qa_histories: Record<string, QaHistoryRecord[]>;
  decision_history?: Array<{
    stage_id: number;
    decision: DecisionType;
    best_score: number;
    next_stage_id: number | null;
    next_stage_score?: number | null;
    reason_lines: string[];
    strategy_snapshot: StageDecisionPayload['strategy_snapshot'];
    created_at: string;
  }>;
}

// 改成：
export interface TutorMessage {
  question: string;
  answer: string;
  in_scope?: boolean;
}

export interface TutorReplyPayload extends TutorMessage {
  stage_id: number;
}

export interface SessionSnapshotPayload {
  stage_explanations: Record<string, string>;
  stage_qa_histories: Record<string, QaHistoryRecord[]>;
  decision_history?: Array<{
    stage_id: number;
    decision: DecisionType;
    best_score: number;
    next_stage_id: number | null;
    next_stage_score?: number | null;
    reason_lines: string[];
    strategy_snapshot: StageDecisionPayload['strategy_snapshot'];
    created_at: string;
  }>;
  tutor_histories?: Record<string, TutorMessage[]>;
}
```

- [ ] **Step 2: 確認 TypeScript 無錯誤（此時前端 store 尚未更新，預期有型別錯誤，但不應有 syntax error）**

```bash
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/frontend
npx tsc --noEmit 2>&1 | head -20
```

預期：可能有 `tutorHistory` 型別不符的錯誤（下一 Task 修正），但不能有 syntax error。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/messages.ts
git commit -m "feat: 新增 TutorMessage 型別，TutorReplyPayload 加 stage_id，SessionSnapshotPayload 加 tutor_histories"
```

---

## Task 5：前端 Store

**Files:**
- Modify: `frontend/src/store/sessionStore.ts`

- [ ] **Step 1: 更新 imports，加入 TutorMessage**

找到 `messages` types 的 import 行（約第 1–10 行），確認同時匯入 `TutorMessage` 與 `TutorReplyPayload`：

```typescript
// 確保 import 中含 TutorMessage（原本可能只有 TutorReplyPayload 的 inline 型別）
// 找到含 types/messages 的 import，加入 TutorMessage：
import type { ..., TutorMessage, TutorReplyPayload, ... } from '../types/messages';
```

若原本 `TutorReplyPayload` 未被匯入（只是 inline `{ question: string; ... }`），需確認 `TutorReplyPayload` 也加入 import。

- [ ] **Step 2: 更新 State 介面——tutorHistory 型別、addTutorMessage 簽名、新增 setTutorHistories**

找到約第 79–94 行的 State 介面定義：

```typescript
// 找到：
  tutorReply: { question: string; answer: string; in_scope?: boolean } | null;
  tutorHistory: { question: string; answer: string; in_scope?: boolean }[];
  isTutorLoading: boolean;
  setTutorLoading: (v: boolean) => void;
  addTutorMessage: (msg: { question: string; answer: string; in_scope?: boolean }) => void;

// 改成：
  tutorReply: TutorMessage | null;
  tutorHistory: Record<number, TutorMessage[]>;
  isTutorLoading: boolean;
  setTutorLoading: (v: boolean) => void;
  addTutorMessage: (msg: TutorReplyPayload) => void;
  setTutorHistories: (map: Record<number, TutorMessage[]>) => void;
```

找到 `setTutorReply`（約第 93 行）：

```typescript
// 找到：
  setTutorReply: (reply: { question: string; answer: string; in_scope?: boolean } | null) => void;

// 改成：
  setTutorReply: (reply: TutorMessage | null) => void;
```

- [ ] **Step 3: 刪除 loadTutorHistory 函式，初始值改為 `{}`**

找到約第 173–180 行的 `loadTutorHistory` 函式，**整個刪除**：

```typescript
// 刪除這整段：
function loadTutorHistory(): { question: string; answer: string; in_scope?: boolean }[] {
  try {
    const raw = localStorage.getItem('wl_tutor_history');
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}
```

找到初始值（約第 379 行）：

```typescript
// 找到：
  tutorHistory: loadTutorHistory(),

// 改成：
  tutorHistory: {},
```

- [ ] **Step 4: 更新 addTutorMessage**

找到約第 382–387 行：

```typescript
// 找到：
  addTutorMessage: (msg) =>
    set((s) => {
      const updated = [...s.tutorHistory, msg];
      localStorage.setItem('wl_tutor_history', JSON.stringify(updated));
      return { tutorReply: msg, tutorHistory: updated, isTutorLoading: false };
    }),

// 改成：
  addTutorMessage: (msg) =>
    set((s) => {
      const prev = s.tutorHistory[msg.stage_id] ?? [];
      const updated: Record<number, TutorMessage[]> = {
        ...s.tutorHistory,
        [msg.stage_id]: [
          ...prev,
          { question: msg.question, answer: msg.answer, in_scope: msg.in_scope },
        ],
      };
      if (s.sessionId) {
        try {
          localStorage.setItem(`wl_tutor_${s.sessionId}`, JSON.stringify(updated));
        } catch {}
      }
      return {
        tutorReply: { question: msg.question, answer: msg.answer, in_scope: msg.in_scope },
        tutorHistory: updated,
        isTutorLoading: false,
      };
    }),
```

- [ ] **Step 5: 加入 setTutorHistories（在 addTutorMessage 之後）**

```typescript
  setTutorHistories: (map) =>
    set((s) => {
      if (s.sessionId) {
        try {
          localStorage.setItem(`wl_tutor_${s.sessionId}`, JSON.stringify(map));
        } catch {}
      }
      return { tutorHistory: map };
    }),
```

- [ ] **Step 6: 更新 clearTutorHistory**

找到約第 388–391 行：

```typescript
// 找到：
  clearTutorHistory: () => {
    localStorage.removeItem('wl_tutor_history');
    set({ tutorHistory: [], tutorReply: null });
  },

// 改成：
  clearTutorHistory: () =>
    set((s) => {
      if (s.sessionId) {
        try { localStorage.removeItem(`wl_tutor_${s.sessionId}`); } catch {}
      }
      localStorage.removeItem('wl_tutor_history');
      return { tutorHistory: {}, tutorReply: null };
    }),
```

- [ ] **Step 7: 將所有 `tutorHistory: []` 改為 `tutorHistory: {}`**

用編輯器全域搜尋 `tutorHistory: []`，全部替換為 `tutorHistory: {}`。  
涉及位置：約第 211、289、609 行（clearAuth、pendingResets、clearSession 各一處）。

- [ ] **Step 8: 確認 TypeScript 無錯誤**

```bash
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/frontend
npx tsc --noEmit 2>&1 | head -30
```

預期：`AskTutorPanel` 相關型別錯誤（下一 Task 修正），其餘無誤。

- [ ] **Step 9: Commit**

```bash
git add frontend/src/store/sessionStore.ts
git commit -m "feat: tutorHistory 改為 Record<stage_id, msgs>，新增 setTutorHistories"
```

---

## Task 6：前端 UI — AskTutorPanel + App.tsx

**Files:**
- Modify: `frontend/src/components/AskTutorPanel.tsx`
- Modify: `frontend/src/App.tsx`

### AskTutorPanel.tsx

- [ ] **Step 1: 加入 currentStageId prop**

找到 Props 介面（第 8–13 行）：

```typescript
// 找到：
interface Props {
  onAskTutor: (question: string) => void;
  isCollapsed: boolean;
  onToggle: () => void;
  isLoading?: boolean;
}

// 改成：
interface Props {
  onAskTutor: (question: string) => void;
  isCollapsed: boolean;
  onToggle: () => void;
  isLoading?: boolean;
  currentStageId: number | null;
}
```

- [ ] **Step 2: 更新函式簽名與 store 讀取**

找到第 49–52 行：

```typescript
// 找到：
export function AskTutorPanel({ onAskTutor, isCollapsed, onToggle, isLoading = false }: Props) {
  const tutorHistory = useSessionStore((s) => s.tutorHistory);
  const clearTutorHistory = useSessionStore((s) => s.clearTutorHistory);

// 改成：
export function AskTutorPanel({ onAskTutor, isCollapsed, onToggle, isLoading = false, currentStageId }: Props) {
  const tutorHistoryMap = useSessionStore((s) => s.tutorHistory);
  const clearTutorHistory = useSessionStore((s) => s.clearTutorHistory);
  const stageHistory = currentStageId !== null && currentStageId !== undefined
    ? (tutorHistoryMap[currentStageId] ?? [])
    : [];
```

- [ ] **Step 3: 將元件內所有 `tutorHistory` 替換為 `stageHistory`**

以下 6 處需要替換（以完整改後結果呈現）：

```typescript
// 第 65 行
{tutorHistory.length > 0 && (
→ {stageHistory.length > 0 && (

// 第 66 行
{tutorHistory.length}
→ {stageHistory.length}

// 第 70 行
{tutorHistory.length > 0 && !isCollapsed && (
→ {stageHistory.length > 0 && !isCollapsed && (

// 第 103 行
{tutorHistory.length > 0 && (
→ {stageHistory.length > 0 && (

// 第 105 行
{[...tutorHistory].reverse().map((item, reversedIdx) => (
→ {[...stageHistory].reverse().map((item, reversedIdx) => (

// 第 109 行
index={tutorHistory.length - 1 - reversedIdx}
→ index={stageHistory.length - 1 - reversedIdx}
```

### App.tsx

- [ ] **Step 4: 加入 setTutorHistories 至 store 解構**

找到 `useSessionStore` 的解構（約第 40–67 行），加入 `setTutorHistories`：

```typescript
// 在現有解構中加入：
  addTutorMessage,
  setTutorHistories,    // 新增
  isTutorLoading,
```

- [ ] **Step 5: 在 session_snapshot case 加入 tutor_histories 恢復**

找到 `case 'session_snapshot':` 區塊，在 `break` 之前加入（放在所有其他 hydration 之後）：

```typescript
        // 恢復 tutor 問答歷史（string key 轉 number key）
        const tutorHistoriesRaw = msg.payload.tutor_histories ?? {};
        const tutorHistoriesMap: Record<number, { question: string; answer: string; in_scope?: boolean }[]> = {};
        for (const [k, v] of Object.entries(tutorHistoriesRaw)) {
          tutorHistoriesMap[Number(k)] = v;
        }
        setTutorHistories(tutorHistoriesMap);
        break;
```

（若 `break` 前已有其他語句，只需在 `break` 之前插入上方 4 行，確保 `break` 保留在最後。）

- [ ] **Step 6: 傳入 currentStageId 至 AskTutorPanel**

找到約第 978 行的 `<AskTutorPanel`：

```tsx
// 找到：
                <AskTutorPanel
                  onAskTutor={handleAskTutor}
                  isCollapsed={isAskTutorCollapsed}
                  onToggle={() => setIsAskTutorCollapsed((v) => !v)}
                  isLoading={isTutorLoading}

// 改成：
                <AskTutorPanel
                  currentStageId={currentStageId}
                  onAskTutor={handleAskTutor}
                  isCollapsed={isAskTutorCollapsed}
                  onToggle={() => setIsAskTutorCollapsed((v) => !v)}
                  isLoading={isTutorLoading}
```

（`currentStageId` 已在 App.tsx 第 71 行透過 `useSessionStore` 取得，直接使用。）

- [ ] **Step 7: 確認 TypeScript 全部無錯誤**

```bash
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/frontend
npx tsc --noEmit 2>&1
```

預期：無輸出（0 errors）。

- [ ] **Step 8: 確認前端 build 成功**

```bash
npm run build 2>&1 | tail -5
```

預期：`✓ built in ...`

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/AskTutorPanel.tsx frontend/src/App.tsx
git commit -m "feat: AskTutorPanel 依章節過濾問答，App 整合 session_snapshot tutor_histories 恢復"
```

---

## Task 7：端對端手動測試

**無需修改任何檔案。**

- [ ] **Step 1: 啟動後端**

```bash
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/backend
.venv/Scripts/python.exe -m uvicorn run:app --reload --port 8000
```

- [ ] **Step 2: 啟動前端**

```bash
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/frontend
npm run dev
```

- [ ] **Step 3: 測試問答正常流程**

1. 開啟 `http://localhost:5173`，登入
2. 上傳教材，等待 session 啟動（至少 2 個 stage）
3. 在第 1 章的「想追問老師」輸入問題，按送出
4. 確認：面板顯示 1 筆問答記錄
5. 打開 DevTools → Network → WS → 找到 `tutor_reply` 訊息，確認 payload 含 `"stage_id"`

- [ ] **Step 4: 測試章節隔離**

1. 答題進入第 2 章
2. 在第 2 章問一個不同問題
3. 確認：面板只顯示第 2 章的問答（不含第 1 章的問題）

- [ ] **Step 5: 測試頁面重整恢復**

1. 第 2 章問完問題後按 F5
2. Session 恢復後，確認「想追問老師」面板中第 2 章的問答記錄仍在
3. DevTools → Network → WS → 找到 `session_snapshot`，確認 payload 含 `"tutor_histories"` 欄位且有資料

- [ ] **Step 6: 確認 DB 有資料**

```bash
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/backend
.venv/Scripts/python.exe -c "
import asyncio, sys
sys.path.insert(0, '..')
from backend.db.database import init_db, get_db, close_db
async def check():
    await init_db('../data/learning.db')
    db = await get_db()
    async with db.execute('SELECT id, session_id, stage_id, question, in_scope FROM tutor_records LIMIT 5') as cur:
        rows = await cur.fetchall()
    for r in rows:
        print(dict(r))
    await close_db()
asyncio.run(check())
"
```

預期：列出剛才問答的記錄，`in_scope` 為 0 或 1。
