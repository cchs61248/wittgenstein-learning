# 後端流程詳解

> 適用版本：2026-05 feature 分支（最後更新：2026-05-30，**Curriculum Pipeline 統一化**：V1 / V2 大檔 / Plan B 全部刪除，reducer 改非主線（agent 刪除、`reducer_skipped=True`，2 個 reducer prompt 留存但不呼叫——詳見 §7.2），唯一路徑是 V2 小檔逐檔切（`single_split` 或 `per_source_split`），由前端「是否同一教材」radio 控制 ContentOutline 是否跑；EPUB 在上傳階段就由 `split_epub_by_toc` 切成多章節獨立 file_id；標題去重閾值改為 env `STAGE_TITLE_MERGE_THRESHOLD`（預設 0.85），ConceptCanonicalize 改為 env `CONCEPT_CANONICALIZE` 開關（預設 off）。設計文件：`docs/superpowers/specs/2026-05-27-curriculum-unify-v2-design.md`。較早保留的能力：region checkpoint 斷點續跑（Migration 022–023）、Arq + Redis 背景 worker（`CURRICULUM_USE_ARQ=1`）、LLM result cache（Migration 024 / `LLM_CACHE_ENABLED=1`）、`docker-compose.yml`（Redis :6380 + curriculum-worker）、**LLM 流量治理**（`LLM_MAX_CONCURRENT` + Redis ZSET semaphore，見 [§13](#13-llm-流量治理)）。**

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
11. [WS 基礎設施（Phase 1–3 增補）](#11-ws-基礎設施phase-13-增補)
12. [Curriculum 背景化（Checkpoint / Arq / LLM Cache）](#12-curriculum-背景化checkpoint--arq--llm-cache)
13. [LLM 流量治理](#13-llm-流量治理)

---

## 1. 系統架構概覽

```
前端 (React + Zustand)
    │
    ├── REST  → /auth/*, /upload, /upload/url, /sessions/*
    │               │           │
    │               │           └── url_fetcher.py → readability-lxml + strict_main + youtube-transcript-api + ASR fallback
    │               └── upload_store.py → data/uploads/*.bin + *.meta.json
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
- `WorkingMemory._store: dict[str, WorkingMemory]` — 以 `session_id` 為鍵；resume_session / start_session 主動 build
- `generation_handle._registry: dict[str, _GenerationHandle]` — 以 generation key 為鍵；done_callback 自動清

> Phase 3 Task C1 起：**`_orchestrators` in-memory pool 已移除**。`backend/main.py` 內 `_build_orchestrator_for_session(session_id, p)` 在每次 WS 訊息進來時從 DB session row 重建 orchestrator instance（partial stateless — orchestrator 不快取，WorkingMemory 仍 in-process）。

**跨 worker 同步（Phase 3 Task B）**：
- `inflight_locks` DB 表 + `generation_handle.register_async/finish_async/cancel_async` — 同步寫 DB lock
- `_wait_or_lookup_cache` helper（Phase 1 + Bug F）：先查歷史 cache（命中 emit + return True）→ miss 才看 inflight registry → 等對方完成後再查 cache
- WAL 模式啟用：並發 acquire/release/cleanup_stale 無 deadlock

**WebSocketManager 多裝置管理**：
- 同一用戶**不同 client_id** 新連線進來時，舊連線收到 `kicked` 訊息後被強制關閉（code 4002）
- 同瀏覽器**多分頁**共用同個 `client_id`（localStorage 跨分頁同享）→ 允許並存

**重連機制（Phase 2）**：
- 前端 `LearningWebSocket` 指數退避 1/2/4/8/16/32s（上限 6 次共 63 秒），重連後自動 replay `resume_session`
- `verifyAuth` 三態 `'ok' | 'invalid' | 'network'` — 網路斷不誤踢登出

**串流寫入持久化（Phase 1）**：
- `DebouncedExplanationWriter`（time + size 雙閘門 throttle）把 chunks 寫入 `stage_progress.full_explanation`；cancel / disconnect / 例外時透過 `finally writer.flush()` 確保已生成部分留在 DB

**跨重啟的持久狀態**：全部存於 SQLite（`data/learning.db`）

**Curriculum 生成（V2 + 背景化）**：
- **In-process（預設）**：`CURRICULUM_USE_ARQ=0` 時，uvicorn 內 `asyncio.create_task` 跑 pipeline；API startup 掃 `list_resumable_sessions()` 自動續跑
- **Arq worker**：`CURRICULUM_USE_ARQ=1` 時，API 只 `prepare + enqueue`；獨立 worker 從 Redis 取 job 執行；重啟 uvicorn **不**中斷生成
- **Checkpoint**：每完成一個 macro region 寫入 `curriculum_checkpoints`；重啟後 skip 已完成 regions
- **LLM cache**：`LLM_CACHE_ENABLED=1` 時 curriculum agents 共用 `llm_result_cache`（同 content_hash + agent + prompt_version）

```
本機 uvicorn (:8000)                    Docker（docker compose up）
      │                                        │
      │  start_session                         │  wl-redis (:6380)
      │  prepare + enqueue ──────────► Redis ◄─┤  wl-curriculum-worker
      │  （Arq 模式）                           │  run_curriculum_job
      └─ in-process task（非 Arq 模式）         └─ resume from checkpoint
```

---

## 2. 啟動流程

```
uvicorn run:app --port 8000
    │
    ├── run.py → 將上層目錄加入 sys.path，讓 backend.* 匯入正常
    │
    └── lifespan(app)
            ├── init_db(DB_PATH)           # 建立 DB 連線、執行 migrations、PRAGMA journal_mode（預設 WAL；`SQLITE_JOURNAL_MODE` 可覆寫）
            ├── inflight_cleanup_dead_workers()  # 清 worker_pid 已不存在的孤兒 lock（Windows 用 OpenProcess）
            ├── inflight_lock.cleanup_stale(max_age_s=600)  # 清前次 worker 強制關閉殘留的孤兒 lock
            ├── upload_gc / llm_cache evict（可選）
            ├── 【僅 CURRICULUM_USE_ARQ=0】curriculum auto-resume：
            │       list_resumable_sessions() → resume_generating_session_background(create_task)
            └── （應用結束）close_db()      # 關 DB 連線
```

**Arq worker 獨立進程**（`CURRICULUM_USE_ARQ=1` 時必開）：

```
python -m arq backend.jobs.arq_settings.WorkerSettings
# 或：docker compose up -d   → wl-redis + wl-curriculum-worker

WorkerSettings.on_startup:
    ├── init_db + cleanup_dead_worker_locks
    └── list_resumable_sessions() → enqueue_curriculum_job(redis, sid)
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
| 008 (Python) | `ALTER TABLE sessions ADD COLUMN question_mode TEXT DEFAULT 'short_answer'` |
| 009 (Python) | 建立 `source_chunks` 表 + index（後端掌控的 source truth，Phase 1）；`CREATE TABLE IF NOT EXISTS` |
| 010 (Python) | `ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT NULL`（書櫃自訂標題） |
| 011 (Python) | `ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0`（單裝置強制登出 — 每次登入 +1，舊 token 的 `sv` 不符即視為無效） |
| 012 (Python) | `ALTER TABLE user_learning_profile ADD COLUMN ui_state_json TEXT DEFAULT '{}'`（跨裝置 UI 狀態：書櫃排序、版面 prefs） |
| 013 (Python) | 建立 `tutor_records` 表 + index（ask_tutor 問答按 stage 持久化）；`CREATE TABLE IF NOT EXISTS` |
| 014 (Python) | `ALTER TABLE tutor_records ADD COLUMN scope TEXT DEFAULT NULL`（三態邊界判定：`current_chapter` / `other_chapter` / `out_of_scope`） |
| 015 (Python) | `ALTER TABLE sessions ADD COLUMN source_file_ids_json TEXT DEFAULT '[]'`（記錄 session 引用的 upload file_ids，供 `delete_session` 時 GC 磁碟 blob） |
| 016 (Python) | 建立 `inflight_locks` 表 + index（Phase 3 Task B：跨 worker dedup lock）；`CREATE TABLE IF NOT EXISTS` |
| 017 (Python) | `concept_mastery` 加 `source_signature`（跨教材 mastery 隔離） |
| 018 (Python) | `concept_mastery` 唯一鍵改為 `(user_id, source_signature, concept_name)` |
| 019 (Python) | `source_chunks` 加 `source_id/source_index/source_label`；`sessions.sources_json` |
| 022 (Python) | 建立 `curriculum_checkpoints` 表（V2 pipeline region 斷點續跑） |
| 023 (Python) | `sessions.target_depth`（generating 期間持久化） |
| 024 (Python) | 建立 `llm_result_cache` 表（curriculum LLM 結果快取） |

Migration 002–005、007–008、010–012、014–015、017、019、023 均用 `try/except` 包裹，已存在欄位時靜默跳過（冪等）。006、009、013、016、022、024 使用 `CREATE TABLE IF NOT EXISTS`。018 以 `concept_mastery_scoped` 一次性遷移。沒有正式的 `schema_migrations` 追蹤表，所有狀態依賴 `try/except`／`IF NOT EXISTS` 的冪等性。`PRAGMA journal_mode` 預設 WAL，可由環境變數 `SQLITE_JOURNAL_MODE` 覆寫（Docker worker 在 Linux volume 內通常用 WAL）。

---

## 3. 資料庫 Schema

### `users`
```
user_id          TEXT PRIMARY KEY
email            TEXT UNIQUE NOT NULL
password_hash    TEXT NOT NULL
session_version  INTEGER NOT NULL DEFAULT 0  # Migration 011：每次登入 +1，舊 token 失效
created_at       TIMESTAMP
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
question_mode        TEXT DEFAULT 'short_answer'
title                TEXT DEFAULT NULL     # 書櫃自訂標題（Migration 010，可透過 PATCH 更新）
source_file_ids_json TEXT DEFAULT '[]'     # Migration 015，sessions 引用的 upload file_id 列表（供 delete_session GC）
target_depth         TEXT DEFAULT NULL     # Migration 023，generating 期間的 target_depth（checkpoint meta 備援）
created_at           TIMESTAMP
updated_at           TIMESTAMP
status 可能值         generating | pending_confirmation | active | completed | abandoned
```

書櫃 stub 與生成中狀態：`start_session` 流程進入 ContentSplitter 之前先 `create_generating_stub`（status='generating'，title='生成中…'），讓書櫃在 LLM 呼叫期間持久顯示。失敗時呼叫 `abandon_generating_stub` 把 status 改為 `abandoned`。`get_user_sessions` 與 `/sessions/list` 會排除 `abandoned`，但保留 `generating`。

### `source_chunks`（Migration 009 新增）
```
chunk_id      TEXT NOT NULL              # 文件層級，格式：chunk_NNNN（非 stage 前綴）
session_id    TEXT NOT NULL → sessions.session_id
order_index   INTEGER NOT NULL           # 在原始文件中的順序
text          TEXT NOT NULL              # 原文（逐字，後端掌控）
section_title TEXT                       # 所屬段落標題（若有）
char_start    INTEGER                    # 字元起點（於原始純文字）
char_end      INTEGER                    # 字元終點
created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
PRIMARY KEY (chunk_id, session_id)
INDEX: idx_source_chunks_session ON source_chunks(session_id)
```
注意：chunker.py 輸出的 chunk dict 含 `page` 欄位（PDF 頁碼），但 DB schema 無此欄位，僅存於 in-memory dict 用於 text_extractor 追蹤。

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
ui_state_json          TEXT DEFAULT '{}'   # Migration 012：跨裝置 UI 狀態（書櫃排序、版面 prefs）
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

### `tutor_records`（Migration 013–014）
```
id          INTEGER PK AUTOINCREMENT
session_id  TEXT NOT NULL
stage_id    INTEGER NOT NULL
question    TEXT NOT NULL
answer      TEXT NOT NULL
in_scope    INTEGER NOT NULL DEFAULT 1  # 0/1 布林（與 scope 共存，舊資料相容）
scope       TEXT DEFAULT NULL           # Migration 014：current_chapter | other_chapter | out_of_scope
created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
INDEX: idx_tutor_records_session ON tutor_records(session_id, stage_id)
```

`ask_tutor` 每筆問答持久化於此。`get_all_tutor_records` 載入時，若 `scope` 為 NULL（舊資料），會用 `in_scope` 反推（`1 → current_chapter`、`0 → out_of_scope`）。

### `inflight_locks`（Migration 016，Phase 3 Task B）
```
key          TEXT PRIMARY KEY        # generation key（ws layer 命名）：
                                     #   sess_X          → start_session / confirm_map / resume_session
                                     #   sess_X:tutor    → ask_tutor
                                     #   sess_X:answer:Q → submit_answer
session_id   TEXT NOT NULL
kind         TEXT NOT NULL           # 'start_session' | 'confirm_map' | 'submit_answer' |
                                     #   'resume_session' | 'ask_tutor'
started_at   REAL NOT NULL           # Unix timestamp，cleanup_stale 用
worker_pid   INTEGER                 # debug 用
meta_json    TEXT                    # 預留擴展欄位
INDEX: idx_inflight_session ON inflight_locks(session_id)
```

跨 worker dedup 機制：`register_async` 嘗試 `INSERT`（PRIMARY KEY 衝突即 lock 已被占有），衝突回 `None`、呼叫端做 race lost 處理。`done_callback` / `finish_async` / `cancel_async` 都會 release lock。`cleanup_stale(max_age_s=600)` 在 FastAPI lifespan startup 跑一次，清掉前次 worker 強制關閉時殘留的孤兒 lock。`cleanup_dead_worker_locks()` 檢查 `worker_pid` 是否仍存活；**Windows** 使用 `OpenProcess`/`GetExitCodeProcess`（`os.kill(pid,0)` 會 WinError 87）。

### `curriculum_checkpoints`（Migration 022）
```
session_id                TEXT PRIMARY KEY → sessions.session_id
content_hash              TEXT NOT NULL
pipeline_version          TEXT DEFAULT 'v2'
pipeline_meta_json        TEXT             # {user_id, target_depth, question_mode, provider_name, model_name}
required_outline_json     TEXT
regions_json              TEXT             # 切分區段列表（unified path 為 single/per-source split 區段；resume 時 skip 已完成區段。MacroRegionPlanner 已刪，欄位沿用）
completed_region_ids_json TEXT DEFAULT '[]'
all_candidates_json       TEXT DEFAULT '[]'
summary_parts_json        TEXT DEFAULT '[]'
meter_json                TEXT DEFAULT '{}'
last_region_id            TEXT
updated_at                TIMESTAMP
INDEX: idx_curriculum_ckpt_updated ON curriculum_checkpoints(updated_at)
```

每完成一個 region 更新 `completed_region_ids_json`；`resume_generating_session` 讀取後 skip 已完成 regions 繼續跑。`list_resumable_sessions()` 回傳 `status='generating'` 且有 checkpoint 的 session_id 列表。

### `llm_result_cache`（Migration 024）
```
cache_key       TEXT PRIMARY KEY    # hash(scope, content_hash, agent, region, prompt_version, model, messages)
scope           TEXT DEFAULT 'curriculum'
content_hash    TEXT
agent_name      TEXT NOT NULL
region_id       TEXT
prompt_version  TEXT NOT NULL       # CURRICULUM_PROMPT_VERSION
model_name      TEXT NOT NULL
result_json     TEXT NOT NULL
input_tokens    INTEGER
output_tokens   INTEGER
hit_count       INTEGER DEFAULT 0
created_at      TIMESTAMP
last_hit_at     TIMESTAMP
INDEX: idx_llm_cache_content_hash, idx_llm_cache_scope_agent, idx_llm_cache_created
```

`CachingLLMProvider` 包裝底層 provider：cache hit 直接回傳 JSON；miss 時呼叫 LLM 並寫入。CLI：`python backend/tools/llm_cache_stats.py`。

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
- 接收：`{email, password}`，建立 user，bcrypt 密碼雜湊；初始 `session_version=1`
- 回傳：`TokenOut`（含 `access_token`、`user_id`、`email`）

### `POST /auth/login`
- 接收：`{email, password}`，驗證後 `session_version += 1`、簽發 JWT
- 回傳：`TokenOut`（含 `access_token`、`user_id`、`email`）
- **單裝置強制登出**：JWT payload 包含 `sv`（session_version 快照）；新登入會讓所有舊 token 的 `sv` 不符，`decode_token_active` 立即視為無效

### `GET /auth/me?token=...`
- 解碼 JWT 並驗證 `sv` 與 DB 中的 `session_version` 相符，回傳當前使用者資訊

### `POST /upload`（需 `Authorization: Bearer <token>`）
- 接收：multipart/form-data，欄位 `file`
- 允許格式：`.txt .md .pdf .docx .pptx .html .htm .epub`；單檔限 `UPLOAD_MAX_FILE_MB` MB（預設 10）
- 將原始 bytes + filename + mime_type 寫入磁碟 `data/uploads/{file_id}.bin` + `{file_id}.meta.json`（跨重啟可讀）
- `meta.json` 結構：`{file_id, filename, mime_type, size, ...extra_meta}`
- 回傳：`{file_id: "upl_<hex>", filename, size, mime_type}`
- **生命週期**：`start_session` 會把 `file_id` 列表寫入 `sessions.source_file_ids_json`；`DELETE /sessions/{sid}` 會呼叫 `delete_upload(file_id)` 清理 `.bin` 與 `.meta.json`

### `POST /upload/url`（需 `Authorization: Bearer <token>`）
- 接收：`{url: "https://..."}`（JSON body）
- 支援公開網頁與 YouTube 影片：
  - 一般網頁：`readability-lxml` 抽正文，並以 `BeautifulSoup` 轉為 Markdown-like 結構文字（保留標題層級、清單、超連結 URL）
  - 網頁抽取低命中時：自動 fallback 全頁清洗抽文（移除 script/style/nav/footer 等）
  - `strict_main`：遇到 `Related Learning / Explore All Courses / Recommended` 等區塊標題後自動截斷，只保留主文章
  - YouTube：`youtube_asr_mode="defer"` — 先用 `youtube-transcript-api`；字幕缺失時 raise `YoutubeTranscriptUnavailable`，REST 以 HTTP 409 回應，由前端決定是否觸發 ASR 端點
- 擷取後存為純文字 `.bin`，`meta.json` 額外記錄 `source_url`、`source_type: "url"`
- 限制：需公開無需登入；不支援需登入/嚴格防爬頁面；單次最多 500,000 字
- 回傳：`{file_id: "upl_<hex>", title, url, char_count}` 或 HTTP 409 `{asr_required: true, video_id, url, title, reason}`

### `POST /upload/youtube/asr/stream`（需 `Authorization: Bearer <token>`）
- 接收：`{url: "https://..."}`（YouTube URL）
- 串流回應 `application/x-ndjson`，每行一個 JSON 事件：
  - `{type: "progress", stage: "download" | "transcribe", progress: 0~1}`
  - `{type: "done", file_id, title, url, char_count}`
  - `{type: "error", message}`
- 後端在獨立 thread 跑 `yt-dlp` 下載音訊 + `faster-whisper`（small/cpu/int8）ASR；暫存音訊位於 `tempfile.TemporaryDirectory(prefix="yt_asr_")`，轉寫結束自動刪除
- 完成後與一般 URL 上傳一致：`meta.json` 標記 `source_type: "url"`、`source_url`

### `GET /sessions/active?token=...`
- 查詢該用戶最新的 active / pending_confirmation session
- 若 pending：回傳 `pending_map`（nodes + summary）
- 若 active：回傳 `stage_statuses`、`stages`、`provider`、`model`

### `GET /sessions/list?token=...`（書櫃）
- 列出用戶所有 session（含 title、status、created_at）

### `GET /sessions/{session_id}?token=...`
- 取得單一 session 詳細資訊
- status = `generating` 時回傳空 stages 與生成中狀態

### `PATCH /sessions/{session_id}/title?token=...`
- 接收：`{title: str}`，更新 session 自訂標題
- 回傳：`{ok: true}`

### `DELETE /sessions/{session_id}?token=...`
- 刪除 session 及其所有 qa_records、stage_progress、source_chunks、decision_records、tutor_records
- 同時呼叫 `delete_upload(file_id)` 清理 `sessions.source_file_ids_json` 中記錄的磁碟 blob（`.bin` + `.meta.json`）
- 不刪除 concept_mastery（保留長期學習記錄）
- 回傳：`{ok: true}`

### `GET /sessions/{session_id}/stages/{stage_id}/explanation?token=...`
- 回傳該 stage 已持久化的講解 Markdown，與 `session_snapshot` 相同的格式轉換邏輯（內含進度表 + 教師區塊）
- 供前端回顧時不必整段重整、不必呼叫 LLM

### `GET /sessions/{session_id}/stages/{stage_id}/qa_history?token=...`
- 回傳該 stage 已持久化的答題紀錄列表（格式與 `session_snapshot.stage_qa_histories` 中單章一致）

### `DELETE /sessions/{session_id}/tutor/{record_id}?token=...`
- 刪除 ask_tutor 單筆問答紀錄
- 回傳：`{ok: true}` 或 HTTP 404

### `GET /learner/stats?token=...`
- 查詢用戶所有 concept_mastery，回傳：
  - `concepts`: 所有概念掌握度列表（含 mastery_score、total_exposures、last_tested）
  - `misconceptions`: 展開的結構化混淆模式列表（含 concept_name、pattern、severity、repair_strategy）
  - `weak_count`: mastery_score < 0.6 的概念數量

### `GET /user/ui-state?token=...`
- 跨裝置 UI 狀態（Migration 012），回傳 `{v, layoutBySession, bookshelfOrder}`；若用戶尚無紀錄回傳 `DEFAULT_UI_STATE`

### `PUT /user/ui-state?token=...`
- 接收：`{layoutBySession: dict, bookshelfOrder: list[str]}`
- 寫入 `user_learning_profile.ui_state_json`（UPSERT）
- 回傳：`{ok: true}`

### `GET /health`
- 回傳 `{"status": "ok"}`

### `GET /config`
- 回傳 `{"default_provider": "claude"}`（或當前 `DEFAULT_PROVIDER` 環境變數的小寫值），供前端決定預設 provider

---

## 6. WebSocket 訊息協定

連線位址：`ws://localhost:8000/ws/{session_id}?token=JWT`

### 客戶端 → 伺服器

| 訊息 type | 必要欄位 | 說明 |
|-----------|----------|------|
| `start_session` | `sources`（新格式）或 `uploaded_file_id`/`content`（舊格式），`provider`，`target_depth`，`question_mode?`，`model?` | 啟動新學習會話 |
| `confirm_map` | `provider?`，`model?` | 確認知識地圖，開始教學 |
| `submit_answer` | `session_id`，`question_id`，`answer` | 提交答案 |
| `resume_session` | `session_id`，`provider?`，`model?` | 重整後恢復 session |
| `ask_tutor` | `question`，`stage_id?` | 學生對教材提出問題；`stage_id` 為 null 時自動取 `wm.current_stage_id` |
| `cancel_generation` | `key?` | 取消指定 key 的 in-flight task；`key` 不指定則嘗試取消該 session 任何 in-flight（fallback） |
| `request_hint` | — | 請求提示（目前回傳固定「即將開放」） |

### 伺服器 → 客戶端

| 訊息 type | 重要欄位 | 說明 |
|-----------|----------|------|
| `kicked` | `message` | 同帳號同 session 在其他裝置重連，此連線被強制關閉（code 4002） |
| `session_generating` | — | `start_session` 開始解析，通知前端生成中 |
| `knowledge_map` | `nodes`，`summary` | LLM 切割完成，等待確認 |
| `session_started` | `session_id`，`total_stages`，`stages`，`stage_statuses?` | 會話啟動、恢復或 advance 後刷新 |
| `session_snapshot` | `stage_explanations`，`stage_qa_histories`，`decision_history`，`tutor_histories` | `resume_session` 時全量推送（含 tutor_records 按 stage_id 分組） |
| `explanation_chunk` | `chunk: str`，`is_final: bool` | 串流講解片段 |
| `explanation_complete` | `stage_id`，`stage_title`，`full_explanation` | 本 stage 講解完成 |
| `explanation_reset` | — | 清空前端講解區 |
| `question` | `question_id`，`text`，`type`，`answer_mode`，`options`，`evidence_chunk_ids`，`stage_id`，`attempt_number` | 發送一道題目 |
| `feedback` | `question_id`，`score`，`feedback_text`，`needs_clarification`，`clarification_question?` | 評分結果 |
| `stage_decision` | `decision`，`message`，`next_stage_id?`，`best_score`，`reason_lines`，`strategy_snapshot` | 進度決策；`strategy_snapshot` 含 selection_reason（Phase 4） |
| `qa_history` | `records` | `_resume_from_stored` 時推送歷史答題 |
| `resume_state` | `current_question?`，`last_feedback?` | `_resume_from_stored` 時推送 |
| `tutor_chunk` | `chunk: str`，`stage_id: int`，`question: str` | ask_tutor 串流片段（Phase 2：逐字渲染） |
| `tutor_reply_complete` | `stage_id`，`question`，`full_answer` | tutor 串流結束（搭配 `tutor_chunk`） |
| `tutor_reply` | `question`，`answer`，`in_scope`，`scope`，`stage_id`，`id?` | DB cache hit 路徑直接 emit 完整答覆（不走 stream）；`scope` 三態，`id` 為 tutor_records.id |
| `generation_cancelled` | `key`，`kind: 'ask_tutor' \| 'other'` | 對應 client `cancel_generation` 回應；前端依 `kind` 決定是否清 streaming bubble（`ask_tutor` 走 `commitStreamingTutorAsCancelled`） |
| `hint` | `message` | 回應 `request_hint`（目前固定「即將開放」） |
| `course_completed` | `message` | 所有 stage 完成 |
| `error` | `message` | 錯誤通知 |

---

## 7. 完整學習流程

### 7.1 上傳資料源

**方式 A：檔案上傳（一般檔案 .txt/.md/.pdf/.docx/.pptx/.html/.htm）**
```
前端 POST /upload（multipart）
    │
    └── upload_store.save_upload(filename, mime_type, raw)
            ├── 產生 file_id（"upl_<hex>"）
            ├── 寫入 data/uploads/{file_id}.bin（raw bytes）
            └── 寫入 {file_id}.meta.json（filename/mime_type/size）

前端收到 {file_id, filename, size, mime_type}
    └── 加入 sources 陣列：{type: "file", file_id, label: filename}
```

**方式 A'：EPUB 上傳（按 TOC 切多章節）**
```
前端 POST /upload（multipart，file=book.epub）
    │
    └── split_epub_by_toc(raw) → [(章節標題, 純文字), ...]
        每章獨立 _save_parsed_upload：
            ├── filename=f"{idx:03d}_{safe_title}.txt"
            ├── extra_meta={source_type:"epub_chapter", epub_filename, chapter_index, chapter_title}
            └── 取得 file_id（每章一個）

前端收到 {epub_chapters: [{file_id, filename, char_count, size, mime_type}, ...],
         total_chapters: N,
         parent_filename: "book.epub"}
    └── UploadModal `addFileSources` 偵測 `epub_chapters` 鍵 → 把單一 placeholder
        展開為 N 個 source items（每個 ✕ 可獨立移除）
```

- EPUB 無 TOC → `group_by_toc` fallback「每個 spine document 一章」
- 0 章 → HTTP 422
- 章節數 > 50 不擋（前端 `console.warn` 提示），使用者可手動刪不想學的章
- `_extract_epub` 已刪除；`extract_text(".epub", ...)` 退化為呼叫 `split_epub_by_toc` 並 join 章節文字（僅本地 chunker 測試/工具會走到）

**方式 B：URL 擷取**
```
前端 POST /upload/url（JSON：{url}）
    │
    └── url_fetcher.fetch_url_content(url)
            ├── YouTube URL
            │    ├── 優先：youtube-transcript-api 取字幕
            │    ├── fallback：yt-dlp 下載音訊 + faster-whisper（small/cpu/int8）ASR 轉寫
            │    └── 再 fallback：oEmbed metadata（title/author/url）
            └── 一般網頁
                 ├── httpx.get + readability-lxml 抽正文
                 ├── 轉 Markdown-like 結構文字（標題/清單/連結）
                 ├── readability 過短時 fallback 全頁清洗抽文
                 └── 噪音過濾 + strict_main 主文截斷
    │
    └── upload_store.save_upload(title.txt, text/plain, utf8_bytes,
                                  extra_meta={source_url, source_type: "url"})

前端收到 {file_id, title, url, char_count}
    └── 加入 sources 陣列：{type: "url", file_id, label: title}
```

**YouTube ASR 暫存行為（2026-05-08）**：
- ASR fallback 下載的音訊位於系統暫存目錄（`tempfile.TemporaryDirectory(prefix="yt_asr_")`）
- 轉寫完成離開 `with` 區塊後，自動刪除暫存音訊（不留在專案目錄）
- Whisper 模型權重快取於 Hugging Face cache（跨重啟可重用，不會自動刪除）

**方式 C：純文字**
```
前端直接構成 source item：{type: "text", content: "...", label: "貼上的文字"}
    └── 不呼叫上傳 API，直接在 start_session payload 的 sources 中傳遞
```

**Upload 生命週期與 GC（Migration 015）**：
- `start_session` 在 `_build_source_chunks_from_payload` 蒐集所有 `type=file|url` 的 `file_id` 後傳給 orchestrator
- orchestrator 把 `file_id` 列表寫入 `sessions.source_file_ids_json`
- `DELETE /sessions/{sid}` 觸發 `delete_upload(file_id)`，刪除 `data/uploads/{file_id}.bin` 與 `{file_id}.meta.json`
- Migration 015 前建立的舊 session 沒有 file_id 紀錄，刪除時不會 GC，需要時可手動清理

### 7.2 啟動新會話（start_session）

```
前端 WebSocket send: {
  type: "start_session",
  payload: {
    sources: [
      {type: "file", file_id: "upl_abc", label: "report.pdf"},
      {type: "file", file_id: "upl_def", label: "001_第一章.txt"},  # EPUB 拆出的章節以一般 file 形式進來
      {type: "url",  file_id: "upl_ghi", label: "Example Article"},
      {type: "text", content: "直接貼上的文字...",      label: "貼上的文字"}
    ],
    same_material: true | false,                        # 必選；1 source 時前端自動視為 true
    provider, target_depth, question_mode, model
  }
}
    │
    main.py
        ├── 驗證 JWT
        ├── _build_source_chunks_from_payload(p, emit)   # 多來源切 chunk + 全域編號（同前）
        ├── same_material = bool(p.get("same_material", True))   # 讀 payload；預設 True
        ├── create_provider(provider_name, model?)
        │
        ├── 【Arq 路徑】CURRICULUM_USE_ARQ=1：
        │       prepare_curriculum_session(..., same_material=same_material)
        │           → 寫 sessions（含 same_material INTEGER）/ source_chunks / checkpoint pipeline_meta
        │       enqueue_curriculum_job(redis, session_id)
        │       emit session_generating；不 create_task（worker resume_generating_session）
        │
        ├── 【In-process 路徑】
        │       LearningOrchestrator(llm)
        │       asyncio.create_task(orchestrator.start_session(..., same_material=same_material))
        │
        └── orchestrator.start_session(...) 直接 delegate `run_start_session_v2`
            （`_start_session_v1` 已刪；不再有 `CURRICULUM_PIPELINE_V2` env 分叉）

`curriculum_pipeline_v2.run_start_session_v2(..., same_material)`：
    │
    ├── checkpoint 載入；resume 從 DB session row 讀 same_material（NULL = legacy → True）
    │
    ├── ContentOutlineAgent（**只在 `same_material=False` 時跑**；Phase 3 2026-05-29 收斂自 P0a）
    │     產物：required_outline = {named_cases, required_stage_titles, must_cover_topics}
    │     `run_outline = not same_material`。同教材（含 ≥3 章 EPUB）一律跳過：
    │     global outline 的跨章 named_cases 會把不同章同主題 chunk 併進同一 stage
    │     （章節邊界破壞器，live sess_f9qt8rac9 7.1=第6+8章），章序改由 SourceOrderResolver 處理
    │
    ├── 唯一切分分支（n_sources 決定）：
    │     n_sources <= 1 → `_run_single_split`（整包一次 Splitter+Verifier）
    │     n_sources >  1 → `_run_per_source_split`（每 source 各跑一次，**P1b: 帶 chapter_title hint**）
    │     兩者都把 required_outline 餵給 splitter 當 hint
    │     SplitterVerifier **非阻塞**：失敗只走確定性 false-positive filter + bounded reroll，
    │       **不中止 session**（舊 fail-hard `SplitterVerificationRejected` / `MAX_SPLITTER_VERIFY_RETRIES` 已移除）
    │     **P2a**：filtered missing 數 < `SPLITTER_VERIFIER_MIN_MISSES`（預設 2）→ soft-pass 省 reroll
    │
    ├── `candidates_to_stages_flat`（無 LLM reducer）
    ├── quality_warnings = {small_file_path: True, reducer_skipped: True, multi_source_split?, source_count?}
    ├── `assess_reducer_health`（vestigial：candidate/outcome/unsure 都 0，僅輸出 healthy 狀態）
    │
    ├── **Phase 1 mode-aware 後處理（2026-05-29）**：`postprocess_mode = choose_postprocess_mode(n_sources, same_material)`
    │     `allow_merge = postprocess_mode == "cross_material_merge_and_coordinate"`（只有「多本不同書」為 True）
    │     單 source / 同教材：`allow_merge=False` → **跳過下面所有語意合併**，只做確定性排序 + 收尾
    │     寫入 quality_warnings.postprocess_mode / source_order / outline_skipped_same_material（同教材 ≥3 章）
    │
    ├── `merge_duplicate_topic_stages(threshold=duplicate_title_threshold())`（一律跑，純標題去重）
    │     env `STAGE_TITLE_MERGE_THRESHOLD`（預設 0.85；越界值 fallback 至預設）
    ├── **P0b-1**: `merge_by_concept_overlap(...)`（**僅 allow_merge**）
    │     env `STAGE_CONCEPT_OVERLAP_THRESHOLD`（預設 0.6）— jaccard 抓跨 source 命名漂移
    ├── `enforce_stage_ordering` + `merge_singleton_chunk_stages`（一律跑，確定性排序/薄節清理，非語意合併）
    ├── **P0b-2**: **allow_merge 且** chunks ≥ 30 時跑 `StageConsolidatorAgent`（LLM 全局 rename + reorder + merge；legacy，Phase 4 將改 plan-based）
    │     硬約束：不可新增/移除 chunk_id；驗證失敗 fallback 沿用原 stages，記到 `quality_warnings`
    ├── `verify_global_coverage`（named_cases + orphan chunks + duplicate titles）
    │     不對齊時 **非阻塞**：`SPLITTER_FAIL_MODE` 只切 warning 通道（soft=寫 quality_warnings；hard 預設=寫 WARNING log），
    │       接著 `fold_interior_orphan_chunks`（內部孤兒併入相鄰 stage）→ `_build_follow_up_stages` 補 missing named_case / orphan → re-verify
    ├── `finalize_small_file_stages`（compact 路徑）或 `finalize_curriculum_stages`（路徑收尾規則）
    ├── **P2b**：orphan attach 後 re-verify；剩餘 orphan 寫 `quality_warnings.orphan_attach_incomplete`
    ├── `cleanup_orphan_enumerator_titles`（**僅 `same_material=True`**；finalize 匯流點、含 compact path）
    │     title-only 移除無 sibling 的孤兒序號標題（「模式二：X」無「模式一」）；不 relabel、不動 chunk/kc；
    │     寫 `quality_warnings.title_cleanup_removed_orphan_enumerators` + log `v2 title cleanup ... count=N`（count=0 無 log 屬正常）
    ├── 條件式 `ConceptCanonicalizeAgent`（env `CONCEPT_CANONICALIZE=1` 才跑；**預設 off → 現行主線不跑**）
    ├── `assess_curriculum_cost` → `quality_warnings.curriculum_llm_calls` / `_tier` / `_over_budget`
    └── WS：`reduce_done`（含 small_file 健康狀態）/ `knowledge_map` / `composer_done`
```

> 已刪除（2026-05-27 unify-v2-small-file-pipeline 分支起）：
> - V1 整套（`_start_session_v1` + 五檔一次切 + V1 ConceptCanonicalize）
> - V2 大檔流程：`MacroRegionPlannerAgent` / `GlobalCurriculumReducer` agent / Step A/B/C / `_splitter_stages_to_candidates` / `_dedupe_candidates`
> - Plan B / Plan B 自動 fallback
> - 對應 env：`CURRICULUM_PIPELINE_V2` / `SMALL_FILE_CHUNK_THRESHOLD` / `SMALL_FILE_FORCE_OUTLINE` / `MACRO_REGION_USE_LLM` / `CURRICULUM_V2_PLAN_B` / `CURRICULUM_V2_PLAN_B_AUTO` / `REDUCER_FAIL_MODE`
>
> ⚠️ **未刪但非主線（2026-05-30 校正）**：`global_curriculum_reducer` / `macro_region_refiner` 兩個 **prompt 仍在 `prompt_templates.py`**（`SYSTEM_PROMPTS` 現共 13 keys），`utils/curriculum_reducer.py` 與 `reducer_constants` 也還在被 import（僅取 `MAX_MERGED_OUTCOME_CHUNKS` 常數 + health metrics）。但 unified path `reducer_skipped=True`，**主線不跑 reducer planning**。正常驗收看不到 reducer log，屬正常。
>
> 詳見 `docs/superpowers/specs/2026-05-27-curriculum-unify-v2-design.md` §5

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
    ├── 【步驟 2】串流講解（TeacherAgent）— Phase 3 Task A 起改用 stream_explanation_with_intent
    │   TeacherAgent.stream_explanation_with_intent(ctx)
    │       task_payload: {stage, adaptive_context: adaptive_ctx, prev_stage_title}
    │       system prompt 包含：
    │           學生掌握度、混淆模式、最近答題、
    │           must_reinforce、forbidden_future、selection_reason（Phase 4）
    │           + 尾段 <<INTENT_JSON>>{...}<<END_INTENT>> 標記區塊指示
    │       每個 chunk（標記前的純文字）→ emit: explanation_chunk（is_final=False）
    │       標記區塊內 chunks 累積但不外送、結束後 parse 到 self.teacher.last_intent
    │       透過 DebouncedExplanationWriter (Phase 1) throttle 寫入 stage_progress.full_explanation
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
    ├── 【步驟 3】提取教學意圖（Phase 3 Task A 改為 inline）
    │   teaching_intent = self.teacher.last_intent or await teacher.extract_teaching_intent(full_explanation, stage)
    │       # 主路徑：直接用 stream 共生的 INTENT JSON 區塊（last_intent 是 dict 或 None）
    │       # Fallback：last_intent is None 時走獨立 LLM extract_teaching_intent
    │       # 輸出：{reinforced_concepts, analogies_used, repair_target, main_chunk_ids}
    │       #   或新格式 {key_concepts, expected_misunderstandings, evidence_chunk_ids}
    │   wm.current_teaching_intent = teaching_intent
    │
    ├── 【步驟 4】生成問題（QuestionGeneratorAgent）
    │   task_payload: {stage, teaching_intent, allowed_evidence, num_questions, ...}
    │       ├── 問題與 teaching_intent.repair_target 對齊（至少 1 題）
    │       ├── 問題與 analogies_used 對齊（至少 1 題）
    │       ├── **出題範圍嚴格限制（2026-05-19）**：題目測試的概念必須在
    │       │   full_explanation 中明確出現並有解釋；chunks 提到但講解未涵蓋
    │       │   的概念不得出題
    │       └── evidence 優先用 allowed_evidence（真實 DB 原文）
    │   DriftVerifierAgent 驗證（content_type=questions，questions 模式以
    │       full_explanation 為唯一對齊基準）：
    │       ├── aligned=True → 採用
    │       ├── aligned=False →
    │       │     1. _build_question_retry_guidance(verify_result) 把
    │       │        unsupported_claims（前 5 條）注入 retry prompt
    │       │     2. 重新生成 questions
    │       │     3. **重試後再次 DriftVerifier**；仍漂移的題目
    │       │        透過 _tag_drifting_questions 比對前後 30 字 fuzzy match，
    │       │        在 text 前加 `[註：本題未對齊講解]` 軟性標記
    │       │        並設 `_grounding_status="drift_after_retry"`（冪等）
    │   wm.pending_questions = questions
    │   session_memory.store_stage_questions(...)
    │   ⚠️ 三處 retry 區塊（run_stage / remediate / reteach）共用同一對 helper
    │      方法（_build_question_retry_guidance、_tag_drifting_questions），
    │      差別僅在 full_explanation 來源變數（full_explanation /
    │      wm.current_explanation / teacher_only）
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
    │   前置計算：
    │       scores          = 各題分數（MC 先套猜測校正，short_answer 直接用原始分）
    │       best_score      = max(scores)
    │       latest_score    = scores[-1]
    │       high_severity   = misconception_patterns 中 severity=="high" 的項目
    │       repeated_patterns = 同一 pattern 字串出現 >= 2 次
    │       unique_confused = 所有評估的 confused_concepts 去重
    │       mastery         = _mastery_state(scores, unique_confused, pass_threshold)
    │           "complete"  ← best >= 0.75 AND confused 為空
    │           "none"      ← best < 0.5 AND avg < 0.5
    │           "partial"   ← 其餘
    │       is_child_stage  = stage_kind in {"reteach", "remediation"}
    │
    │   子章節（kind = reteach / remediation）決策優先序：
    │   1. mastery == "complete"
    │      → advance
    │   2. high_severity AND source_reteach_count < max_reteach
    │      → reteach（子章節仍有根本誤解，升級重教）
    │   3. stage_kind=="reteach" AND mastery=="none" AND source_reteach_count < max_reteach
    │      → reteach（重教子章節全失，再插一輪）
    │   4. stage_kind=="reteach" AND mastery=="none" AND source_remediation_count < max_remediation
    │      → remediate（重教次數達上限，改補強）
    │   5. source_remediation_count < max_remediation
    │      → remediate（partial 或 remediation 子章節的預設）
    │   6. else（reteach + remediation 雙上限皆滿）
    │      → advance（強制前進）
    │
    │   主章節決策優先序（2026-05-06 重新整理）：
    │   1. mastery == "complete"
    │      → advance（best >= 0.75 且無混淆概念，真正掌握才前進）
    │   2. best_score >= 0.75 AND unique_confused 非空
    │      → remediate（分數到標但仍有弱點，補強後再前進）
    │   3. high_severity 存在
    │      → reteach（根本誤解，立即換框架）
    │   4. repeated_patterns == True
    │      → reteach（同一錯誤重複）
    │   5. mastery == "none" AND attempts == 1
    │      → retry（首次全錯，先給一次機會再決定是否重教）
    │   6. mastery == "none"
    │      → reteach（整章尚未建立理解）
    │   7. mastery == "partial" AND attempts >= max_attempts AND unique_confused 非空
    │      → remediate（局部缺口，次數用完改走補強）
    │   8. mastery == "partial" AND attempts < max_attempts AND best_score >= 0.5
    │      → retry（只做同章再測）
    │   9. attempts == max_attempts AND latest_score < 0.5
    │      → reteach
    │  10. otherwise
    │      → remediate
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
    │   若 next_stage_idx is None → 課程結束（complete_session + course_completed）
    │
    │   【Phase 4：建立選課理由】
    │   selection_reason = {
    │       reason: "弱點重疊度=N，低掌握概念數=M，模式",
    │       target_concepts: [掌握度 < 0.75 的概念],
    │       stable_high: bool,
    │   }
    │
    ├── 【reteach】
    │   _insert_reteach_stage(...)（T.source.N 重教子章節）
    │   原章節講解與 QA 不覆寫，使用者直接進入新子章節
    │
    ├── 【remediate】
    │   _insert_remediation_stage(...)（R.source.N 補強子章節）
    │   原章節講解與 QA 不附加，使用者直接進入新子章節
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
   │   session_memory.store_stage_explanation(
   │       _pack_persisted_explanation(progress_md, combined)
   │   )                                                   ← 以統一格式持久化（含進度表 + 教師內容）
    │   session_memory.store_stage_questions(questions)
    │   emit: explanation_complete（full_explanation = 累積講解文字）
    │   emit: question（第一道新題）
    │
    └── 【remediate / reteach 後續】
        將目前章節標記 completed（branched_to = decision）
        emit: session_started（含新增子章節與 stage_statuses）
        run_stage(next_stage_idx, ...)
        ⚠️  reteach 不再送 explanation_reset，也不覆寫原章節 full_explanation
```

**主章節決策觸發條件（2026-05-06 修正版）**：

| 決策 | 觸發條件 | 優先序 |
|------|----------|--------|
| `advance` | mastery == "complete"（best ≥ 0.75 且無混淆概念） | 1（最高） |
| `remediate` | best_score ≥ 0.75 但仍有 confused concepts | 2 |
| `reteach` | high severity misconception（任何嘗試次數） | 3 |
| `reteach` | 同一 pattern 重複 ≥ 2 次 | 4 |
| `retry` | mastery == "none" 且 attempts == 1（首次全錯，先給機會） | 5 |
| `reteach` | mastery == "none"（attempts > 1） | 6 |
| `remediate` | mastery == "partial" 且 attempts >= max_attempts 且有明確 confused concepts | 7 |
| `retry` | mastery == "partial" 且 attempts < max_attempts 且 best_score >= 0.5 | 8 |
| `reteach` | attempts == max_attempts AND latest < 0.5 | 9 |
| `remediate` | 其餘情況 | 10（最低） |

**子章節決策觸發條件**：

| 決策 | 觸發條件 | 優先序 |
|------|----------|--------|
| `advance` | mastery == "complete" | 1 |
| `reteach` | high_severity 且 source_reteach_count < 2 | 2 |
| `reteach` | stage_kind=="reteach" 且 mastery=="none" 且 source_reteach_count < 2 | 3 |
| `remediate` | stage_kind=="reteach" 且 mastery=="none" 且 source_remediation_count < 2 | 4 |
| `remediate` | source_remediation_count < 2 | 5 |
| `advance` | 雙上限皆滿（強制前進） | 6（最低） |

**動態節點類型**：

| 類型 | node_id | 觸發時機 |
|------|---------|---------|
| 重教子章節 | `T.source.N` | reteach，且同一原章節重教次數 < 2 |
| 補強子章節 | `R.source.N` | remediate，且同一原章節補強次數 < 2 |

> 註：原規劃中的「整合挑戰節點（E.N）」與 `WorkingMemory.enrichment_stage_added` 機制已於 2026-05 移除，advance 後若無剩餘節點直接走 `complete_session` 結束課程。

**最壞情況套娃深度（上限保護）**：
```
主章節 → T.1.1（reteach）→ T.1.2（high_severity reteach）
       → R.1.1（remediate）→ R.1.2（remediate）→ advance（雙上限滿）
最多 4 個子章節後必定前進，不會無限套娃。
```

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
前端 WebSocket send: {type: "ask_tutor", payload: {question, stage_id?}}
    │
    orchestrator.handle_student_question(...)
        ├── 解析 effective_stage_id（payload.stage_id 或 wm.current_stage_id）
        ├── 建構 judge_source：只用當前 stage 的 source_chunks（避免全文截斷）
        ├── 建構 chapter_index：全課程章節索引（動態節點標示「源自 X.X」）
        ├── 【三態邊界判定】LLM（scope_judge prompt，回傳 JSON）
        │     {scope, relevant_node_ids, reason}
        │     scope ∈ {current_chapter, other_chapter, out_of_scope}
        │     relevant_node_ids：當 scope=other_chapter 時列出非動態章節 node_id
        │     向後相容：舊 schema 回傳 {in_scope: bool} 也能解析
        │
        ├── 依 scope 決定 answer_source：
        │     current_chapter → judge_source（當前章 source_chunks）
        │     other_chapter   → 過濾動態節點（改用父章節）→ 取相關章節 chunks
        │     out_of_scope    → 全文 source_corpus
        │
        ├── 若 scope=out_of_scope → await search_web(question, max_results=3)
        │     （httpx.AsyncClient，非阻塞 DuckDuckGo Instant Answer API）
        │
        ├── 【生成回答】LLM（tutor_reply prompt）
        ├── 持久化：insert_tutor_record(stage_id, question, answer, in_scope, scope)
        │     → 回傳 record_id（前端可用於 DELETE /sessions/{sid}/tutor/{rid}）
        └── emit: tutor_reply（question, answer, in_scope, scope, stage_id, id?）

前端收到 tutor_reply：
    ├── addTutorMessage(msg) → 追加至 Zustand tutorHistory 陣列
    ├── localStorage.setItem('wl_tutor_history', JSON.stringify(updated))
    └── AskTutorPanel 顯示可收縮筆記（HistoryNote 元件，最新展開）
        ├── 每筆記錄顯示：問題摘要 + scope 標籤（current_chapter/other_chapter/out_of_scope）+ ReactMarkdown 回答
        └── 整份 tutorHistory 在頁面重整、重新登入後自動恢復（後端 tutor_records 為來源）
```

**重連去重**：main.py 在 ask_tutor 進入前會檢查 `_active_generations` Event，若同 session 仍有未結束的 ask_tutor，先 `await wait_for(... timeout=60)`，再從 `tutor_records` 查同問題的快取回應，若有則直接 `emit` 快取結果，避免 LLM 重跑。

---

## 8. 七個核心元件詳解

所有 Agent 繼承 `BaseAgent`；`_messages` 在進入點開始與結束時都呼叫 `_reset()` 清空，防止跨呼叫上下文污染。`BaseAgent.run()` 為可選預設實作（raise `NotImplementedError`）—— `ProgressManagerAgent` 純規則、`TeacherAgent` 使用 `stream_explanation` / `extract_teaching_intent`，其餘 Agent 才覆寫 `run()`。

### 8.1 ContentSplitterAgent

**職責**：將後端 source_chunks 切割為邏輯 stage 序列

**呼叫時機**：`start_session`

**Token 預算**：`max_context_tokens=4000`

**輸入（Phase 1 改版）**：
- `source_chunks: list[dict]` — 後端已切好的 chunk 列表（含 chunk_id、text、order_index）
- `max_stages: int` — 由 `compute_dynamic_max_stages()` 動態計算（含 outline named_cases×4）
- `target_depth: str` — beginner / intermediate / advanced

**Prompt 重點（Phase 1）**：
- LLM 只做語義切分，**不生成任何引用文字**
- 回傳 `source_chunk_ids`（引用後端 chunk_id），不生成 quote
- 標記 `chunk_roles`：core / example / transition / ignored
- 不要把每個 chunk 都變成一個 stage（語義合併）

**正規化後處理（`_merge_thin_stages`，2026-04-30 新增）**：
- 前向掃描：`source_chunk_ids < 2` 的 stage 自動合併至後繼 stage（繼承 chunk_ids、source_chunks、key_concepts）
- 最後一個 stage 若仍只有 1 chunk，合往前一個 stage
- 合併後重新編號 `stage_id`（1 起算），消除 orchestrator 的 `possibly_too_small` 警告

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

**展開義務（grounding，2026-05-19 新增）**：teacher prompt 重要限制第 7 條規定，講解中提及的「專有名詞、術語、工具名、機制名、命名概念」（如「理財型房貸」「斷路器」「polling」）都必須至少一句解釋運作 / 特性 / 意義，不可只當道具引用。若教材未涵蓋運作細節，改用淺白詞彙描述其作用、**不在講解中提及該名詞**（避免後續 QuestionGenerator 誤把該名詞當教過的概念出題）。類比場景的虛構名稱（如「圖書館」「保險絲」）不受此限。

> 這條規則與 DriftVerifier questions 模式的「字面提及 vs 有展開」雙管把關（§8.7）：Teacher 從源頭預防漏展開、DriftVerifier 兜底抓 Teacher 偶爾偷懶的情況。

**串流方法**：

- **`stream_explanation_with_intent(ctx)`**（主路徑、最新）：包裝 LLM stream，偵測尾段的 `<<INTENT_JSON>>{...}<<END_INTENT>>` 標記區塊：
  - 標記前的純文字 chunks 原樣 `yield`（前端正常顯示講解；buffer 留 `len(MARKER)-1` 字元餘量，避免標記被切半送出）
  - 進入標記區塊後 chunks 累積到 `intent_buffer`、**不外送**
  - 收到 END marker 後 `json.loads(intent_buffer.strip())` 存進 `self.last_intent`
  - 若 LLM 沒輸出標記區塊，`emit` 正常結束、`self.last_intent = None`
- **`stream_explanation(ctx)`**（既有）：純串流，無 intent 抽取；保留供未來需要時呼叫

**Prompt 結尾的 INTENT 標記指示**（已加入 `SYSTEM_PROMPTS["teacher"]` 尾段）：

```
【教學意圖標記區塊（強制）】
講解結束後，必須在最後加上以下標記區塊（一字不差）：

<<INTENT_JSON>>
{
  "key_concepts": ["...本節要傳達的核心概念，依重要性排序"],
  "expected_misunderstandings": ["...學生可能會搞錯的點"],
  "evidence_chunk_ids": ["chunk_0001", "..."]
}
<<END_INTENT>>
```

注意：prompt 為 f-string `.format()` 模板，JSON 大括號實際寫成 `{{` `}}`。

**`extract_teaching_intent(explanation_text, stage)`**（fallback）：
- 串流結束後，非串流呼叫 LLM 分析講解全文
- 輸出：`{reinforced_concepts, analogies_used, repair_target, main_chunk_ids}`
- **僅 fallback 觸發**：`run_stage` line 712 走 `self.teacher.last_intent or await self.teacher.extract_teaching_intent(...)`，若 inline 抽出失敗才呼叫
- **第二個獨立呼叫點**：`run_stage` line 1666 — resume 重整時若 DB 已有講解但沒題目、補生題目時呼叫（無 streaming context 可用，必須走獨立 extract）

Phase 3 收益：5/5 stage 命中 inline 抽取，fallback 0 次觸發，每個 stage 省下 2–5 秒 LLM 來回。

### 8.4 QuestionGeneratorAgent

**職責**：生成與教學意圖對齊的布魯姆式問題

**呼叫時機**：`run_stage` 與 `retry` 後重新出題；`remediate` / `reteach` 先插入獨立子章節，再由該子章節的 `run_stage` 產題

**輸入（Phase 3 升級）**：
- `stage` — 節點定義
- `teaching_intent` — TeacherAgent 提取的教學意圖（Phase 3）
- `allowed_evidence` — ContextBuilder 提供的 DB 原文（Phase 2）
- `num_questions`、`attempt_number`、`previous_question_ids`、`question_mode`

**Prompt 新增（Phase 3，2026-04-30 修正類比隔離）**：
```
本篇講解的教學意圖：
- 補強概念：{reinforced_concepts}
- 教師使用的類比（僅供理解教學側重，這些類比是教師自創的說明工具，
  不存在於 source_chunks，禁止把類比細節當成題目素材）：{analogies_used}
- 修正目標：{repair_target}

→ 至少一題直接測試修正目標（若有）
→ 問題應測試學生是否理解補強概念的核心原理（依據 source_chunks），
  而非測試類比的情境細節
```

> ⚠️ 修正原因：Phase 3 舊版要求「至少一題檢驗類比框架」，LLM 誤把 TeacherAgent 自創的比喻情境（如「超市收據」「自來水管線」）當作 source_chunks 中的原始素材出題，導致 DriftVerifier 必然 fail。修正後明確標示類比為教師工具，禁止作為題目素材。

**出題範圍嚴格限制（2026-05-19 新增，最高優先）**：
```
題目測試的概念必須同時滿足：
1. 在 source_chunks 中有原文支撐
2. 在本篇 full_explanation 中明確出現並有解釋

若 chunks 提到但講解未涵蓋的概念（例如 chunks 有 polling、circuit breaker，
但講解沒展開）→ 禁止作為題目主題。
規則 5 從舊版「source_chunks 或 full_explanation」收緊為「必須出現在
full_explanation」。
```

> ⚠️ 修正原因：實測發現 LLM 會出 chunks 提到但講解略過的概念（如 polling），學生看完講解後完全答不出，違反「題目應檢驗講解理解」的契約。新規則由 QuestionGenerator prompt 與 DriftVerifier questions 模式雙重把關。

**Evidence 來源**：優先 `allowed_evidence`（DB 真實原文，key: `text`），退回 `stage.source_chunks`（key: `quote`）

**num_questions**：
- `multiple_choice`：`max(4, estimated_questions * 2)`
- `short_answer`：`estimated_questions`（重試固定 2）

**JSON repair（2026-05 補強）**：與 `ContentSplitterAgent` 一致的 3-attempt repair — 解析失敗時請 LLM 用「JSON 修復器」system prompt 修正格式，最多重試兩次後才 raise，避免 LLM 偶發 schema 錯誤直接讓整個 stage 出題失敗。

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

**決策邏輯（2026-05-06 重新整理）**：
```
前置計算：
    mastery = _mastery_state(scores, unique_confused, pass_threshold)
        "complete" ← best >= 0.75 AND confused 為空
        "none"     ← best < 0.5 AND avg < 0.5
        "partial"  ← 其餘
    is_child_stage = stage_kind in {"reteach", "remediation"}

子章節（kind = reteach / remediation）：
1. mastery == "complete"                                    → advance
2. high_severity AND source_reteach_count < 2               → reteach（升級重教）
3. stage_kind=="reteach" AND mastery=="none"
   AND source_reteach_count < 2                             → reteach（再插重教）
4. stage_kind=="reteach" AND mastery=="none"
   AND source_remediation_count < 2                         → remediate（重教上限改補強）
5. source_remediation_count < 2                             → remediate
6. else（雙上限滿）                                          → advance（強制前進）

主章節：
1. mastery == "complete"                                    → advance
2. best_score >= 0.75 AND unique_confused 非空              → remediate（高分但有弱點）
3. high_severity 存在                                        → reteach
4. repeated_patterns == True                                → reteach
5. mastery == "none" AND attempts == 1                      → retry（首次全錯先給機會）
6. mastery == "none"                                        → reteach
7. mastery == "partial" AND attempts >= max_attempts
   AND unique_confused 非空                                  → remediate
8. mastery == "partial" AND attempts < max_attempts
   AND best_score >= 0.5                                    → retry
9. attempts == max_attempts AND latest_score < 0.5          → reteach
10. otherwise                                               → remediate
```

**輸出**：`{decision, message, best_score, remediation_focus, high_severity_misconceptions, repeated_patterns_detected}`

### 8.7 DriftVerifierAgent

**職責**：驗證 LLM 生成的講解或問題是否紮根於原始教材，防止幻覺與錯誤引用

**呼叫時機**：TeacherAgent 串流完成後（explanation）、QuestionGeneratorAgent 完成後（questions）；重教內容在獨立子章節的 `run_stage` 中驗證

**Citation Accuracy 升級（Phase 4）**：

```python
def _extract_cited_chunks(candidate_text, source_chunks):
    # 用 \bchunk_\w+\b 模式提取所有 chunk_id 引用
    # 同時支援 Markdown 格式 [chunk_0001] 與 JSON 格式 ["chunk_0001"]
    # 查詢 source_chunks 取對應原文
    # 回傳 [{chunk_id, text, found}]
```

> ⚠️ 修正說明（2026-04-30）：舊版使用 `\[([^\]]+)\]` 正則，在 JSON 格式（QuestionGenerator 輸出）中會抓到 `"chunk_0000"`（含引號），與 chunk_map 的 key `chunk_0000` 不符，導致全部 `found=False`，citation lookup 完全失效。改用 `\bchunk_\w+\b` 後同時相容兩種格式。

傳給 LLM 的資料包含 `cited_chunks_lookup`（引用 id + 對應原文），LLM 可逐條驗證「主張是否確實被該 chunk 支撐」，而非只做形式引用檢查。

後端強制（2026-05 修正）：
1. `found=False` 的 chunk_id 一定 append 一筆 `supported=False` 的 claim_check（若 LLM 沒列入）
2. 最終 `aligned = llm_aligned AND not has_unsupported`，只要任一 `claim_check.supported=False`，後端把 `aligned` 強制設為 False；不再單純信任 LLM 的 `aligned` 欄位

**輸入**（`task_payload`）：
- `content_type` — `"explanation"` 或 `"questions"`
- `source_chunks` — 本 stage 的 source_chunks
- `candidate_text` — 待驗證文字
- `full_explanation` — **questions 模式必填**（2026-05-19）：本 stage 已驗證的完整講解，作為出題對齊基準

**questions 模式嚴格對齊（2026-05-19）**：
- prompt 由舊版「寬鬆模式（chunks 或 explanation 任一支撐即可）」改為「嚴格對齊講解模式」
- LLM 收到的 USER message 多一段 `full_explanation（本次課程已驗證講解，出題對齊基準）`（drift_verifier.py:47–51）
- 判定規則：題目測試的概念**僅以 full_explanation 為基準**；即使 source_chunks 中提及，只要 full_explanation 沒明確展開，視為漂移
- **字面提及 ≠ 有展開說明**（2026-05-19 追加 A）：判定一個概念「在 explanation 中有講解」必須對該概念至少一句「運作 / 特性 / 機制 / 原因 / 例子」展開說明，不能只把名詞當道具引用。三態判定：（1）沒提 → 漂移（2）字面提及但未展開 → 漂移（3）有展開 → 對齊
- prompt 內附 3 個 few-shot：
  - 範例 A（漂移：字面也沒出現）— polling drift
  - 範例 B（對齊：有展開）— circuit breaker
  - 範例 C（漂移：字面提及但未展開）— 理財型房貸 mentioned-but-not-explained 案
- runtime label：`出題對齊基準`（drift_verifier.py:48，已從舊「出題驗證寬鬆模式依據」改名）

**Retry 與軟性標記（2026-05-19，三處 retry 點共用）**：

Orchestrator 在 questions 模式 `aligned=False` 時，由 helper 方法統一處理：

```python
def _build_question_retry_guidance(self, verify_result: dict) -> str:
    """注入 retry prompt：提示 LLM 上一輪哪些題目漂移到範圍外（取前 5 條）。"""
    unsupported = verify_result.get("unsupported_claims") or []
    if not unsupported:
        return "（對齊修正要求：請每題僅依 source_chunks 設計，並補 evidence_chunk_ids）"
    bullets = "\n".join(f"- {claim}" for claim in unsupported[:5])
    return (
        "（對齊修正要求：上一輪以下題目漂移到未在講解中提及的範圍，請完全避免：\n"
        f"{bullets}\n"
        "出題只能測試講解全文中明確出現並有解釋的概念。）"
    )

def _tag_drifting_questions(self, questions, unsupported_claims) -> int:
    """retry 後仍漂移者，前綴 `[註：本題未對齊講解]`（冪等，已標記者跳過）。
    比對方式：fuzzy match question.text 前 30 字 與 claim 前 30 字。"""
```

流程：
1. 第一輪 questions verify `aligned=False` → 用 `_build_question_retry_guidance` 注入 retry prompt → QuestionGenerator 重生
2. **重試後再 verify 一次**（content_type=questions，帶 full_explanation）
3. 仍 `aligned=False` → `_tag_drifting_questions(questions, verify_result["unsupported_claims"])` 對每題 fuzzy match，命中者：
   - `text` 前加 `[註：本題未對齊講解] `
   - 設 `_grounding_status = "drift_after_retry"`
   - 已標記過則跳過（冪等）
4. 帶標記的 questions 仍寫入 `session_memory.store_stage_questions(...)` 持久化，使用者可看見軟性提醒，前端不阻斷流程

三處 retry 區塊位於 `learning_orchestrator.py` 約 line 761（run_stage）、1222（remediate）、1713（reteach），差別僅在 `full_explanation` 來源變數（`full_explanation` / `wm.current_explanation` / `teacher_only`）。

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

| Provider | system_prompt 傳遞方式 | 預設 model |
|----------|----------------------|-------------|
| ClaudeProvider | 獨立 `system` 參數（Anthropic SDK） | `claude-sonnet-4-6` |
| OpenAIProvider | messages[0] 插入 system role；GPT-5 系列改用 `max_completion_tokens` | `gpt-5.4-mini` |
| GeminiProvider | `config.system_instruction`（新 `google.genai` SDK） | `gemini-3-flash-preview` |
| MonicaProvider | OpenAI 相容（透過 `MONICA_BASE_URL`/`MONICA_API_KEY`） | `claude-4.6-sonnet` |
| DeepSeekProvider | 繼承 OpenAIProvider，呼叫 `https://api.deepseek.com`；推理模型 reasoning_content fallback | `deepseek-v4-flash` |

### 工廠函式

```python
llm = create_provider("claude" | "openai" | "gemini" | "monica" | "deepseek", model=None)
# 名稱大小寫不敏感（"DeepSeek" → deepseek）
```

> Phase 1 後，Files API 主路徑改為後端 `text_extractor` + `chunker`，原 `llm/file_adapter.py`（多 provider 上傳 adapter）已於 2026-05 清理（無人引用）。DeepSeek 走 OpenAI 相容 endpoint，需設定 `DEEPSEEK_API_KEY`；推理模型輸出在 `reasoning_content` 時自動 fallback 取用。Monica 同樣使用 OpenAI 相容 API，需設定 `MONICA_BASE_URL` 與 `MONICA_API_KEY`。

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
| `global_curriculum_reducer` | **⚠️ legacy / 非主線**：原 GlobalCurriculumReducerAgent（V2 Step B unsure pairs）。prompt 仍在但 unified path `reducer_skipped=True`，主線不呼叫 |
| `macro_region_refiner` | **⚠️ legacy / 非主線**：原 MacroRegionPlannerAgent tier-3。prompt 仍在但主線不呼叫（見 §7.2 reducer 非主線註） |

---

## 10. 設定與環境變數

**設定檔**：`backend/.env`（由 `backend/config.py` 明確載入）

| 環境變數 | 預設值 | 說明 |
|----------|--------|------|
| `ANTHROPIC_API_KEY` | — | Claude API 金鑰 |
| `OPENAI_API_KEY` | — | OpenAI API 金鑰 |
| `GOOGLE_API_KEY` | — | Gemini API 金鑰 |
| `MONICA_API_KEY` | — | Monica 代理 API 金鑰（可選） |
| `MONICA_BASE_URL` | — | Monica 代理基底 URL（可選） |
| `DEEPSEEK_API_KEY` | — | DeepSeek API 金鑰（可選） |
| `DEFAULT_PROVIDER` | `claude` | 預設 LLM Provider（claude｜openai｜gemini｜monica｜deepseek） |
| `DB_PATH` | `../data/learning.db` | 相對路徑以 backend/ 為基準解析 |
| `JWT_SECRET` | `dev-secret-change-in-production` | JWT 簽名密鑰 |
| `JWT_EXPIRE_DAYS` | `7` | JWT 有效期天數 |
| `CORS_ORIGINS` | （見 config.py） | 允許 CORS 來源；逗號分隔，未設則用 Vite dev server 預設清單 |
| `CORS_ORIGIN_REGEX` | `https://.*\.trycloudflare\.com` | 動態 Quick Tunnel 子網域用 regex 白名單；設為空字串可關閉 |
| `SPLITTER_FAIL_MODE` | `hard` | **已非阻塞**：global verify 不對齊時只切 warning 通道（`soft`=附 `quality_warnings`；`hard`=寫 WARNING log），兩者都繼續 fold + 補節 + 收尾，不中止 session。舊 fail-hard 拒絕已移除 |
| `RUN_LLM_TESTS` | — | 設 `1` 才執行 `pytest -m llm_live` 真 LLM gate |
| `STAGE_TITLE_MERGE_THRESHOLD` | `0.85` | stage 標題去重合併閾值（0~1）；高=保守、低=積極合併；越界值 fallback 至預設 |
| `STAGE_CONCEPT_OVERLAP_THRESHOLD` | `0.6` | P0b-1：跨 source stage 用 key_concepts jaccard 合併閾值（0~1） |
| `SPLITTER_VERIFIER_MIN_MISSES` | `2` | P2a：Splitter verifier 觸發 reroll 的最少 missing 數；missing ≤ 1 直接 soft-pass 省一輪 LLM |
| `CONCEPT_CANONICALIZE` | `0` | `1` 時 stage 合併後再跑 ConceptCanonicalizeAgent 統一關鍵詞命名（耗一次 LLM） |
| `CURRICULUM_USE_ARQ` | `0` | `1` 時 start_session 改 enqueue 至 Redis，由 Arq worker 執行 pipeline |
| `REDIS_URL` | `redis://localhost:6379/0` | Arq 佇列；本機 docker compose 用 `redis://localhost:6380/0` |
| `ARQ_MAX_JOBS` | `1` | Arq worker 同時執行 job 數 |
| `ARQ_JOB_TIMEOUT_S` | `7200` | 單一 curriculum job 最長秒數 |
| `LLM_CACHE_ENABLED` | `0` | `1` 啟用 curriculum LLM result cache |
| `CURRICULUM_PROMPT_VERSION` | `1` | cache key 一部分；prompt 改版時遞增以失效舊 cache |
| `LLM_CACHE_EVICT_DAYS` | `90` | startup 清掉超過 N 天的 cache entry；`0` 關閉 |
| `LLM_MAX_CONCURRENT` | `0` | 全域 LLM 同時呼叫上限；`0` 關閉限制。與 worker 併用時請與 curriculum-worker 設相同值（建議 Monica 共用時為 `3`） |
| `LLM_SLOT_WAIT_TIMEOUT_S` | `120` | 等待 Redis slot 的最長秒數（逾時拋錯） |
| `LLM_SLOT_LEASE_S` | `600` | 每個佔用 slot 的租約秒數（過期自動釋放，防 worker 當掉卡死） |
| `SQLITE_JOURNAL_MODE` | `WAL` | SQLite journal mode（Docker worker volume 內建議 WAL） |

> 歷史 schema 參考：`docs/superpowers/specs/2026-05-22-curriculum-v2-schema.md`（內含 large-file path / Reducer schema，2026-05-27 起停用）
> 統一架構 spec：`docs/superpowers/specs/2026-05-27-curriculum-unify-v2-design.md`

### 切分流程的 Cap / 防護

統一架構下只剩小檔限制；reducer / region 相關 cap 已隨 D2 刪除。

| cap | 預設 | 定義位置 | 作用位置 | 觸發行為 |
|-----|------|---------|---------|---------|
| `STAGE_MAX_KEY_CONCEPTS` | 8 | `small_curriculum.py` | orphan attach 寫入 stage 時的 kc 上限 | 超過時新建 `kind=follow_up_orphan` overflow stage |
| `ORPHAN_STAGE_MAX_CHUNKS` | 14 | `small_curriculum.py` | orphan attach 寫入 stage 時的 chunk 上限 | 超過時拆「補充段落（N）」overflow stage |
| `MAX_MERGED_OUTCOME_CHUNKS` | 20 | `reducer_constants.py`（殘留 const） | `stage_composer.outcomes_to_stages` 仍引用 | 合併後 chunk 數超過 → 保留 split |

### 監控信號（`backend/utils/curriculum_health.py`）

`assess_reducer_health` 在小檔路徑仍會跑（vestigial），但 candidate/outcome/unsure 都是 0，通常 healthy。可選附加至 `pending_map_json.quality_warnings`：

| signal | 意義 |
|--------|------|
| `splitter_verifier_failed` | `SPLITTER_FAIL_MODE=soft` 時 verifier 失敗仍放行，附 missing chunk_ids |
| `small_file_path` | 走小檔路徑（永遠 True） |
| `reducer_skipped` | reducer 已棄用 |
| `post_process_added_stages` | `_build_follow_up_stages` 補了 N 個 stage |

`assess_curriculum_cost` 仍輸出 `curriculum_llm_calls` / `curriculum_tier` / `curriculum_llm_over_budget` / `curriculum_llm_budget`（用於 cost 監控）。

> 進階門檻分數與重試上限寫死在 orchestrator 中（`pass_threshold=0.75`、`max_attempts=3`），不由環境變數覆寫。

### 外部工具

| 工具 | 位置 | 說明 |
|------|------|------|
| `extract_text()` | `backend/utils/text_extractor.py` | 本地文件解析（PDF/DOCX/PPTX/MD/TXT），輸出純文字 |
| `build_source_chunks()` | `backend/utils/chunker.py` | 機械切分 + 結構優先（Wittgenstein 命題編號、Markdown 標題），輸出 chunk 列表 |
| `search_web()` | `backend/tools/web_search.py` | DuckDuckGo Instant Answer API（免 key），供 ask_tutor 使用 |
| `fetch_url_content()` | `backend/utils/url_fetcher.py` | URL/YouTube 擷取：readability + fallback 清洗 + strict_main；YouTube 走字幕優先，失敗時 ASR 轉寫 |

### URL/YouTube 擷取依賴（2026-05-08）

- `readability-lxml>=0.8.1`
- `youtube-transcript-api>=0.6.2`
- `yt-dlp>=2025.1.26`（YouTube 音訊下載）
- `faster-whisper>=1.0.3`（ASR 轉寫）

---

## 11. WS 基礎設施（Phase 1–3 增補）

以下三套機制橫跨多個 message handler，集中說明。

### 11.1 `DebouncedExplanationWriter`（Phase 1）

**位置**：`backend/orchestrator/debounced_writer.py`

**目的**：講解串流期間每 chunk 寫 DB 太頻繁（IO 浪費 + 鎖頻寬）→ 用時間 + size 雙閘門 throttle。

```python
class DebouncedExplanationWriter:
    def __init__(self, store_fn, session_id, stage_id,
                 min_interval_s=0.5, min_delta_chars=200):
        ...

    async def update(self, full_text: str) -> None:
        """update 不會自行 sanitize；time_due 或 size_due 任一達到即 _do_write。"""

    async def flush(self) -> None:
        """確保最新狀態落地（finally 內呼叫，保證 cancel/disconnect 仍寫一次）。"""
```

關鍵：`run_stage` 的 `try / finally writer.flush()` 配合 Phase 2 cancel 機制 — 即使中途 `task.cancel()` 也會把已生成部分留在 `stage_progress.full_explanation`。

### 11.2 `_GenerationHandle` + `inflight_lock`（Phase 2 + Phase 3 Task B）

**位置**：`backend/ws/generation_handle.py`、`backend/db/inflight_lock.py`

**目的**：5 個 dispatcher handler（start_session / confirm_map / submit_answer / resume_session / ask_tutor）共用的「**dedup + 可取消**」基礎建設。

**Generation key 命名**：

| message_type | key 格式 |
|---|---|
| start_session / confirm_map / resume_session | `{session_id}` |
| submit_answer | `{session_id}:answer:{question_id}` |
| ask_tutor | `{session_id}:tutor` |

**API**：

| 函式 | 同步 / async | 用途 |
|---|---|---|
| `register(key, task)` | sync | 純 in-process registry（既有 unit tests 在用） |
| `register_async(key, task, *, session_id, kind)` | async | **主用**：同步 `INSERT inflight_locks`；衝突回 None |
| `get_active(key)` | sync | 取 in-process handle |
| `finish(key)` | sync | 純 clear registry |
| `finish_async(key)` | async | clear registry + `DELETE inflight_locks` |
| `cancel(key)` | async | task.cancel() + clear registry |
| `cancel_async(key)` | async | task.cancel() + clear registry + release DB lock |

**保險機制**：`register_async` 在 `task.add_done_callback` 內 `asyncio.create_task(inflight_lock.release(key))` — 即使呼叫端忘了 `finish_async`，task 結束時 DB lock 仍會釋放。

**Phase 2 Bug D 修補**：5 處 handler 內**不 await task**（fire-and-forget），dispatcher loop 必須能繼續處理 `cancel_generation` 訊息；`cancel_generation` handler 自己 emit `generation_cancelled`（帶 `kind` 字段，前端用 `'ask_tutor'` vs `'other'` 判斷是否清 streaming bubble）。

### 11.3 `_wait_or_lookup_cache` helper（Phase 1 + Bug F 修補）

**位置**：`backend/main.py:158`

**行為**（Phase 1 + Bug F 後）：

```
async def _wait_or_lookup_cache(key, timeout_s, cache_lookup, emit_cached) -> bool:
    1. 先 cache_lookup（無條件，不論有無 inflight）→ 命中即 emit + return True
    2. cache miss → 查 inflight registry
       - 無 inflight → return False（呼叫端走新任務路徑）
       - 有 inflight → wait 最多 timeout_s
    3. 等完後再 cache_lookup → 命中 emit + return True；否則 return False
```

Bug F 修補關鍵：原本只在「有 inflight」時才查 cache，導致 tutor 重複問同問題會跑 LLM 兩次（第一次完成後 registry 已清，第二次來時 prev=None 直接跑新 LLM）。改為「**無條件先 cache lookup**」對 4 個 caller (start_session / confirm_map / submit_answer / ask_tutor) 都是正確行為。

各 caller 的 cache_lookup 內容：

| caller | cache_lookup 邏輯 |
|---|---|
| start_session | session row 已存在且 `content_hash` 命中（或 row 已 `status != pending_confirmation`） |
| confirm_map | session row `status != pending_confirmation`（已 confirm 過） |
| submit_answer | `qa_records` 已有同 question_id 記錄 |
| ask_tutor | `tutor_records` 已有同 stage_id + 同 question 文字記錄 |

### 11.4 Orchestrator stateless（Phase 3 Task C1）

**`_build_orchestrator_for_session(session_id, p)` (main.py)**：每個 WS message 從 DB session row 重建 `LearningOrchestrator` instance。

優先序：`payload.provider/model` > `sessions.provider_name/model_name` > `DEFAULT_PROVIDER`。

**已移除**：`_orchestrators: dict[str, LearningOrchestrator]` in-memory cache 與其 `pop / set` 邏輯。WS disconnect cleanup 仍呼叫 `delete_working_memory(session_id)`（WorkingMemory 沒走 stateless 化，仍用 in-process `_store`）。

### 11.5 cancel_generation dispatch

```python
elif msg_type == "cancel_generation":
    target_key = p.get("key")
    cancelled_keys: list[str] = []
    if not target_key:
        # 不指定 key fallback：嘗試取消該 session 兩個常見 key
        for k in (session_id, f"{session_id}:tutor"):
            if await _gen_cancel_async(k):
                cancelled_keys.append(k)
    else:
        if await _gen_cancel_async(target_key):
            cancelled_keys.append(target_key)
    for k in cancelled_keys:
        kind = "ask_tutor" if k.endswith(":tutor") else "other"
        await emit({"type": "generation_cancelled", "payload": {"key": k, "kind": kind}})
```

前端 `case 'generation_cancelled'` 看 `kind === 'ask_tutor'` → `commitStreamingTutorAsCancelled()`（streaming bubble 凍結為 history note）；其他 kind 走 `endExplanationLoading()` 通用路徑。

---

## 12. Curriculum 背景化（Checkpoint / Arq / LLM Cache）

155-chunk 等長教材的 V2 pipeline 可能跑數小時。Phase 1–3 背景化讓生成與 API 進程解耦、支援斷點續跑與 LLM 去重。

### 12.1 Region Checkpoint（Phase 1）

**模組**：`backend/memory/curriculum_checkpoint.py`、`backend/orchestrator/curriculum_pipeline_v2.py`

- 每完成一個 macro region → `upsert_checkpoint(completed_region_ids, ...)`
- `resume_generating_session(orch, session_id)` 讀 checkpoint，skip 已完成 regions
- API startup（`CURRICULUM_USE_ARQ=0`）：`list_resumable_sessions()` → `resume_generating_session_background`
- CLI：`python backend/tools/resume_curriculum.py`、`abandon_session.py --delete-checkpoint`

### 12.2 Arq Background Worker（Phase 2）

**模組**：`backend/jobs/`（`session_prepare.py`、`enqueue.py`、`curriculum_job.py`、`arq_settings.py`）

```
start_session (WS, CURRICULUM_USE_ARQ=1)
    → prepare_curriculum_session
    → enqueue_curriculum_job(redis, session_id)   # inflight key: {session_id}:start
    → emit session_generating

run_curriculum_job (Arq worker)
    → maybe_wrap_curriculum_llm(create_provider(...))
    → resume_generating_session(orch, session_id, emit=_null_emit)
    → finally: release(inflight_key)
```

**注意**：
- Worker 內 `_null_emit`：Arq 模式**不**推 WS `region_done` / `reduce_done` 等進度事件（前端需 polling session status 或之後補機制）
- 固定 job id `curriculum:{session_id}`；重試前需清 stale `arq:result:*` key（`backend/tools/live_arq_verify.py` 內建清理）
- **Docker**（`docker-compose.yml`）：
  - `wl-redis`：host `:6380` → container `:6379`
  - `wl-curriculum-worker`：`./data:/seed`（種子 DB）、`wl-worker-data:/data`（執行時 DB）、`./backend:/app/backend`
  - entrypoint（`backend/docker/worker-entrypoint.sh`）：啟動時 `cp /seed/learning.db → /data/learning.db`；退出時 `wal_checkpoint` + sync 回 `/seed`
  - 可選 `WORKER_SYNC_INTERVAL=30` 週期 sync（預設關閉，避免與本機 uvicorn 同時寫 DB）
- **Windows + 本機 uvicorn 共用 SQLite**：**勿**與 in-process 生成同時寫同一 DB；Arq 模式下 uvicorn 只 enqueue

### 12.3 LLM Result Cache（Phase 3）

**模組**：`backend/memory/llm_cache.py`、`backend/llm/caching_provider.py`、`backend/llm/cache_context.py`

- 6 個 curriculum agents 在 `llm_cache_context(...)` 內呼叫 LLM
- Cache key = hash(scope, content_hash, agent_name, region_id, prompt_version, model, messages)
- `LLM_CACHE_ENABLED=1` 啟用；reroll / verifier retry 同 prompt 可 hit cache
- Stats：`python backend/tools/llm_cache_stats.py`

### 12.4 部署模式對照

| 模式 | uvicorn | worker | 重啟 API | WS region 進度 |
|------|---------|--------|----------|----------------|
| In-process（預設） | pipeline 在進程內 | 不需要 | 中斷；startup 自動續跑 | 有 |
| Arq 本機 | 只 enqueue | `python -m arq ...` | 不影響 worker | 無（目前） |
| Arq Docker | 只 enqueue | `docker compose up -d` | 不影響 worker | 無（目前） |

**建議 `.env`（Arq 模式）**：
```env
CURRICULUM_PIPELINE_V2=1
CURRICULUM_USE_ARQ=1
REDIS_URL=redis://localhost:6380/0
LLM_CACHE_ENABLED=1
```

---

## 13. LLM 流量治理

### 問題

多個 WebSocket 會話、背景 curriculum worker、以及教學／評量等路徑會**同時呼叫 Monica（或其他上游 LLM 代理）**，易造成 burst 與限速、排隊混亂或上游不穩。

### 解法概要

- **`LLM_MAX_CONCURRENT`**：設定全域「同時進行中的 LLM 請求」上限；設為 `0` 表示關閉限制（與舊行為相容）。
- **Redis ZSET 分散式訊號量**：所有 process 透過同一 **`REDIS_URL`** 協調；Redis key 為 **`wittgenstein:llm:global_slots`**（實作見 `backend/llm/concurrency.py`）。長時間 LLM 呼叫期間會以背景任務約每 **60 秒**續租（更新該 holder 之 ZSET 分數至 `現在 + LLM_SLOT_LEASE_S`）；Redis 可否用的同步連線探測則**成功結果快取 300 秒、失敗結果快取 30 秒**，過期後自動重試，避免離線 Redis 一直被快取為不可用。

### 部署要點

| 要點 | 說明 |
|------|------|
| **API 與 worker 一致** | 跑 uvicorn 的本機／容器與 **`curriculum-worker`** 必須使用**相同的** `LLM_MAX_CONCURRENT` 與 **`REDIS_URL`**，否則兩邊各自以為還有 quota，實際會打爆上游。 |
| **快取不佔 slot** | **`CachingLLMProvider`** 在 cache **命中**時不會進入內層 provider，因此**不會佔用** LLM slot（僅實際對上游發請求時才.acquire）。 |
| **與 `ARQ_MAX_JOBS=1` 的關係** | **`ARQ_MAX_JOBS=1`** 表示 worker **一次只跑一個 curriculum job**（job 層序）；**LLM 流量治理**限制的是**跨 process 的並發 LLM HTTP 呼叫**。兩者互補：後者避免多 session + worker 同時打滿 Monica。 |

### 可觀測性

檢視目前 slot 使用量（需可連到與後端相同的 Redis）：

```powershell
# 於 wittgenstein-learning 根目錄；使用 backend\.venv
.\backend\.venv\Scripts\python.exe backend/tools/llm_concurrency_stats.py
```

等同於以專案虛擬環境執行：`python backend/tools/llm_concurrency_stats.py`（將 `python` 換成 `backend\.venv\Scripts\python.exe` 即可）。

### 環境變數（摘錄）

詳見 [§10](#10-設定與環境變數) 主表；與本節直接相關者：

| 環境變數 | 預設（見 `config.py`） | 用途 |
|----------|------------------------|------|
| `LLM_MAX_CONCURRENT` | `0`（關閉） | 全域並發上限；Docker worker 建議與本機一致設為 `3` 等正值 |
| `LLM_SLOT_WAIT_TIMEOUT_S` | `120` | 等待取得 slot 的上限秒數 |
| `LLM_SLOT_LEASE_S` | `600` | 持有 slot 的租約；到期 Redis 端可視為釋放，降低 crash 後卡死 |

> **`docker-compose.yml`** 內 `curriculum-worker` 可依長任務將 `LLM_SLOT_WAIT_TIMEOUT_S` 設為 **`300`**（較長等待），與預設 API 本機 `120` 可分開調校；**`LLM_MAX_CONCURRENT` 與 `REDIS_URL` 仍須與 API 對齊。**
