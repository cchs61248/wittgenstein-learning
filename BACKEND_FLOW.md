# 後端流程詳解

> 適用版本：2026-04 master 分支（Phase 1–4 完整實作，最後更新：2026-04-29，含 retry/remediate 分離、補強文章串流、持久化修正、ask_tutor 前端持久化）

---

## 目錄

1. [系統架構概覽](#1-系統架構概覽)
2. [啟動流程](#2-啟動流程)
3. [資料庫 Schema](#3-資料庫-schema)
4. [記憶體系統三層](#4-記憶體系統三層)
5. [REST API 端點](#5-rest-api-端點)
6. [WebSocket 訊息協定](#6-websocket-訊息協定)
7. [完整學習流程](#7-完整學習流程)
   - 7.1 [上傳檔案](#71-上傳檔案)
   - 7.2 [啟動新會話（start_session）](#72-啟動新會話start_session)
   - 7.3 [確認知識地圖（confirm_map）](#73-確認知識地圖confirm_map)
   - 7.4 [教學單一 Stage（run_stage）](#74-教學單一-stagerun_stage)
   - 7.5 [提交答案（submit_answer）](#75-提交答案submit_answer)
   - 7.6 [進度決策（_make_progress_decision）](#76-進度決策_make_progress_decision)
   - 7.7 [恢復會話（resume_session）](#77-恢復會話resume_session)
   - 7.8 [學生提問（ask_tutor）](#78-學生提問ask_tutor)
8. [七個核心元件詳解](#8-七個核心元件詳解)
9. [LLM 抽象層與 Provider](#9-llm-抽象層與-provider)
10. [設定與環境變數](#10-設定與環境變數)

---

## 1. 系統架構概覽

```
前端 (React + Zustand)
    │
    ├── REST  → /auth/*, /upload, /sessions/active
    │
    └── WebSocket → /ws/{session_id}?token=JWT
                        │
                   main.py（WebSocketManager）
                        │
               LearningOrchestrator
                        │
          ┌─────────────┼──────────────────┬────────────────┐
          │             │                  │                │
   ContentSplitter   ContextBuilder    TeacherAgent    DriftVerifier
   Agent             │                 (+ teaching     Agent
          │          ├─ allowed_evidence  intent)          │
          │          ├─ learner_state         │         verify ◄─┤
          │          ├─ mastery_map       QuestionGen       │    │
          │          └─ misconceptions    Agent             │    │
          │                                  │              │    │
          └──── EvaluatorAgent ──────────────┘              │    │
                        │ misconception_patterns             │    │
               ProgressManagerAgent ◄──────────────────────┘    │
                        │ (high_severity / repeated_patterns)     │
          ┌─────────────┼──────────────────┐                     │
          │             │                  │                     │
    WorkingMemory  SessionMemory     LongtermMemory              │
    (in-process)   (SQLite)          (SQLite)                    │
    current_teaching_intent           concept_mastery            │
                   source_chunks ─────────────────────────►──────┘
                   (source truth)
```

**Source Truth 架構**：後端掌控所有原文（`source_chunks` 表），LLM 只做語義切分與推理，不生成原文引用。

**單 process 內的狀態**：
- `_orchestrators: dict[str, LearningOrchestrator]` — 以 `session_id` 為鍵
- `WorkingMemory._store: dict[str, WorkingMemory]` — 以 `session_id` 為鍵

**WebSocketManager 多裝置管理**：
- 同一用戶新連線進來時，舊連線收到 `kicked` 訊息後被強制關閉（code 4002）

**跨重啟的持久狀態**：全部存於 SQLite（`data/learning.db`）

---

## 2. 啟動流程

```
uvicorn run:app --port 8000
    │
    ├── run.py → 將上層目錄加入 sys.path，讓 backend.* 匯入正常
    │
    └── lifespan(app)
            ├── init_db(DB_PATH)           # 建立 DB 連線、執行 migrations
            └── （應用結束）close_db()      # 關閉 DB 連線
```

### 資料庫 Migration 流程

| Migration | 內容 |
|-----------|------|
| 001 (SQL) | 建立 `users`、`sessions`、`stage_progress`、`qa_records`、`concept_mastery`、`user_learning_profile` 六張表 |
| 002 (Python) | `ALTER TABLE sessions ADD COLUMN stages_json` |
| 003 (Python) | `ALTER TABLE stage_progress ADD COLUMN full_explanation` |
| 004 (Python) | `ALTER TABLE stage_progress ADD COLUMN questions_json` |
| 005 (Python) | `ALTER TABLE sessions ADD COLUMN pending_map_json` |
| 006 (Python) | 建立 `decision_records` 表（進度決策歷史，含策略快照） |
| 007 (Python) | `ALTER TABLE sessions ADD COLUMN provider_name`、`model_name` |
| 008 (SQL) | 建立 `source_chunks` 表（後端掌控的 source truth，Phase 1） |

Migration 002–005、007 均用 `try/except` 包裹，已存在欄位時靜默跳過（冪等）。006 使用 `CREATE TABLE IF NOT EXISTS`。008 使用 `CREATE TABLE IF NOT EXISTS`。

---

## 3. 資料庫 Schema

### `users`
```
user_id       TEXT PRIMARY KEY
email         TEXT UNIQUE NOT NULL
password_hash TEXT NOT NULL
created_at    TIMESTAMP
```

### `sessions`
```
session_id          TEXT PRIMARY KEY
user_id             TEXT → users.user_id
content_hash        TEXT                  # 內容指紋，取 sha256[:16]
total_stages        INTEGER
current_stage_id    INTEGER DEFAULT 0
status              TEXT                  # pending_confirmation | active | completed | abandoned
raw_content_summary TEXT                  # 材料一句話摘要
stages_json         TEXT DEFAULT '[]'     # JSON 陣列，完整 stage 定義（含動態節點）
pending_map_json    TEXT DEFAULT NULL     # JSON，{nodes, summary}，確認後清除
provider_name       TEXT DEFAULT NULL     # 記錄本 session 使用的 LLM provider
model_name          TEXT DEFAULT NULL     # 記錄本 session 使用的 model（NULL 表示預設）
question_mode       TEXT DEFAULT 'short_answer'
created_at          TIMESTAMP
updated_at          TIMESTAMP
```

### `source_chunks`（Phase 1 新增）
```
chunk_id      TEXT NOT NULL              # 文件層級，格式：chunk_NNNN（非 stage 前綴）
session_id    TEXT NOT NULL → sessions.session_id
order_index   INTEGER NOT NULL           # 在原始文件中的順序
text          TEXT NOT NULL              # 原文（逐字，後端掌控）
section_title TEXT                       # 所屬段落標題（若有）
page          INTEGER                    # 頁碼（PDF 用）
char_start    INTEGER                    # 字元起點（於原始純文字）
char_end      INTEGER                    # 字元終點
created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
PRIMARY KEY (chunk_id, session_id)
INDEX: idx_source_chunks_session ON source_chunks(session_id)
```

### `stage_progress`
```
id                  INTEGER PK AUTOINCREMENT
session_id          TEXT → sessions.session_id
stage_id            INTEGER
status              TEXT      # pending | in_progress | completed
attempts            INTEGER
best_score          REAL
understanding_notes TEXT      # JSON，{confused: [...], dynamic?: bool, focus?: [...]}
completed_at        TIMESTAMP
full_explanation    TEXT      # 本 stage 的完整講解文字（含 Markdown）
questions_json      TEXT      # JSON 陣列，本 stage 的所有問題定義
UNIQUE(session_id, stage_id)
```

### `qa_records`
```
id            INTEGER PK AUTOINCREMENT
session_id    TEXT
stage_id      INTEGER
question_id   TEXT
question_text TEXT
question_type TEXT     # apply | understand | create
user_answer   TEXT
score         REAL
feedback      TEXT
created_at    TIMESTAMP
```

### `concept_mastery`
```
user_id              TEXT → users.user_id
concept_name         TEXT
mastery_score        REAL    # 0.0～1.0，EMA（α=0.3）計算
total_exposures      INTEGER
confusion_patterns   TEXT    # JSON 陣列，最多 5 筆；Phase 3+ 存結構化 dict：
                             # [{concept, pattern, student_evidence, severity, repair_strategy}]
                             # 舊版相容：字串列表仍可讀取
successful_analogies TEXT    # JSON 陣列，最多 5 筆；高分且有類比時寫入
last_tested          TIMESTAMP
UNIQUE(user_id, concept_name)
```

### `user_learning_profile`
```
user_id                TEXT PK → users.user_id
preferred_style        TEXT DEFAULT 'concrete'
avg_attempts_per_stage REAL DEFAULT 1.5
strong_domains         TEXT DEFAULT '[]'
weak_domains           TEXT DEFAULT '[]'
optimal_stage_length   INTEGER DEFAULT 500
updated_at             TIMESTAMP
```

### `decision_records`
```
id                      INTEGER PK AUTOINCREMENT
session_id              TEXT NOT NULL
stage_id                INTEGER NOT NULL
decision                TEXT NOT NULL     # advance | retry | remediate | reteach
best_score              REAL NOT NULL
next_stage_id           INTEGER NULL
next_stage_score        REAL NULL
reason_lines_json       TEXT DEFAULT '[]'
strategy_snapshot_json  TEXT DEFAULT '{}'
                        # 含：mastery_map、weak_concepts、score_trend、
                        #     next_stage_candidates、remediation_focus、
                        #     selection_reason（Phase 4）、
                        #     high_severity_misconceptions（Phase 4）、
                        #     repeated_patterns_detected（Phase 4）
created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

每個 session 最多保留 200 筆決策歷史（`DECISION_HISTORY_MAX_PER_SESSION = 200`）。

---

## 4. 記憶體系統三層

### 4.1 WorkingMemory（in-process）

**位置**：`backend/memory/working_memory.py`
**生命週期**：process 存活期間，重啟即消失

```python
@dataclass
class TurnContext:
    turn_id: str
    question_id: str
    question_text: str
    user_answer: str | None
    evaluation: dict | None
    clarification_rounds: int
    created_at: datetime

@dataclass
class WorkingMemory:
    session_id: str
    current_stage_id: int = 0
    stages: list[dict] = ...             # 整份 stage 列表，session 期間可增長
    current_turn: TurnContext | None = None
    stage_turns: list[TurnContext] = ...
    pending_questions: list[dict] = ...
    current_explanation: str = ""
    stage_evaluations: list[dict] = ...
    current_attempt: int = 1
    source_corpus: str = ""              # 全部 stages 的原文語料庫
    question_mode: str = "short_answer"
    enrichment_stage_added: bool = False
    current_teaching_intent: dict | None = None  # Phase 3：TeacherAgent 講解後提取
                                                  # {reinforced_concepts, analogies_used,
                                                  #  repair_target, main_chunk_ids}
```

關鍵方法：
- `get_compressed_history(max_turns=3)` — 取最近 3 輪 Q/A，供 EvaluatorAgent 使用
- `record_completed_turn()` — 將 `current_turn` 移入 `stage_turns`
- `reset_for_new_stage(stage_id)` — 切換 stage 時清空所有狀態，包含 `current_teaching_intent = None`

### 4.2 SessionMemory（SQLite）

**位置**：`backend/memory/session_memory.py`

主要函式：

| 函式 | 說明 |
|------|------|
| `create_pending_session(...)` | 建立 `status='pending_confirmation'` 的 session |
| `activate_pending_session(session_id)` | 將 status 改為 `active`，清空 `pending_map_json` |
| `get_user_active_session(user_id)` | 查詢最新的 active / pending_confirmation session |
| `get_session(session_id)` | 依 ID 查單筆 session |
| `store_stages(session_id, stages)` | 更新 `stages_json` |
| `get_stage_statuses(session_id)` | 取得 `{stage_id: status}` 字典 |
| `insert_source_chunks(session_id, chunks)` | 批次插入 source_chunks（Phase 1） |
| `get_source_chunks(session_id, chunk_ids?)` | 依 session_id（可選 chunk_id 列表）查詢原文（Phase 1） |
| `get_recent_qa_summary(session_id, max_items=5)` | 取近期答題摘要列表（Phase 2） |
| `get_last_decision_record(session_id)` | 取最後一筆決策記錄（Phase 2） |
| `store_stage_explanation(...)` | 寫入 `full_explanation` |
| `get_stage_explanation(...)` | 讀取 `full_explanation` |
| `get_all_stage_explanations(session_id)` | 全量讀取所有 stage 講解 |
| `store_stage_questions(...)` | 寫入 `questions_json` |
| `get_stage_questions(...)` | 讀取 `questions_json` |
| `get_stage_qa_records(...)` | 查詢本 stage 所有已答記錄 |
| `get_all_stage_qa_records(session_id)` | 查詢所有 stage 答題記錄，按 stage_id 分組 |
| `insert_qa_record(...)` | 插入一筆答題記錄 |
| `upsert_stage_progress(...)` | INSERT OR UPDATE stage_progress |
| `update_current_stage(...)` | 更新 sessions.current_stage_id |
| `complete_session(session_id)` | 設定 status='completed' |
| `insert_decision_record(...)` | 插入進度決策歷史，超過上限時刪除最舊筆 |
| `get_decision_records(session_id)` | 取得 session 的決策歷史列表 |

### 4.3 LongtermMemory（SQLite）

**位置**：`backend/memory/longterm_memory.py`

主要函式：

| 函式 | 說明 |
|------|------|
| `get_user_profile_summary(user_id)` | 回傳偏好風格 + 平均嘗試次數（字串） |
| `get_weak_concepts(user_id)` | 取 mastery_score < 0.6 的概念，以「、」分隔 |
| `get_concept_mastery_map(user_id, concepts)` | 批次查詢指定概念掌握度，回傳 `{概念名: 分數}` 字典（Phase 2） |
| `get_misconceptions(user_id, concepts)` | 取結構化混淆模式列表（Phase 2），相容舊版字串格式 |
| `update_concept_mastery(user_id, concept_name, new_score, ...)` | EMA 更新；Phase 3 新增 `misconception_pattern`（dict）、`analogy_used`、`lesson_was_effective` 參數 |
| `update_user_profile(user_id, attempts)` | EMA（α=0.2）更新平均嘗試次數 |

`update_concept_mastery` 邏輯：
- EMA 公式：`new_score = 0.7 * old + 0.3 * latest`
- `misconception_pattern`（Phase 3）：按 `pattern` 字串去重後 append，保留最近 5 筆
- `lesson_was_effective` + `analogy_used`（Phase 3）：高分且有類比時 append `successful_analogies`，保留最近 5 筆
- 舊版 `confused_concepts`（字串列表）維持相容，但優先使用結構化 `misconception_pattern`

---

## 5. REST API 端點

### `POST /auth/register`
- 接收：`{email, password}`，建立 user，bcrypt 密碼雜湊
- 回傳：`{token: JWT, user_id, email}`

### `POST /auth/login`
- 接收：`{email, password}`，驗證後簽發 JWT
- 回傳：`{token: JWT, user_id, email}`

### `GET /auth/me?token=...`
- 解碼 JWT，回傳當前使用者資訊

### `POST /upload`（需 `Authorization: Bearer <token>`）
- 接收：multipart/form-data，欄位 `file`
- 將原始 bytes + filename + mime_type 存入 `backend/files/upload_store.py`（process 記憶體）
- 回傳：`{file_id: UUID}`

### `GET /sessions/active?token=...`
- 查詢該用戶最新的 active / pending_confirmation session
- 若 pending：回傳 `pending_map`（nodes + summary）
- 若 active：回傳 `stage_statuses`、`stages`、`provider`、`model`

### `GET /health`
- 回傳 `{"status": "ok"}`

---

## 6. WebSocket 訊息協定

連線位址：`ws://localhost:8000/ws/{session_id}?token=JWT`

### 客戶端 → 伺服器

| 訊息 type | 必要欄位 | 說明 |
|-----------|----------|------|
| `start_session` | `uploaded_file_id`，`provider`，`target_depth`，`question_mode?`，`model?` | 啟動新學習會話 |
| `confirm_map` | `provider?`，`model?` | 確認知識地圖，開始教學 |
| `submit_answer` | `session_id`，`question_id`，`answer` | 提交答案 |
| `resume_session` | `session_id`，`provider?`，`model?` | 重整後恢復 session |
| `ask_tutor` | `question` | 學生對教材提出問題 |

### 伺服器 → 客戶端

| 訊息 type | 重要欄位 | 說明 |
|-----------|----------|------|
| `kicked` | `message` | 同帳號在其他裝置登入，此連線被強制關閉（code 4002） |
| `knowledge_map` | `nodes`，`summary` | LLM 切割完成，等待確認 |
| `session_started` | `session_id`，`total_stages`，`stages`，`stage_statuses?` | 會話啟動、恢復或 advance 後刷新 |
| `session_snapshot` | `stage_explanations`，`stage_qa_histories`，`decision_history` | `resume_session` 時全量推送 |
| `explanation_chunk` | `chunk: str`，`is_final: bool` | 串流講解片段 |
| `explanation_complete` | `stage_id`，`stage_title`，`full_explanation` | 本 stage 講解完成 |
| `explanation_reset` | — | 清空前端講解區 |
| `question` | `question_id`，`text`，`type`，`answer_mode`，`options`，`evidence_chunk_ids`，`stage_id`，`attempt_number` | 發送一道題目 |
| `feedback` | `question_id`，`score`，`feedback_text`，`needs_clarification`，`clarification_question?` | 評分結果 |
| `stage_decision` | `decision`，`message`，`next_stage_id?`，`best_score`，`reason_lines`，`strategy_snapshot` | 進度決策；`strategy_snapshot` 含 selection_reason（Phase 4） |
| `qa_history` | `records` | `_resume_from_stored` 時推送歷史答題 |
| `resume_state` | `current_question?`，`last_feedback?` | `_resume_from_stored` 時推送 |
| `tutor_reply` | `question`，`answer`，`in_scope` | 回應 `ask_tutor` |
| `course_completed` | `message` | 所有 stage 完成 |
| `error` | `message` | 錯誤通知 |

---

## 7. 完整學習流程

### 7.1 上傳檔案

```
前端 POST /upload（multipart）
    │
    └── upload_store.py
            ├── 產生 file_id（UUID）
            └── 儲存 {filename, mime_type, raw: bytes}（process 記憶體）

前端收到 {file_id}
    └── 送 start_session 時帶入 uploaded_file_id
```

### 7.2 啟動新會話（start_session）

```
前端 WebSocket send: {type: "start_session", payload: {uploaded_file_id, provider, target_depth, ...}}
    │
    main.py
        ├── 驗證 JWT
        ├── load_upload(file_id)                    # 從 upload_store 取原始 bytes
        │
        ├── 【Phase 1：後端 Source Truth 建立】
        │   text_extractor.extract_text(filename, raw_bytes)
        │       ├── .txt / .md → decode utf-8
        │       ├── .pdf       → pdfplumber（保留段落）
        │       ├── .docx      → python-docx（保留 heading）
        │       └── 其他       → utf-8 fallback
        │   chunker.build_source_chunks(text, session_id)
        │       ├── 優先按結構切（Wittgenstein 命題編號、Markdown 標題、Word heading）
        │       ├── 無結構則按段落 + 大小限制（目標 500–800 字，max 1000）
        │       └── 每個 chunk：{chunk_id: "chunk_NNNN", text, order_index,
        │                       section_title, page, char_start, char_end}
        │
        ├── create_provider(provider_name, model?)
        ├── LearningOrchestrator(llm)
        └── orchestrator.start_session(source_chunks=chunks, ...)
                │
                ├── ContentSplitterAgent.run(ctx)
                │       輸入：source_chunks（後端已切好的 chunk 列表）
                │       LLM 只做語義切分，不生成原文引用
                │       回傳：{stages（含 source_chunk_ids），chunk_roles，summary}
                │
                ├── 後端回填 source_chunks 至每個 stage：
                │       stage["source_chunks"] = [
                │           {"chunk_id": cid, "quote": db_chunks[cid]["text"]}
                │           for cid in stage["source_chunk_ids"]
                │       ]
                │
                ├── _check_stage_quality(stages, all_chunks)
                │       檢查：過小節點 / 概念碎片化 / 孤立 chunk
                │       僅 log warning，不阻擋流程
                │
                ├── session_memory.create_pending_session(...)
                │       status = 'pending_confirmation'
                │
                ├── session_memory.insert_source_chunks(session_id, chunks)
                │       將後端 source truth 持久化至 DB
                │
                └── emit: {type: "knowledge_map", payload: {nodes, summary}}
```

### 7.3 確認知識地圖（confirm_map）

```
前端 WebSocket send: {type: "confirm_map", payload: {provider?, model?}}
    │
    main.py
        ├── 若 _orchestrators 中找不到（重整後遺失）：
        │       從 DB 讀取 provider_name / model_name，重建 LLM + Orchestrator
        │
        └── orchestrator.confirm_session(session_id, user_id, emit)
                │
                ├── session_memory.activate_pending_session(session_id)
                ├── session_memory.store_stages(session_id, stages)
                ├── 為每個 stage upsert_stage_progress(status='pending')
                ├── WorkingMemory 初始化：
                │       wm.stages = stages
                │       wm.enrichment_stage_added = ...
                │       wm.reset_for_new_stage(0)
                │
                ├── emit: {type: "session_started", ...}
                │
                └── run_stage(session_id, user_id, stages, stage_index=0, ...)
```

### 7.4 教學單一 Stage（run_stage）

```
run_stage(session_id, user_id, stages, stage_index, question_mode, emit)
    │
    ├── wm.reset_for_new_stage(stage_id)
    ├── wm.source_corpus = 所有 stages 全文語料庫
    ├── session_memory.update_current_stage(...)
    ├── session_memory.upsert_stage_progress(status='in_progress')
    ├── longterm_memory.get_user_profile_summary(user_id)
    │
    ├── 【Phase 2：組裝完整學生狀態包（ContextBuilder）】
    │   adaptive_ctx = build_adaptive_context(
    │       session_id, user_id, stage, current_attempt=1, stages
    │   )
    │   ├── get_source_chunks(session_id, chunk_ids)  → allowed_evidence（真實原文）
    │   ├── get_concept_mastery_map(user_id, key_concepts)  → mastery_map
    │   ├── get_misconceptions(user_id, key_concepts)  → misconceptions（結構化）
    │   ├── get_recent_qa_summary(session_id)  → recent_qa_summary
    │   ├── get_last_decision_record(session_id)  → selection_reason（Phase 4）
    │   ├── 計算 forbidden_future_concepts（後續節點的概念）
    │   └── 計算 must_reinforce（mastery < 0.75 的概念）
    │
    ├── 【步驟 1】emit: explanation_chunk（進度表 Markdown）
    │
    ├── 【步驟 2】串流講解（TeacherAgent）
    │   TeacherAgent.stream_explanation(ctx)
    │       task_payload: {stage, adaptive_context: adaptive_ctx, prev_stage_title}
    │       system prompt 包含：
    │           學生掌握度、混淆模式、最近答題、
    │           must_reinforce、forbidden_future、selection_reason（Phase 4）
    │       每個 chunk → emit: explanation_chunk（is_final=False）
    │
    │   DriftVerifierAgent.run(...)（citation accuracy 驗證，Phase 4）
    │       ├── _extract_cited_chunks()：提取 [chunk_id] 並配對原文
    │       ├── LLM 逐條驗證每個引用是否確實支撐對應主張
    │       ├── 後端強制：found=False 的 chunk_id → claim_check.supported=False
    │       ├── 回傳 claim_checks + unsupported_claims
    │       └── 若 aligned=False → 重新生成講解（附 revision_hint）
    │   wm.current_explanation = full_explanation
    │   session_memory.store_stage_explanation(...)
    │
    ├── 【步驟 3】提取教學意圖（Phase 3）
    │   teaching_intent = await teacher.extract_teaching_intent(full_explanation, stage)
    │       # non-streaming，解析講解全文，輸出：
    │       # {reinforced_concepts, analogies_used, repair_target, main_chunk_ids}
    │   wm.current_teaching_intent = teaching_intent
    │
    ├── 【步驟 4】生成問題（QuestionGeneratorAgent）
    │   task_payload: {stage, teaching_intent, allowed_evidence, num_questions, ...}
    │       ├── 問題與 teaching_intent.repair_target 對齊（至少 1 題）
    │       ├── 問題與 analogies_used 對齊（至少 1 題）
    │       └── evidence 優先用 allowed_evidence（真實 DB 原文）
    │   DriftVerifierAgent 驗證（aligned=False → 重新生成）
    │   wm.pending_questions = questions
    │   session_memory.store_stage_questions(...)
    │
    ├── 【步驟 5】emit: explanation_chunk（問題區塊 + is_final=True）
    ├──           emit: explanation_complete
    │
    └── 【步驟 6】emit: question（第一道題目）
```

### 7.5 提交答案（submit_answer）

```
前端 WebSocket send: {type: "submit_answer", payload: {question_id, answer}}
    │
    orchestrator.handle_answer(...)
        │
        ├── 驗證 current_turn.question_id == question_id
        │
        ├── 【評分】EvaluatorAgent.run(ctx)
        │   回傳（Phase 3 新增 misconception_patterns）：
        │   {
        │     score, understood_concepts, confused_concepts,
        │     misconception_patterns: [           ← Phase 3
        │       {concept, pattern, student_evidence, severity, repair_strategy}
        │     ],
        │     feedback, needs_clarification, clarification_question
        │   }
        │   評分邊界（Phase 2）：只依 evidence_chunks 評估，教材外知識不加分
        │
        ├── wm.record_completed_turn()
        ├── session_memory.insert_qa_record(...)
        ├── emit: feedback
        │
        ├── 更新 LongtermMemory（Phase 3 升級）
        │   mastery_score = EMA(raw_score)（選擇題先套猜測校正）
        │   analogies_to_record = wm.current_teaching_intent.analogies_used
        │       if mastery_score >= 0.8 and teaching_intent exists else []
        │   for concept in stage.key_concepts:
        │       mp = misconception_patterns[concept]（若有）
        │       effective = (concept in understood_concepts) and analogies_to_record
        │       update_concept_mastery(
        │           misconception_pattern=mp,         ← 結構化存入 confusion_patterns
        │           analogy_used=analogies[0],        ← 若 effective，存入 successful_analogies
        │           lesson_was_effective=effective,
        │       )
        │
        ├── 若有未答問題 → emit: question（下一題）
        │
        └── 若全部回答完 → _make_progress_decision(...)
```

### 7.6 進度決策（_make_progress_decision）

```
_make_progress_decision(...)
    │
    ├── ProgressManagerAgent.run(ctx)
    │   【純程式邏輯，不呼叫 LLM】
    │
    │   ⚠️  attempts 來源：task_payload["current_attempt"]（第幾輪嘗試）
    │       不可用 len(evaluations)（當輪已答題目數，stage_evaluations 每輪重置）
    │       Orchestrator 必須傳入 "current_attempt": wm.current_attempt
    │
    │   從 evaluations 收集 misconception_patterns：
    │       all_misconceptions = [m for ev in evaluations for m in ev.misconception_patterns]
    │       high_severity = [m for m in all_misconceptions if m.severity == "high"]
    │       repeated_patterns = _detect_repeated_patterns(evaluations)
    │           # 同一 pattern 字串出現 >= 2 次 → True
    │
    │   決策優先序（Phase 4 調整）：
    │   1. best_score >= 0.75           → advance
    │   2. high_severity 存在           → reteach（根本誤解，立即換框架）
    │   3. repeated_patterns = True     → reteach（同一錯誤重複）
    │   4. attempts < max_attempts      → retry
    │   5. attempts == max_attempts
    │      AND latest_score < 0.5      → reteach
    │   6. otherwise                   → remediate
    │
    │   回傳：{decision, message, best_score, remediation_focus,
    │          high_severity_misconceptions, repeated_patterns_detected}
    │
    ├── stable_high = _is_stable_high_performance(wm.stage_evaluations)
    │       條件：評分筆數 >= 2 且 min(scores) >= 0.8 且 avg >= 0.87
    │
    ├── 【advance】
    │   completed_stage_ids.add(current)
    │   mastery_map = get_concept_mastery_map(user_id, all_concepts)
    │   _pick_next_stage_index(stages, current_idx, completed, weak, mastery, stable_high)
    │       _rank_next_stage_candidates(...)：
    │           stable_high → unseen*3.0 + low_mastery*1.2 + weak_overlap*0.8
    │                          - mastered*1.5 - distance_penalty
    │           一般        → weak_overlap*3.0 + low_mastery*2.2 + unseen*0.8
    │                          - mastered*0.5 - distance_penalty
    │   若 next_stage_idx is None 且 stable_high 且尚未加 enrichment：
    │       _insert_enrichment_stage(...)（E.N 節點）
    │
    │   【Phase 4：建立選課理由】
    │   selection_reason = {
    │       reason: "弱點重疊度=N，低掌握概念數=M，模式",
    │       target_concepts: [掌握度 < 0.75 的概念],
    │       stable_high: bool,
    │   }
    │
    ├── 【remediate / reteach】
    │   若有 remediation_focus 且無候選節點：
    │       _insert_remediation_stage(...)（R.N 補強節點）
    │
    ├── session_memory.upsert_stage_progress(...)
    ├── strategy_snapshot = {
    │       ...，
    │       selection_reason,             ← Phase 4
    │       high_severity_misconceptions, ← Phase 4
    │       repeated_patterns_detected,   ← Phase 4
    │   }
    ├── session_memory.insert_decision_record(...)
    ├── emit: stage_decision
    │
    ├── 【advance 後續】
    │   emit: session_started（更新 stage_statuses）
    │   run_stage(next_stage_idx, ...)
    │       selection_reason 透過 strategy_snapshot → get_last_decision_record
    │       → build_adaptive_context → next_lesson_requirements
    │       → TeacherAgent prompt 的 selection_reason_text 欄位
    │
    ├── 【retry 後續】
    │   wm.current_attempt += 1
    │   ⚠️  不送 explanation_reset（保留原文於前端）
    │   在 wm.current_explanation 尾端附加「### 🔄 第 N 次嘗試」標題 + decision.message
    │   QuestionGeneratorAgent 重新出題（新題目）
    │   session_memory.store_stage_explanation(combined)  ← 累積講解持久化
    │   session_memory.store_stage_questions(questions)
    │   emit: explanation_complete（full_explanation = 累積講解文字）
    │   emit: question（第一道新題）
    │
    ├── 【remediate 後續】
    │   wm.current_attempt += 1
    │   ⚠️  不送 explanation_reset（保留原文於前端）
    │   在 wm.current_explanation 尾端附加補強標題（含 focus 概念）
    │   build_adaptive_context()（重新取 learner_state，current_attempt 已遞增）
    │   TeacherAgent.stream_explanation（stage.content 附「補強模式」指示）→ 串流補強文章
    │   extract_teaching_intent → wm.current_teaching_intent（更新）
    │   QuestionGeneratorAgent（帶新 teaching_intent 與 allowed_evidence）
    │   session_memory.store_stage_explanation(combined)  ← 原文 + 補強文章一起持久化
    │   session_memory.store_stage_questions(questions)
    │   emit: explanation_complete
    │   emit: question（第一道新題）
    │
    └── 【reteach 後續】
        wm.current_attempt += 1
        session_memory.store_stage_explanation(current_explanation)  ← 換框架前先存舊版
        session_memory.store_stage_questions(current_questions)      ← 換框架前先存舊版
        emit: explanation_complete（將舊版通知前端，防止 resume 重新生成）
        rebuild adaptive_ctx（current_attempt 已遞增）
        TeacherAgent.stream_explanation（附「換框架」指引）
        extract_teaching_intent → wm.current_teaching_intent（更新）
        QuestionGeneratorAgent（帶新 teaching_intent）
        emit: 新講解 + 新問題
```

**五種決策觸發條件（Phase 4 更新）**：

| 決策 | 觸發條件 | 優先序 |
|------|----------|--------|
| `advance` | best_score ≥ 0.75 | 1（最高） |
| `reteach` | high severity misconception（任何嘗試次數） | 2 |
| `reteach` | 同一 pattern 重複 ≥ 2 次 | 3 |
| `retry` | attempts < max_attempts | 4 |
| `reteach` | attempts == max_attempts AND latest < 0.5 | 5 |
| `remediate` | 其餘情況 | 6（最低） |

**動態節點類型**：

| 類型 | node_id | 觸發時機 |
|------|---------|---------|
| 補強節點 | `R.N` | remediate/reteach 且無現成弱點節點 |
| 整合挑戰節點 | `E.N` | advance 且所有原始節點已完成 + stable_high |

### 7.7 恢復會話（resume_session）

```
前端重整 → GET /sessions/active → 若 active → WebSocket send: resume_session
    │
    orchestrator.resume_session(session_id, user_id, emit)
        ├── 從 DB 讀取 stages、stage_statuses
        ├── wm.stages = stages
        ├── emit: session_started + session_snapshot（全量恢復）
        ├── 取 current stage 的 stored_explanation
        ├── 若有 stored_explanation：
        │       _resume_from_stored(...)
        │           emit: explanation + 問題區塊
        │           查詢 qa_records，找出 unanswered
        │           emit: qa_history + resume_state + question（若有未答題）
        │           若全部已答：重建 stage_evaluations → _make_progress_decision
        └── 若無：run_stage(...)（完整重新執行）
```

### 7.8 學生提問（ask_tutor）

```
前端 WebSocket send: {type: "ask_tutor", payload: {question: "..."}}
    │
    orchestrator.handle_student_question(...)
        ├── 取 wm.source_corpus
        ├── 【範疇判斷】LLM（scope_judge prompt）→ {in_scope: bool}
        ├── 若 in_scope=False → search_web(question, max_results=3)（DuckDuckGo）
        └── 【生成回答】LLM（tutor_reply prompt）
            emit: tutor_reply（question, answer, in_scope）

前端收到 tutor_reply：
    ├── addTutorMessage(msg) → 追加至 Zustand tutorHistory 陣列
    ├── localStorage.setItem('wl_tutor_history', JSON.stringify(updated))
    └── AskTutorPanel 顯示可收縮筆記（HistoryNote 元件，最新展開）
        ├── 每筆記錄顯示：問題摘要 + in_scope 標籤 + ReactMarkdown 回答
        └── 整份 tutorHistory 在頁面重整、重新登入後自動恢復
```

---

## 8. 七個核心元件詳解

所有 Agent 繼承 `BaseAgent`，`_messages` 在 `run()` 開始與結束時都呼叫 `_reset()` 清空，防止跨呼叫上下文污染。

### 8.1 ContentSplitterAgent

**職責**：將後端 source_chunks 切割為邏輯 stage 序列

**呼叫時機**：`start_session`

**Token 預算**：`max_context_tokens=4000`

**輸入（Phase 1 改版）**：
- `source_chunks: list[dict]` — 後端已切好的 chunk 列表（含 chunk_id、text、order_index）
- `max_stages: int = 8`
- `target_depth: str` — beginner / intermediate / advanced

**Prompt 重點（Phase 1）**：
- LLM 只做語義切分，**不生成任何引用文字**
- 回傳 `source_chunk_ids`（引用後端 chunk_id），不生成 quote
- 標記 `chunk_roles`：core / example / transition / ignored
- 不要把每個 chunk 都變成一個 stage（語義合併）

**輸出 JSON**：
```json
{
  "stages": [{
    "stage_id": 1, "node_id": "1.1", "title": "...",
    "source_chunk_ids": ["chunk_0001", "chunk_0002"],
    "key_concepts": ["概念A", "概念B"],
    "estimated_questions": 3,
    "teaching_goal": "一句話教學目標"
  }],
  "chunk_roles": {"chunk_0001": "core", "chunk_0003": "ignored"},
  "summary": "一句話摘要"
}
```

Orchestrator 在切割完成後**後端回填**：
```python
stage["source_chunks"] = [{"chunk_id": cid, "quote": db_chunks[cid]["text"]}
                           for cid in stage["source_chunk_ids"]]
```

### 8.2 ContextBuilder

**職責**：每次 TeacherAgent 呼叫前，組裝完整的學生狀態包（adaptive_context）

**位置**：`backend/orchestrator/context_builder.py`（Phase 2 新增）

**輸入**：session_id, user_id, stage, current_attempt, stages

**輸出**：
```python
{
    "stage": stage,
    "current_attempt": int,
    "allowed_evidence": [{"chunk_id", "text"},...],  # 真實 DB 原文
    "learner_state": {
        "mastery_map": {"概念A": 0.72, ...},
        "misconceptions": [{concept, pattern, severity, repair_strategy},...],
        "recent_qa_summary": [{question_text, score},...],
    },
    "next_lesson_requirements": {
        "must_reinforce": ["掌握度<0.75的概念"],
        "forbidden_future_concepts": ["後續節點才有的概念"],
        "selection_reason": {                  # Phase 4
            "reason": "弱點重疊度=N，...",
            "target_concepts": [...],
            "stable_high": bool,
        },
    },
    "source_constraints": {
        "must_cite_chunks": True,
        "no_external_claims": True,
        "forbidden_future_concepts": [...],
    },
}
```

### 8.3 TeacherAgent

**職責**：串流生成講解，並提取教學意圖

**呼叫時機**：`run_stage`（串流）、`remediate` 決策（串流）、`reteach` 決策（串流）

**輸入（Phase 2 升級）**：stage、adaptive_context（含 allowed_evidence、mastery_map 等）

**System Prompt 格式參數（Phase 2+4）**：
- `{user_profile_summary}` — 學習風格
- `{mastery_summary}` — 掌握度摘要（`概念=分數%` 格式）
- `{misconceptions_text}` — 結構化混淆模式
- `{recent_qa_text}` — 最近答題摘要
- `{must_reinforce_text}` — 必須補強的概念
- `{forbidden_future_text}` — 禁止提前教的概念
- `{selection_reason_text}` — 選課理由（Phase 4）

**重要限制（Prompt）**：每個核心敘述後標記 `[chunk_id]`，類比標示「（類比說明，非原文）」，禁止提及 forbidden_future 概念。

**extract_teaching_intent（Phase 3 新增）**：
- 串流結束後，非串流呼叫 LLM 分析講解全文
- 輸出：`{reinforced_concepts, analogies_used, repair_target, main_chunk_ids}`
- 存入 `wm.current_teaching_intent`，供 QuestionGeneratorAgent 使用

### 8.4 QuestionGeneratorAgent

**職責**：生成與教學意圖對齊的布魯姆式問題

**呼叫時機**：`run_stage`、`retry/remediate/reteach` 後重新出題

**輸入（Phase 3 升級）**：
- `stage` — 節點定義
- `teaching_intent` — TeacherAgent 提取的教學意圖（Phase 3）
- `allowed_evidence` — ContextBuilder 提供的 DB 原文（Phase 2）
- `num_questions`、`attempt_number`、`previous_question_ids`、`question_mode`

**Prompt 新增（Phase 3）**：
```
本篇文章的教學意圖：
- 補強概念：{reinforced_concepts}
- 使用的類比：{analogies_used}
- 修正目標：{repair_target}

→ 至少一題直接測試修正目標（若有）
→ 至少一題能檢驗學生是否理解文章使用的類比框架
```

**Evidence 來源**：優先 `allowed_evidence`（DB 真實原文，key: `text`），退回 `stage.source_chunks`（key: `quote`）

**num_questions**：
- `multiple_choice`：`max(4, estimated_questions * 2)`
- `short_answer`：`estimated_questions`（重試固定 2）

### 8.5 EvaluatorAgent

**職責**：評估學生答案，給分並提供結構化診斷

**呼叫時機**：每次 `submit_answer`

**評分邊界（Phase 2）**：只依 evidence_chunks 評估，教材外知識不加分。

**三條評分路徑**：

| 情境 | 處理 |
|------|------|
| 選擇題答對 | 不呼叫 LLM，直接回傳 score=1.0 |
| 選擇題答錯 | LLM 評分 0.0–0.6（依與正確答案的概念相近程度） |
| 短答題 | LLM 評分 0.0–1.0 |

所有路徑的 `feedback` 前面由 `_add_mastery_label()` 統一注入 `✅/⚠️/❌` 標籤。

**輸出（Phase 3 新增 misconception_patterns）**：
```json
{
  "score": 0.62,
  "understood_concepts": ["概念A"],
  "confused_concepts": ["概念B"],
  "misconception_patterns": [{
    "concept": "概念B",
    "pattern": "把因果方向搞反",
    "student_evidence": "學生說「...」",
    "severity": "medium",
    "repair_strategy": "下次用步驟式例子說明"
  }],
  "feedback": "⚠️ **掌握度部分不足**\n\n...",
  "needs_clarification": false,
  "clarification_question": null
}
```

### 8.6 ProgressManagerAgent

**職責**：決定學習進度策略

**特點**：不呼叫 LLM，純規則計算

**輸入**：stage_evaluations（含 misconception_patterns）、pass_threshold=0.75、max_attempts=3

**決策邏輯（Phase 4 升級）**：
```
all_misconceptions = evaluations[*].misconception_patterns（展開）
high_severity = [m for m if m.severity == "high"]
repeated = 同一 pattern 字串出現 >= 2 次

1. best_score >= 0.75              → advance
2. high_severity 存在              → reteach（根本誤解）
3. repeated patterns               → reteach（同一錯誤重複）
4. attempts < max_attempts         → retry
5. attempts == max_attempts
   AND latest_score < 0.5          → reteach
6. otherwise                       → remediate
```

**輸出**：`{decision, message, best_score, remediation_focus, high_severity_misconceptions, repeated_patterns_detected}`

### 8.7 DriftVerifierAgent

**職責**：驗證 LLM 生成的講解或問題是否紮根於原始教材，防止幻覺與錯誤引用

**呼叫時機**：TeacherAgent 串流完成後（explanation）、QuestionGeneratorAgent 完成後（questions）、reteach 重寫後

**Citation Accuracy 升級（Phase 4）**：

```python
def _extract_cited_chunks(candidate_text, source_chunks):
    # 正則提取所有 [chunk_id] 引用
    # 查詢 source_chunks 取對應原文
    # 回傳 [{chunk_id, text, found}]
```

傳給 LLM 的資料包含 `cited_chunks_lookup`（引用 id + 對應原文），LLM 可逐條驗證「主張是否確實被該 chunk 支撐」，而非只做形式引用檢查。

後端強制：`found=False` 的 chunk_id 一定標記為 `supported=False`，不依賴 LLM 偵測。

**輸入**（`task_payload`）：
- `content_type` — `"explanation"` 或 `"questions"`
- `source_chunks` — 本 stage 的 source_chunks
- `candidate_text` — 待驗證文字

**輸出（Phase 4 升級）**：
```json
{
  "aligned": false,
  "claim_checks": [{
    "claim": "條件機率的核心是在條件成立的集合中觀察目標",
    "cited_chunk_id": "chunk_0012",
    "supported": true,
    "issue": ""
  }, {
    "claim": "這可以直接推出貝氏定理",
    "cited_chunk_id": null,
    "supported": false,
    "issue": "教材未涵蓋貝氏定理"
  }],
  "unsupported_claims": ["這可以直接推出貝氏定理"],
  "issues": ["具體問題描述"],
  "missing_evidence": ["缺少來源的陳述"],
  "revision_hint": "移除貝氏定理相關內容"
}
```

若 `aligned=False`，Orchestrator 自動觸發一次重試（附 `revision_hint`）。

---

## 9. LLM 抽象層與 Provider

**位置**：`backend/llm/`

`BaseLLMProvider` 定義統一介面：
```python
async def chat(messages, system_prompt) -> LLMResponse
async def stream_chat(messages, system_prompt) -> AsyncGenerator[str, None]
```

### 各 Provider 差異

| Provider | system_prompt 傳遞方式 | 附件傳遞方式 |
|----------|----------------------|-------------|
| ClaudeProvider | 獨立 `system` 參數 | Anthropic Files API（fallback 用） |
| OpenAIProvider | messages[0] 插入 system role | OpenAI Files API（fallback 用） |
| GeminiProvider | `config.system_instruction` | google-genai files.upload（fallback 用） |
| MonicaProvider | OpenAI 相容格式 | inline base64 |

> Phase 1 後，Files API 降級為 fallback，主路徑改為後端 text_extractor + chunker。

### 工廠函式

```python
llm = create_provider("claude" | "openai" | "gemini" | "monica", model=None)
```

### System Prompt 一覽

| 鍵名 | 使用元件 |
|------|---------|
| `content_splitter` | ContentSplitterAgent |
| `teacher` | TeacherAgent（含 6 個格式參數 + selection_reason_text） |
| `question_generator` | QuestionGeneratorAgent |
| `evaluator` | EvaluatorAgent（含評分邊界 + misconception_patterns 指引） |
| `drift_verifier` | DriftVerifierAgent（含 cited_chunks_lookup 驗證規則） |
| `scope_judge` | handle_student_question（範疇判斷） |
| `tutor_reply` | handle_student_question（生成回答） |

---

## 10. 設定與環境變數

**設定檔**：`backend/.env`（由 `backend/config.py` 明確載入）

| 環境變數 | 預設值 | 說明 |
|----------|--------|------|
| `ANTHROPIC_API_KEY` | — | Claude API 金鑰 |
| `OPENAI_API_KEY` | — | OpenAI API 金鑰 |
| `GOOGLE_API_KEY` | — | Gemini API 金鑰 |
| `DEFAULT_PROVIDER` | `claude` | 預設 LLM Provider |
| `PASS_THRESHOLD` | `0.75` | 進階門檻分數 |
| `MAX_STAGE_ATTEMPTS` | `3` | 同一 stage 最多重試次數 |
| `DB_PATH` | `../data/learning.db` | 相對路徑以 backend/ 為基準解析 |
| `JWT_SECRET` | `change-me` | JWT 簽名密鑰 |

### 外部工具

| 工具 | 位置 | 說明 |
|------|------|------|
| `extract_text()` | `backend/utils/text_extractor.py` | 本地文件解析（PDF/DOCX/PPTX/MD/TXT），輸出純文字 |
| `build_source_chunks()` | `backend/utils/chunker.py` | 機械切分 + 結構優先（Wittgenstein 命題編號、Markdown 標題），輸出 chunk 列表 |
| `search_web()` | `backend/tools/web_search.py` | DuckDuckGo Instant Answer API（免 key），供 ask_tutor 使用 |
