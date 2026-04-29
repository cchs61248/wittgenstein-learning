# 後端流程詳解

> 適用版本：2026-04 當前 master 分支（最後更新：2026-04-29）

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
8. [六個 Agent 詳解](#8-六個-agent-詳解)
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
   ContentSplitter   Teacher          QuestionGenerator  DriftVerifier
   Agent             Agent            Agent             Agent
          │             │                  │                │
          └──── EvaluatorAgent ────────────┘                │
                        │              ◄─────── verify ─────┘
               ProgressManagerAgent
                        │
          ┌─────────────┼──────────────────┐
          │             │                  │
    WorkingMemory  SessionMemory     LongtermMemory
    (in-process)   (SQLite)          (SQLite)
```

**單 process 內的狀態**：
- `_orchestrators: dict[str, LearningOrchestrator]` — 以 `session_id` 為鍵，儲存每個 session 的 Orchestrator 實例
- `WorkingMemory._store: dict[str, WorkingMemory]` — 以 `session_id` 為鍵，儲存當前問答輪次狀態

**WebSocketManager 多裝置管理**：
- `_sid_to_ws: dict[str, WebSocket]` — session_id → WebSocket
- `_uid_to_sid: dict[str, str]` — user_id → 當前 session_id
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

`init_db` 執行的 migration 按版本順序：

| Migration | 內容 |
|-----------|------|
| 001 (SQL) | 建立 `users`、`sessions`、`stage_progress`、`qa_records`、`concept_mastery`、`user_learning_profile` 六張表 |
| 002 (Python) | `ALTER TABLE sessions ADD COLUMN stages_json` |
| 003 (Python) | `ALTER TABLE stage_progress ADD COLUMN full_explanation` |
| 004 (Python) | `ALTER TABLE stage_progress ADD COLUMN questions_json` |
| 005 (Python) | `ALTER TABLE sessions ADD COLUMN pending_map_json` |
| 006 (Python) | 建立 `decision_records` 表（進度決策歷史，含策略快照） |
| 007 (Python) | `ALTER TABLE sessions ADD COLUMN provider_name`、`model_name` |

002～005、007 均用 `try/except` 包裹，已存在欄位時靜默跳過，保證冪等。006 使用 `CREATE TABLE IF NOT EXISTS`。

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
created_at          TIMESTAMP
updated_at          TIMESTAMP
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
user_id             TEXT → users.user_id
concept_name        TEXT
mastery_score       REAL    # 0.0～1.0，EMA 計算
total_exposures     INTEGER
confusion_patterns  TEXT    # JSON 陣列，最多 10 個
successful_analogies TEXT   # JSON 陣列，最多 5 個
last_tested         TIMESTAMP
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

### `decision_records`（新增）
```
id                      INTEGER PK AUTOINCREMENT
session_id              TEXT NOT NULL
stage_id                INTEGER NOT NULL
decision                TEXT NOT NULL     # advance | retry | remediate | reteach
best_score              REAL NOT NULL
next_stage_id           INTEGER NULL
next_stage_score        REAL NULL         # 下一節點的候選排名分數
reason_lines_json       TEXT DEFAULT '[]' # 決策說明文字列表
strategy_snapshot_json  TEXT DEFAULT '{}' # 含 mastery_map、weak_concepts、score_trend 等
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
    turn_id: str            # UUID
    question_id: str
    question_text: str
    user_answer: str | None
    evaluation: dict | None # EvaluatorAgent 回傳
    clarification_rounds: int
    created_at: datetime

@dataclass
class WorkingMemory:
    session_id: str
    current_stage_id: int = 0
    stages: list[dict] = ...          # 整份 stage 列表（含動態節點），session 期間可增長
    current_turn: TurnContext | None = None
    stage_turns: list[TurnContext] = ...
    pending_questions: list[dict] = ...
    current_explanation: str = ""
    stage_evaluations: list[dict] = ...
    current_attempt: int = 1          # 當前 stage 的嘗試次數（retry/reteach 遞增）
    source_corpus: str = ""           # 全部 stages 的原文語料庫，供 ask_tutor 使用
    question_mode: str = "short_answer"   # short_answer | multiple_choice
    enrichment_stage_added: bool = False  # 防止重複插入整合挑戰節點
```

關鍵方法：
- `get_compressed_history(max_turns=3)` — 取最近 3 輪 Q/A，供 EvaluatorAgent 使用
- `record_completed_turn()` — 將 `current_turn` 移入 `stage_turns`，清空 `current_turn`
- `reset_for_new_stage(stage_id)` — 切換 stage 時清空所有輪次狀態，重置 `current_attempt=1`

### 4.2 SessionMemory（SQLite）

**位置**：`backend/memory/session_memory.py`  
**生命週期**：永久持久化

主要函式：

| 函式 | 說明 |
|------|------|
| `create_pending_session(...)` | 建立 `status='pending_confirmation'` 的 session，同時廢棄同用戶其他 pending session；寫入 `provider_name`、`model_name` |
| `activate_pending_session(session_id)` | 將 status 改為 `active`，清空 `pending_map_json` |
| `get_user_active_session(user_id)` | 查詢 `status IN ('active', 'pending_confirmation')`，取最新一筆 |
| `get_session(session_id)` | 依 ID 查單筆 session |
| `store_stages(session_id, stages)` | 更新 `stages_json`（動態插入節點後也呼叫此函式） |
| `get_stage_statuses(session_id)` | 取得 `{stage_id: status}` 字典 |
| `store_stage_explanation(...)` | 寫入 `full_explanation` |
| `get_stage_explanation(...)` | 讀取 `full_explanation` |
| `get_all_stage_explanations(session_id)` | 讀取所有 stage 的講解，供 `resume_session` 全量恢復 |
| `store_stage_questions(...)` | 寫入 `questions_json` |
| `get_stage_questions(...)` | 讀取 `questions_json` |
| `get_stage_qa_records(...)` | 查詢本 stage 所有已答題目記錄 |
| `get_all_stage_qa_records(session_id)` | 查詢所有 stage 答題記錄，按 stage_id 分組 |
| `insert_qa_record(...)` | 插入一筆答題記錄 |
| `upsert_stage_progress(...)` | INSERT OR UPDATE stage_progress |
| `update_current_stage(...)` | 更新 sessions.current_stage_id |
| `complete_session(session_id)` | 設定 status='completed' |
| `insert_decision_record(...)` | 插入進度決策歷史，超過上限時自動刪除最舊筆 |
| `get_decision_records(session_id)` | 取得 session 的決策歷史列表 |

### 4.3 LongtermMemory（SQLite）

**位置**：`backend/memory/longterm_memory.py`  
**生命週期**：永久持久化，跨 session 累積

主要函式：

| 函式 | 說明 |
|------|------|
| `get_user_profile_summary(user_id)` | 回傳偏好風格 + 平均嘗試次數，供 TeacherAgent 使用 |
| `get_weak_concepts(user_id)` | 取 mastery_score < 0.6 的概念，最多 5 個，以「、」分隔 |
| `get_concept_mastery_map(user_id, concepts)` | 批次查詢指定概念的掌握度，回傳 `{概念名: 分數}` 字典，供 advance 決策排名使用 |
| `update_concept_mastery(...)` | 用 EMA（α=0.3）更新概念掌握度 |
| `update_user_profile(user_id, attempts)` | 用 EMA（α=0.2）更新平均嘗試次數 |

---

## 5. REST API 端點

### `POST /auth/register`
- 接收：`{email, password}`
- 建立 user，以 bcrypt 儲存密碼雜湊
- 回傳：`{token: JWT, user_id, email}`

### `POST /auth/login`
- 接收：`{email, password}`
- 驗證密碼後簽發 JWT（過期時間由 `JWT_SECRET` 決定）
- 回傳：`{token: JWT, user_id, email}`

### `GET /auth/me?token=...`
- 解碼 JWT，回傳當前使用者資訊

### `POST /upload`（需 `Authorization: Bearer <token>`）
- 接收：multipart/form-data，欄位 `file`
- 將原始 bytes + filename + mime_type 存入 `backend/files/upload_store.py`（process 記憶體，以 `file_id` 為鍵）
- 回傳：`{file_id: UUID}`

### `GET /sessions/active?token=...`
- 查詢該用戶目前 `status IN ('active', 'pending_confirmation')` 的最新 session
- 若 `status='pending_confirmation'`：回傳 `pending_map`（nodes + summary）
- 若 `status='active'`：回傳 `stage_statuses`（`{stage_id: status}`）
- 同時回傳 `provider`、`model`（從 DB 讀取，恢復連線時使用）
- 同時回傳 `stages` 列表（含 `source_chunks`，含動態插入的節點）
- 讓前端在頁面重整後能恢復狀態

### `GET /health`
- 回傳 `{"status": "ok"}`，供健康檢查

---

## 6. WebSocket 訊息協定

連線位址：`ws://localhost:8000/ws/{session_id}?token=JWT`

### 客戶端 → 伺服器

| 訊息 type | 必要欄位 | 說明 |
|-----------|----------|------|
| `start_session` | `content` 或 `uploaded_file_id`，`provider`，`target_depth`，`question_mode?`，`model?` | 啟動新學習會話；`question_mode` 預設 `short_answer`，可傳 `multiple_choice` |
| `confirm_map` | `provider?`，`model?` | 用戶確認知識地圖，開始教學（重整後可省略，從 DB 讀取） |
| `submit_answer` | `session_id`，`question_id`，`answer` | 提交問題答案 |
| `resume_session` | `session_id`，`provider?`，`model?` | 重整後恢復進行中的 session（provider/model 可從 DB 讀取） |
| `request_hint` | — | 請求提示（目前回傳佔位訊息） |
| `ask_tutor` | `question` | 學生對當前節點或教材提出問題，Orchestrator 判斷範疇後回答 |

### 伺服器 → 客戶端

| 訊息 type | 重要欄位 | 說明 |
|-----------|----------|------|
| `kicked` | `message` | 同帳號在其他裝置登入，此連線被強制中斷（code 4002） |
| `knowledge_map` | `nodes: [{node_id, stage_id, title}]`，`summary` | LLM 切割完成，等待用戶確認 |
| `session_started` | `session_id`，`total_stages`，`stages`（含 source_chunks），`stage_statuses?` | 會話正式啟動、恢復或 advance 後刷新（每次 advance 都會重送以更新側邊欄） |
| `session_snapshot` | `stage_explanations`，`stage_qa_histories`，`decision_history` | `resume_session` 時全量推送所有已儲存的講解、答題記錄與決策歷史，供前端還原完整學習史 |
| `explanation_chunk` | `chunk: str`，`is_final: bool` | 串流講解片段 |
| `explanation_complete` | `stage_id`，`stage_title`，`full_explanation` | 本 stage 講解完成，附完整文字（含進度表 + 講解 + 問題區塊） |
| `explanation_reset` | — | 清空前端講解區（retry/remediate/reteach/DriftVerifier 重寫時觸發） |
| `question` | `question_id`，`text`，`type`，`answer_mode`，`options`，`evidence_chunk_ids`，`stage_id`，`attempt_number` | 發送一道題目；`answer_mode` 為 `short_answer` 或 `multiple_choice`；`options` 在選擇題時填入；`evidence_chunk_ids` 指向原文 source_chunks |
| `feedback` | `question_id`，`score`，`feedback_text`，`needs_clarification`，`clarification_question?` | 評分結果 |
| `stage_decision` | `decision`，`message`，`next_stage_id?`，`next_stage_score?`，`best_score`，`reason_lines`，`strategy_snapshot` | 進度決策結果；`reason_lines` 為決策說明列表；`strategy_snapshot` 包含 mastery_map、weak_concepts、score_trend 等 |
| `qa_history` | `records: [{question_id, question_text, question_type, user_answer, score, feedback_text}]` | `_resume_from_stored` 時推送當前 stage 的歷史答題記錄 |
| `resume_state` | `current_question?`，`last_feedback?` | `_resume_from_stored` 時推送，讓前端知道當前問題與上次 feedback |
| `tutor_reply` | `question`，`answer`，`in_scope` | 回應 `ask_tutor`；`in_scope=false` 表示問題超出教材，答案來源含網路搜尋 |
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
            └── 以 file_id 為鍵，儲存 {filename, mime_type, raw: bytes}
                （process 記憶體，重啟後消失）
前端收到 {file_id}
    └── 送 start_session 時帶入 uploaded_file_id
```

### 7.2 啟動新會話（start_session）

```
前端 WebSocket send: {type: "start_session", payload: {uploaded_file_id, provider, target_depth, question_mode?, model?}}
    │
    main.py WebSocket handler
        ├── 驗證 JWT
        ├── 若有 uploaded_file_id：
        │       load_upload(file_id)                    # 從 upload_store 取原始 bytes
        │       create_provider_file_ref(provider, ...) # 上傳至對應 LLM 平台
        │           ├── claude  → Anthropic Files API（回傳 claude_file_id）
        │           ├── openai  → OpenAI Files API（回傳 openai_file_id）
        │           ├── gemini  → google-genai files.upload（回傳 gemini_file_uri）
        │           └── monica  → inline base64（存入 monica_file_data）
        │
        ├── create_provider(provider_name, model?)      # 建立 LLM Provider 實例
        ├── LearningOrchestrator(llm)                   # 建立 Orchestrator
        └── orchestrator.start_session(...)
                │
                ├── AgentContext 建立（包含 raw_content / provider_file_ref / target_depth）
                ├── ContentSplitterAgent.run(ctx)        # 呼叫 LLM 切割材料
                │       ├── system prompt：content_splitter
                │       ├── 附件（如有）以對應 provider 格式傳遞
                │       ├── 回傳 JSON → 解析、正規化 stages
                │       ├── 每個 stage 呼叫 _normalize_stage_source_chunks() 產生 source_chunks
                │       └── 若 JSON 解析失敗：最多重試 3 次（自動修復 JSON）
                │
                ├── 計算 content_hash（sha256[:16]）
                ├── 存入 DB：session_memory.create_pending_session(...)
                │       status = 'pending_confirmation'
                │       stages_json = 完整 stage 列表
                │       pending_map_json = {nodes, summary}
                │       provider_name = provider_name
                │       model_name = model_name
                │
                └── emit: {type: "knowledge_map", payload: {nodes, summary}}
```

### 7.3 確認知識地圖（confirm_map）

```
前端 WebSocket send: {type: "confirm_map", payload: {provider?, model?}}
    │
    main.py
        ├── 若 _orchestrators 中找不到（重整後遺失）：
        │       從 DB 讀取 provider_name / model_name（payload 可覆蓋）
        │       建立新的 LLM Provider + LearningOrchestrator
        │       存入 _orchestrators[session_id]
        │
        └── orchestrator.confirm_session(session_id, user_id, emit)
                │
                ├── 若 _pending_stages 為 None（in-memory 遺失）：
                │       session_memory.get_session(session_id) 從 DB 恢復
                │       讀取 stages_json 和 content_hash
                │
                ├── session_memory.activate_pending_session(session_id)
                │       status: 'pending_confirmation' → 'active'
                │       清空 pending_map_json
                │
                ├── session_memory.store_stages(session_id, stages)
                ├── 為每個 stage upsert_stage_progress(status='pending')
                ├── WorkingMemory 初始化：
                │       wm.stages = stages
                │       wm.enrichment_stage_added = 任一 stage 有 kind='enrichment'
                │       wm.reset_for_new_stage(0)
                │
                ├── emit: {type: "session_started", payload: {stages（含 source_chunks）, ...}}
                │
                └── run_stage(session_id, user_id, stages, stage_index=0, question_mode, emit)
```

### 7.4 教學單一 Stage（run_stage）

```
run_stage(session_id, user_id, stages, stage_index, question_mode, emit)
    │
    ├── wm.reset_for_new_stage(stage_id)        # 清空本 stage 的所有輪次狀態
    ├── wm.question_mode = question_mode
    ├── wm.source_corpus = 所有 stages 的全文語料庫（含 node_id、title、content、source_chunks）
    ├── session_memory.update_current_stage(...)
    ├── session_memory.upsert_stage_progress(status='in_progress')
    │
    ├── longterm_memory.get_user_profile_summary(user_id)  # 學習風格
    ├── longterm_memory.get_weak_concepts(user_id)          # 薄弱概念
    │
    ├── 【步驟 1】產生進度表 Markdown
    │       _build_progress_table(stages, stage_index)
    │       emit: explanation_chunk（is_final=False）
    │
    ├── 【步驟 2】串流講解（TeacherAgent）
    │       TeacherAgent.stream_explanation(ctx)
    │           system prompt：teacher（含 user_profile + weak_concepts）
    │           user message：節點標題 + 前一節點 + 學習材料 + 關鍵概念
    │           每個 chunk → emit: explanation_chunk（is_final=False）
    │       完成後進行 DriftVerifier 驗證：
    │           DriftVerifierAgent.run(...)
    │               輸入：content_type="explanation"，source_chunks，candidate_text=full_explanation
    │               若 aligned=False：
    │                   重新呼叫 TeacherAgent（content 加入修正指引）
    │                   explanation_rewritten = True
    │       若 explanation_rewritten：
    │           emit: explanation_reset
    │           emit: explanation_chunk（進度表 + 新講解）
    │       wm.current_explanation = full_explanation
    │       session_memory.store_stage_explanation(...)
    │
    ├── 【步驟 3】生成問題（QuestionGeneratorAgent）
    │       QuestionGeneratorAgent.run(ctx)
    │           system prompt：question_generator
    │           task_payload：stage、num_questions、attempt_number=1、
    │                         previous_question_ids=[]、question_mode
    │           num_questions：
    │               multiple_choice → max(4, estimated_questions * 2)
    │               short_answer    → estimated_questions
    │       進行 DriftVerifier 驗證：
    │           DriftVerifierAgent.run(...)
    │               輸入：content_type="questions"，source_chunks，candidate_text=JSON(questions)
    │               若 aligned=False：重新生成問題（content 加入對齊修正要求）
    │       wm.pending_questions = questions
    │       session_memory.store_stage_questions(...)
    │
    ├── 【步驟 4】附加問題區塊 Markdown
    │       _build_questions_section(questions)
    │       emit: explanation_chunk（is_final=False）
    │
    ├── 【步驟 5】結束串流
    │       emit: explanation_chunk（chunk='', is_final=True）
    │       emit: explanation_complete（stage_id, stage_title,
    │                                  full_explanation=進度表+講解+問題區塊）
    │
    └── 【步驟 6】發送第一道問題
            q = questions[0]
            wm.current_turn = TurnContext(question_id=q.question_id, ...)
            emit: {type: "question", payload: {
                question_id, text, type, answer_mode, options,
                evidence_chunk_ids, stage_id, attempt_number=1
            }}
```

### 7.5 提交答案（submit_answer）

```
前端 WebSocket send: {type: "submit_answer", payload: {session_id, question_id, answer}}
    │
    orchestrator.handle_answer(session_id, user_id, question_id, answer, emit)
        │
        ├── 從 WorkingMemory 取 wm.stages、wm.current_turn
        ├── 從 DB 取 current_stage_id（即時查詢，保證與 DB 一致）
        ├── 驗證 current_turn.question_id == question_id（防止重複提交）
        │
        ├── 【評分】EvaluatorAgent.run(ctx)
        │       system prompt：evaluator（理解哲學 + 評分標準）
        │       user message：
        │           問題 + 問題類型 + 測試概念 + 評分要點（不公開）
        │           + 學生答案
        │           + 最近 3 輪壓縮歷史（wm.get_compressed_history）
        │           + source_chunks（本 stage 的原文依據）
        │       回傳 JSON：{score, understood_concepts, confused_concepts,
        │                    feedback, needs_clarification, clarification_question}
        │
        ├── wm.record_completed_turn()
        │       current_turn → stage_turns（stage_evaluations 也同步更新）
        │
        ├── session_memory.insert_qa_record(...)         # 永久記錄答題
        │
        ├── emit: {type: "feedback", payload: {score, feedback_text, ...}}
        │
        ├── 更新 LongtermMemory
        │       為 stage.key_concepts 中每個概念呼叫 update_concept_mastery(...)
        │       EMA 公式：new_score = 0.7 * old + 0.3 * latest
        │
        ├── 找出下一道未答問題
        │       remaining = pending_questions 中 question_id 不在 stage_turns 已答集合內的題目
        │
        ├── 若有 remaining：
        │       wm.current_turn = TurnContext(next question)
        │       emit: {type: "question", payload: {
        │           answer_mode, options, evidence_chunk_ids,
        │           attempt_number = wm.current_attempt, ...
        │       }}
        │
        └── 若全部回答完：
                _make_progress_decision(...)
```

### 7.6 進度決策（_make_progress_decision）

```
_make_progress_decision(session_id, user_id, stages, stage, current_idx, wm, emit)
    │
    ├── ProgressManagerAgent.run(ctx)
    │       【純程式邏輯，不呼叫 LLM】
    │       輸入：wm.stage_evaluations（所有本 stage 的評分）
    │       計算：
    │           attempts = len(evaluations)
    │           best_score = max(scores)
    │           latest_score = scores[-1]
    │       決策邏輯：
    │           best_score >= 0.75          → "advance"
    │           attempts < 3               → "retry"
    │           attempts == 3 且 latest < 0.5 → "reteach"
    │           otherwise                  → "remediate"
    │
    ├── stable_high = _is_stable_high_performance(wm.stage_evaluations)
    │       條件：評分筆數 >= 2 且 min(scores) >= 0.8 且 avg >= 0.87
    │
    ├── 【advance】
    │       completed_stage_ids.add(current stage_id)
    │       取 mastery_map（批次查詢所有概念掌握度）
    │       取 weak_concepts（list[str]）
    │       _pick_next_stage_index(...)
    │           _rank_next_stage_candidates(...)
    │               對每個未完成節點計算 score：
    │               stable_high 模式：unseen*3.0 + low_mastery*1.2 + weak_overlap*0.8
    │                                  - mastered*1.5 - distance_penalty
    │               一般模式：        weak_overlap*3.0 + low_mastery*2.2 + unseen*0.8
    │                                  - mastered*0.5 - distance_penalty
    │       若 next_stage_idx is None 且 stable_high 且尚未加入 enrichment：
    │           _insert_enrichment_stage(session_id, stages)
    │               插入 kind='enrichment' 的整合挑戰節點（node_id: E.N）
    │               wm.enrichment_stage_added = True
    │               dynamic_stage_inserted = True
    │
    ├── 【remediate / reteach】
    │       取 mastery_map 和 weak_concepts
    │       若有 remediation_focus 概念 且無對應候選節點：
    │           _insert_remediation_stage(session_id, stages, current_idx, focus)
    │               插入 is_dynamic=True 的補強節點（node_id: R.N）
    │               next_stage_idx = 插入位置
    │               dynamic_stage_inserted = True
    │       decision_reasons 統一附加：
    │           「補強不影響整體進度：知識地圖中所有節點最終都會完整覆蓋。」
    │           （讓學生安心，明白補強不會導致後續節點被跳過）
    │
    ├── session_memory.upsert_stage_progress(...)
    │       advance → status='completed'
    │       其餘   → status='in_progress'
    │
    ├── session_memory.insert_decision_record(...)
    │       儲存決策、分數、reason_lines、strategy_snapshot（含 mastery_map/weak_concepts/score_trend）
    │
    ├── emit: {type: "stage_decision", payload: {
    │           decision, message, next_stage_id, next_stage_score,
    │           best_score, reason_lines, strategy_snapshot
    │       }}
    │
    ├── 【advance 後續】
    │       若有 next_stage_idx：
    │           longterm_memory.update_user_profile(user_id, len(wm.stage_turns))
    │           emit: session_started（含更新後 stage_statuses，供側邊欄上色）
    │           run_stage(next_stage_idx, wm.question_mode, ...)
    │       否則（無可前進節點）：
    │           session_memory.complete_session(session_id)
    │           emit: {type: "course_completed"}
    │
    ├── 【retry / remediate 後續】
    │       wm.current_attempt += 1
    │       emit: explanation_reset
    │       emit: explanation_chunk（進度表 + 補強說明 Markdown）
    │       QuestionGeneratorAgent.run（新問題，attempt_number 遞增，避開已問 IDs）
    │       DriftVerifierAgent 驗證（aligned=False 則再生一次）
    │       重置 wm.pending_questions、wm.stage_evaluations = []
    │       為新問題做 question_id 去重（與 stage_turns 碰撞時重新生成）
    │       emit: explanation_chunk（問題區塊）
    │       emit: explanation_chunk（is_final=True）
    │       emit: {type: "question"}（第一道新題）
    │
    └── 【reteach 後續】
            wm.current_attempt += 1
            emit: explanation_reset
            TeacherAgent.stream_explanation（換框架重新講解）
                stage content 後附加「請換一個完全不同的比喻框架重新解釋」
            DriftVerifierAgent 驗證（aligned=False 則再重寫一次）
            QuestionGeneratorAgent.run（全新問題，DriftVerifier 驗證）
            重置 wm.stage_evaluations = []
            去重 question_id → emit: question
```

**四種決策的觸發條件一覽**：

| 決策 | 觸發條件 | 後續動作 |
|------|----------|----------|
| `advance` | best_score ≥ 0.75 | 依掌握度/弱點排名選擇下一節點（可能插入整合挑戰節點） |
| `retry` | attempts < 3 且 best_score < 0.75 | 調整難度，出新題（同框架） |
| `reteach` | attempts == 3 且 latest_score < 0.5 | 換框架全新講解 + 新題 |
| `remediate` | attempts >= 3 且 latest_score ≥ 0.5 | 補充例子 + 新題（可能插入動態補強節點） |

**動態節點類型**：

| 類型 | node_id 格式 | 觸發時機 | 特徵欄位 |
|------|-------------|----------|---------|
| 補強節點 | `R.N` | remediate/reteach 且無現成弱點節點 | `is_dynamic=True`，`source_stage_id` 指向來源 |
| 整合挑戰節點 | `E.N` | advance 且所有原始節點已完成 + stable_high | `is_dynamic=True`，`kind='enrichment'` |

### 7.7 恢復會話（resume_session）

```
前端重整後 → getActiveSession(token) → GET /sessions/active
    │
    ├── 若 status='pending_confirmation'：
    │       回傳 pending_map（含 provider/model）
    │       前端直接顯示知識地圖 Modal
    │       → 用戶確認後走 confirm_map 流程（見 7.3）
    │
    └── 若 status='active'：
            前端建立 WebSocket 並立即送 resume_session（provider/model 從 /sessions/active 取得）
                │
            orchestrator.resume_session(session_id, user_id, emit)
                │
                ├── session_memory.get_session(session_id)
                ├── 解析 stages_json（含動態節點）
                ├── session_memory.get_stage_statuses(session_id)
                ├── wm.stages = stages
                ├── wm.enrichment_stage_added = 任一 stage 有 kind='enrichment'
                ├── wm.source_corpus = 全部 stages 語料庫
                │
                ├── emit: session_started（含 stages、stage_statuses）
                │
                ├── emit: session_snapshot（全量恢復）
                │       stage_explanations: {stage_id: explanation_text}
                │       stage_qa_histories: {stage_id: [qa_records]}
                │       decision_history: [{decision, best_score, reason_lines, strategy_snapshot}]
                │
                ├── 取 current_stage_id 對應的 stage_index
                ├── session_memory.get_stage_explanation(session_id, stage_id)
                │
                ├── 若有 stored_explanation：
                │       _resume_from_stored(...)
                │           emit: explanation_chunk（進度表 + 已儲存講解 + 問題區塊）
                │           emit: explanation_chunk（is_final=True）
                │           emit: explanation_complete
                │
                │           查詢 qa_records：找出已回答的 question_id 集合
                │           emit: qa_history（本 stage 歷史答題記錄）
                │
                │           emit: resume_state（current_question, last_feedback）
                │
                │           若有 unanswered：
                │               wm.current_turn = TurnContext(第一道未答題目)
                │               emit: question
                │           若全部已答：
                │               從 qa_records 重建 wm.stage_evaluations
                │               _make_progress_decision(...)
                │
                └── 若無 stored_explanation（stage 尚未講解過）：
                        run_stage(...)（完整重新執行）
```

### 7.8 學生提問（ask_tutor）

```
前端 WebSocket send: {type: "ask_tutor", payload: {question: "..."}}
    │
    orchestrator.handle_student_question(session_id, question, emit)
        │
        ├── 取 wm.source_corpus（全部 stages 的原文語料庫）
        │   若無語料庫 → emit: tutor_reply（提示先開始學習流程）
        │
        ├── 【範疇判斷】呼叫 LLM（scope_judge system prompt）
        │       輸入：教材內容（前 4000 字）、當前節點、學生提問
        │       回傳 JSON：{in_scope: bool}
        │
        ├── 若 in_scope=False：
        │       search_web(question, max_results=3)
        │           呼叫 DuckDuckGo Instant Answer API（免 key）
        │           回傳 [{title, snippet, url}]
        │       web_context = 格式化搜尋結果
        │
        └── 【生成回答】呼叫 LLM（tutor_reply system prompt）
                輸入：in_scope、當前節點、學生問題、教材內容（前 5000 字）、搜尋摘要（若有）
                emit: {type: "tutor_reply", payload: {question, answer, in_scope}}
```

---

## 8. 六個 Agent 詳解

所有 Agent 繼承 `BaseAgent`，共享以下特性：
- `_messages: list[LLMMessage]` — 每次 `run()` 開始前呼叫 `_reset()` 清空
- `_token_usage()` — 估算當前上下文 token 數
- 每次 `run()` 結束後再次 `_reset()`，防止跨呼叫上下文污染

### 8.1 ContentSplitterAgent

**職責**：將學習材料切割為邏輯 stage 序列

**呼叫時機**：`start_session` 時

**Token 預算**：`max_context_tokens=4000`

**輸入**（`task_payload`）：
- `raw_content: str` — 文字內容
- `provider_file_ref: dict | None` — 附件引用（Claude/OpenAI/Gemini 格式）
- `max_stages: int = 8`
- `target_depth: str` — beginner / intermediate / advanced

**System Prompt 重點**：
- 維特根斯坦式「語言遊戲單元」切割原則
- node_id 格式：大章節.小節點（如 1.1、2.3）
- 回傳嚴格 JSON 結構，每個 stage 包含 `source_chunks` 欄位

**輸出 JSON 結構**：
```json
{
  "stages": [{
    "stage_id": 1, "node_id": "1.1", "title": "...",
    "content": "...", "key_concepts": [], "prerequisites": [],
    "estimated_questions": 2,
    "source_chunks": [{"chunk_id": "s1_c1", "quote": "原文引用", "note": ""}]
  }],
  "summary": "一句話摘要"
}
```

Orchestrator 在切割完成後呼叫 `_normalize_stage_source_chunks()` 確保每個 stage 都有有效的 source_chunks（若無則從 content 生成 fallback chunk）。

**容錯機制**：JSON 解析失敗時，最多重試 3 次，用 LLM 自動修復格式。

### 8.2 TeacherAgent

**職責**：為當前 stage 生成串流講解

**呼叫時機**：`run_stage`（串流）、`reteach` 決策（串流），DriftVerifier 判斷 aligned=False 時重寫

**輸入**：stage 定義（含 source_chunks）、前一節點標題、user_profile_summary、weak_concepts

**System Prompt 重點**：
- 角色定位：蘇格拉底式教師，語氣像「懂行的朋友在耐心講解」，既專業精準又親切有溫度
- 固定 Markdown 格式：`### 📖 本節內容` + `### 🔗 與前一節點的關聯`（第一節點填寫「這是本次學習的第一個節點」）
- 【講解原則】（置於格式規範前，確保 LLM 優先遵守）：
  1. 先貼近原文核心敘述，再用生活化類比解釋（需標示「類比說明，非原文」）
  2. 每個抽象概念都必須提供至少 2 個不同角度的生活化類比（家族相似性），**不可略過**
  3. 類比必須取自日常場景（圖書館、超市、銀行、工廠、餐廳等）
  4. 深度優先：寧可把一個概念講透，不蜻蜓點水
  5. 長度 3-5 分鐘閱讀量；不重複節點標題，直接切入內容
- 【重要限制】：只能依據原文，每個敘述需加來源標記（如 `[s1_c1]`），不可超綱

**特殊使用**：
- `reteach` 時 stage content 後附加「請換一個完全不同的比喻框架重新解釋」
- DriftVerifier 重寫時在 content 後附加修正指引（`revision_hint`）

### 8.3 QuestionGeneratorAgent

**職責**：為當前 stage 生成蘇格拉底式問題

**呼叫時機**：`run_stage` 結束前、`retry/remediate/reteach` 決策後，DriftVerifier 判斷 aligned=False 時重新生成

**輸入**：stage 定義、num_questions、attempt_number、previous_question_ids（避免重複）、question_mode

**System Prompt 重點**：
- 布魯姆分類法：至少 1 題應用型 + 1 題理解型
- attempt_number > 1 時降低難度，加入鷹架引導
- 避免是/否問題，避免照抄原文可答的問題
- `question_mode='multiple_choice'` 時需填 `options` 欄位（4 個選項）

**num_questions 計算**：
- `multiple_choice`：`max(4, stage.estimated_questions * 2)`
- `short_answer`：`stage.estimated_questions`（重試時固定 2）

**輸出 JSON**：
```json
{
  "questions": [{
    "question_id": "q_1_0", "text": "...",
    "type": "apply | understand | create",
    "answer_mode": "short_answer | multiple_choice",
    "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
    "difficulty": "easy | medium | hard",
    "key_concepts_tested": ["概念A"],
    "expected_answer_hints": ["要點一"],
    "evidence_chunk_ids": ["s1_c1"]
  }]
}
```

### 8.4 EvaluatorAgent

**職責**：評估學生答案，給分並提供回饋

**呼叫時機**：每次 `submit_answer` 時

**輸入**：question 定義（含 key_concepts_tested + expected_answer_hints）、user_answer、最近 3 輪壓縮歷史、**source_chunks**（本 stage 的原文依據）

**System Prompt 重點**：
- 理解是光譜，不是二元判定
- 重視思考過程，不直接給答案
- Score 定義：0.9+ 能舉一反三 / 0.7~0.89 核心正確 / 0.5~0.69 部分理解 / 0~0.49 未展示基本理解

**三條評分路徑**：

| 情境 | 處理方式 |
|------|---------|
| 選擇題答對（score=1.0） | 不呼叫 LLM，直接回傳，feedback 包含正確選項文字 |
| 選擇題答錯 | LLM 評分 0.0~0.6，依選項與正確答案的概念相近程度給分 |
| 短答題 | LLM 評分 0.0~1.0，依布魯姆分類標準評估 |

**掌握度分類標籤（`_add_mastery_label`）**：

所有評分路徑的最終輸出，都會在 `feedback` 文字最前面加上分類標籤：
- `score ≥ 0.75`：`✅ **掌握度佳**`
- `0.5 ≤ score < 0.75`：`⚠️ **掌握度部分不足**`
- `score < 0.5`：`❌ **掌握度明顯不足**`

此標籤由後端 `_add_mastery_label()` 統一注入，不依賴 LLM 生成，確保格式一致。

**輸出 JSON**（LLM 回傳格式，標籤注入後的 feedback 欄位含前綴）：
```json
{
  "score": 0.85,
  "understood_concepts": ["概念A"],
  "confused_concepts": ["概念B"],
  "feedback": "✅ **掌握度佳**\n\n繁體中文回饋...",
  "needs_clarification": false,
  "clarification_question": null
}
```

### 8.5 ProgressManagerAgent

**職責**：決定學習進度策略

**呼叫時機**：本 stage 所有問題均已回答後

**特點**：**不呼叫 LLM**，純粹以規則計算決策

**輸入**：stage_evaluations（所有評分記錄）、pass_threshold=0.75、max_attempts=3

**決策邏輯**：
```
best_score = max(所有評分)
attempts   = 評分筆數

best_score >= 0.75              → advance
attempts < 3                    → retry
attempts == 3 AND latest < 0.5  → reteach
otherwise                       → remediate
```

**輸出**：`{decision, message, next_stage_id, best_score, remediation_focus}`

### 8.6 DriftVerifierAgent（新增）

**職責**：驗證 LLM 生成的講解或問題是否紮根於原始教材（source_chunks），防止幻覺或教材外推

**呼叫時機**：
1. TeacherAgent 串流完成後，驗證 `full_explanation`（content_type="explanation"）
2. QuestionGeneratorAgent 完成後，驗證 `questions` JSON（content_type="questions"）
3. `reteach` 重寫講解後同樣驗證

**特點**：若 `aligned=False`，Orchestrator 自動觸發一次重試（加入 `revision_hint` 作為修正指引）

**輸入**（`task_payload`）：
- `content_type: str` — `"explanation"` 或 `"questions"`
- `source_chunks: list[dict]` — `[{chunk_id, quote, note}]`
- `candidate_text: str` — 待驗證的文字（講解 Markdown 或問題 JSON）

**System Prompt**：`SYSTEM_PROMPTS["drift_verifier"]`

**輸出 JSON**：
```json
{
  "aligned": true,
  "issues": ["問題描述1"],
  "missing_evidence": ["未引用的關鍵點"],
  "revision_hint": "修正建議"
}
```

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
| ClaudeProvider | 獨立 `system` 參數 | `claude_file_id`（Anthropic Files API） |
| OpenAIProvider | messages[0] 插入 system role | `openai_file_id` |
| GeminiProvider | `config.system_instruction` | `gemini_file_uri` |
| MonicaProvider | OpenAI 相容格式 | inline base64（`data:mime;base64,...`） |

### 工廠函式

```python
llm = create_provider("claude" | "openai" | "gemini" | "monica", model=None)
```

`model=None` 時各 Provider 使用預設模型。

### 系統 Prompt 一覽

| 鍵名 | 使用 Agent / 方法 |
|------|-----------------|
| `content_splitter` | ContentSplitterAgent |
| `teacher` | TeacherAgent |
| `question_generator` | QuestionGeneratorAgent |
| `evaluator` | EvaluatorAgent |
| `drift_verifier` | DriftVerifierAgent |
| `scope_judge` | handle_student_question（範疇判斷） |
| `tutor_reply` | handle_student_question（生成回答） |

---

## 10. 設定與環境變數

**設定檔**：`backend/.env`（由 `backend/config.py` 明確載入，解決不同 CWD 啟動的問題）

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

`CORS_ORIGINS` 在 `config.py` 硬編碼為 `["http://localhost:5173", "http://127.0.0.1:5173"]`，部署時需手動修改。

### 外部工具

| 工具 | 位置 | 說明 |
|------|------|------|
| `search_web()` | `backend/tools/web_search.py` | 使用 DuckDuckGo Instant Answer API（免 API key）搜尋網頁，供 `ask_tutor` 處理範疇外問題時補充脈絡 |
