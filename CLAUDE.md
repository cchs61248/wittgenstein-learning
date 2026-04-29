# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend

```bash
# 建立虛擬環境（需在 backend/ 目錄）
C:\Windows\py.exe -3 -m venv .venv

# 安裝依賴（在 backend/ 目錄）
.venv\Scripts\pip install -r requirements.txt

# 啟動伺服器（在 backend/ 目錄，啟動虛擬環境後直接用 uvicorn）
uvicorn run:app --reload --port 8000
```

**Python 虛擬環境（僅此倉庫）**：本專案的 `.venv` 建立在 **`backend/.venv`**。執行任何與此 repo 相關的 Python（含從上層 `learn` 呼叫的工具腳本）請使用 `backend/.venv/Scripts/python.exe`，勿假設倉庫根目錄或上層 `learn` 根目錄有 `.venv`。其他路徑下的專案各自有其慣例，不適用此路徑。

**注意**：入口點是 `run.py`，而不是 `main.py`。`run.py` 將上層目錄加入 `sys.path`，讓 `backend.*` 的相對匯入正常運作。不能用 `uvicorn main:app`，否則出現 `ImportError: attempted relative import with no known parent package`。

### Frontend

```bash
# 從 frontend/ 目錄執行
npm install
npm run dev       # 開發伺服器，預設 http://localhost:5173
npm run build     # TypeScript 編譯 + Vite 打包至 dist/
npm run lint      # ESLint 檢查
```

### 環境變數設定

複製 `backend/.env.example` 為 `backend/.env` 並填入金鑰：

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
DEFAULT_PROVIDER=claude        # claude | openai | gemini
PASS_THRESHOLD=0.75
MAX_STAGE_ATTEMPTS=3
DB_PATH=../data/learning.db    # 相對路徑以 backend/ 為基準
JWT_SECRET=change-me
```

---

## 架構概述

### 整體資料流

```
前端 (React/Zustand)
  └─ REST → /auth/register, /auth/login, /upload
  └─ WebSocket → /ws/{session_id}?token=JWT
       └─ main.py（WebSocketManager）
            └─ LearningOrchestrator（協調所有元件）
                 ├─ text_extractor.py      → 本地文件解析（PDF/DOCX/MD/TXT）
                 ├─ chunker.py             → 機械切分，建立文件層級 source_chunks
                 ├─ ContentSplitterAgent   → LLM 語義切分（只回傳 chunk_id，不生成原文）
                 ├─ ContextBuilder         → 每次教學前組裝完整學生狀態包
                 │    (context_builder.py)   mastery_map + misconceptions + selection_reason
                 ├─ TeacherAgent           → 串流講解 + extract_teaching_intent
                 ├─ QuestionGeneratorAgent → 與 teaching_intent 對齊的布魯姆題目
                 ├─ EvaluatorAgent         → 評分 + misconception_patterns（結構化診斷）
                 ├─ ProgressManagerAgent   → advance/retry/remediate/reteach
                 │                           Phase 4：high_severity / repeated_patterns
                 └─ DriftVerifierAgent     → Citation accuracy 驗證（逐條 claim 核對原文）
```

### REST API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/auth/register` | 註冊，回傳 JWT |
| POST | `/auth/login` | 登入，回傳 JWT |
| GET | `/auth/me?token=...` | 取得當前使用者 |
| POST | `/upload` | 上傳檔案（需 `Authorization: Bearer <token>`），回傳 `file_id` |
| GET | `/health` | 健康檢查 |

### 檔案上傳流程（Phase 1 後）

1. 前端 `POST /upload` → 伺服器用 `backend/files/upload_store.py` 儲存在記憶體（單 process 有效）並回傳 `file_id`
2. 前端發送 `start_session` WebSocket 訊息，帶入 `uploaded_file_id`
3. 後端執行本地解析（主路徑）：
   - `text_extractor.extract_text(filename, raw_bytes)` → 純文字（PDF/DOCX/PPTX/MD/TXT）
   - `chunker.build_source_chunks(text, session_id)` → `[{chunk_id: "chunk_NNNN", text, ...}]`
   - `session_memory.insert_source_chunks(session_id, chunks)` → 持久化為 source truth
4. ContentSplitterAgent 接收 chunks，LLM 只回傳 `source_chunk_ids`，不生成原文引用
5. Orchestrator 後端回填 `stage["source_chunks"]`（從 DB 取真實原文）

> Files API（`backend/llm/file_adapter.py`）降級為 fallback，不再是主路徑。

### LLM 抽象層（`backend/llm/`）

`BaseLLMProvider` 定義統一介面：`chat()` 和 `stream_chat()`。三個 Provider 的差異：
- **ClaudeProvider**：`system_prompt` 作為獨立參數傳給 Anthropic API
- **OpenAIProvider**：`system_prompt` 插入 messages[0] 作為 system role
- **GeminiProvider**：`system_prompt` 設定為 `config.system_instruction`；使用 `google.genai`（新 SDK，非棄用的 `google.generativeai`）

工廠函式：`create_provider("claude" | "openai" | "gemini")`

### Agent 上下文隔離（`backend/agents/`）

每個 Agent 的 `_messages` 在 `run()` 結束後呼叫 `_reset()` 清除，防止跨呼叫上下文累積。Token 預算（`max_context_tokens`）：ContentSplitter 4000、Teacher 2000、QuestionGenerator 1500、Evaluator 1200、ProgressManager 800、DriftVerifier 1200。

**EvaluatorAgent 掌握度標籤**：`_add_mastery_label()` 在所有三條評分路徑結束後統一注入 `✅/⚠️/❌` 分類標籤，不依賴 LLM 生成。Phase 3 起所有路徑也保證回傳 `misconception_patterns: list`（空列表或結構化診斷）。

**TeacherAgent System Prompt 結構（Phase 2+4）**：`{user_profile_summary}` → `【學生目前狀態】`（mastery_summary、misconceptions_text、recent_qa_text）→ `【本節任務】`（must_reinforce_text、forbidden_future_text、selection_reason_text）→【講解原則】→【重要限制】→【輸出格式】。**格式參數共 7 個**，改動 prompt 時注意順序——講解原則在格式規範之前，確保類比要求優先。

**ContextBuilder（`backend/orchestrator/context_builder.py`）**：Phase 2 新增模組，`build_adaptive_context()` 在每次 TeacherAgent 呼叫前執行，組裝 `allowed_evidence`（DB 原文）、`learner_state`（mastery_map + misconceptions + recent_qa）、`next_lesson_requirements`（must_reinforce + forbidden_future + selection_reason）。

**DriftVerifier Citation Accuracy（Phase 4）**：`_extract_cited_chunks()` 預先提取 `[chunk_id]` 引用並配對原文，傳遞 `cited_chunks_lookup` 給 LLM，實現逐條 claim 驗證而非形式引用檢查。後端強制：`found=False` 的 chunk_id 標記為 `supported=False`。

**ProgressManager 決策優先序（Phase 4）**：`high_severity` misconception（任何嘗試次數）或 `_detect_repeated_patterns()`（同一 pattern ≥ 2 次）立即觸發 `reteach`，優先於 `attempts < max_attempts → retry`。

### 記憶三層（`backend/memory/`）

- **WorkingMemory**（in-process dict）：以 `session_id` 為 key；`wm.stages` 存整份 stage 列表，`wm.pending_questions` 存當前 stage 問題，`wm.current_teaching_intent`（Phase 3）存 TeacherAgent 提取的教學意圖，`reset_for_new_stage()` 切換 stage 時清空含 `current_teaching_intent`
- **SessionMemory**（SQLite）：`sessions`、`stage_progress`、`qa_records`、`source_chunks`（Phase 1）表；新增 `get_source_chunks()`、`get_recent_qa_summary()`、`get_last_decision_record()` 函式（Phase 2）
- **LongtermMemory**（SQLite）：`concept_mastery`（EMA 掌握度）、`user_learning_profile`；Phase 2 新增 `get_misconceptions()`；Phase 3 `update_concept_mastery()` 新增 `misconception_pattern`（結構化，存入 confusion_patterns）和 `analogy_used`/`lesson_was_effective`（存入 successful_analogies）

### 重要實作細節

**`wm.stages` 與 `wm.pending_questions` 分離**：`wm.stages`（`list[dict]` with `stage_id`）在 `start_session` 後設定，整個 session 生命週期內不變；`wm.pending_questions` 僅存放當前 stage 的問題列表，`handle_answer` 直接從 `wm.stages` 取 stage 資料。

**chunk_id 命名空間**：Phase 1 起，chunk_id 格式為 `chunk_NNNN`（文件層級），不含 stage 前綴。舊格式 `s2_c1` 僅在 fallback 路徑（`_normalize_stage_source_chunks`）中仍可能出現，不應新增。

**Source Truth 流向**：`upload → text_extractor → chunker → source_chunks 表 → ContentSplitter（只讀 chunk_id）→ stage 回填`。LLM 永遠不生成原文引用，後端從 DB 回填確保 source truth 一致性。

**Prompt 模板中的 JSON 跳脫**：`backend/utils/prompt_templates.py` 的所有 prompt 使用 Python f-string `.format()` 系統。模板內的 JSON 範例中所有字面大括號必須是 `{{` `}}`，否則 Python 拋出 `KeyError`。例外：`extract_teaching_intent` 的 system 字串不呼叫 `.format()`，可使用真實 `{}`。

**allowed_evidence vs source_chunks 的 key 差異**：DB 查詢的 `source_chunks` 使用 `text` key，stage 內嵌的舊格式使用 `quote` key。`TeacherAgent._format_allowed_evidence()` 和 `QuestionGeneratorAgent._format_evidence()` 均以 `c.get("text") or c.get("quote")` 同時相容。

**confusion_patterns 格式演進**：Phase 3 起寫入結構化 dict（含 concept/pattern/severity/repair_strategy）；`get_misconceptions()` 同時相容舊版字串列表（轉換為 `{concept, pattern, severity: "medium"}`）。新代碼一律傳 `misconception_pattern=dict`，不傳 `confused_concepts`。

**`DB_PATH` 解析**：`config.py` 明確載入 `backend/.env`（用 `Path(__file__).parent`），並將相對路徑以 `backend/` 為基準解析。這解決了從不同 CWD 啟動時路徑找不到的問題。

**WebSocket URL 硬編碼**：`frontend/src/api/websocket.ts` 中 `WS_BASE` 固定為 `ws://localhost:8000`，部署時需手動修改或改成環境變數。

### WebSocket 訊息協定

**客戶端 → 伺服器**：`start_session`、`submit_answer`、`request_hint`

**伺服器 → 客戶端**：`session_started`、`explanation_chunk`（串流，`is_final: bool`）、`explanation_complete`、`question`、`feedback`、`stage_decision`（含 `decision: advance|retry|remediate|reteach`）、`course_completed`、`error`

完整型別定義在 `frontend/src/types/messages.ts`。

### 前端狀態管理（`frontend/src/store/sessionStore.ts`）

Zustand store 管理認證（token 持久化到 localStorage 的 key `wl_token`/`wl_user_id`/`wl_email`）、stages 清單（含 status: `pending|current|completed`）、串流講解文字、當前問題、最新 feedback 與 decision。

### 靜態檔服務

後端若偵測到 `frontend/dist/` 目錄存在，會自動將前端 build 掛載到 `/`。開發時前後端分開跑（前端 :5173，後端 :8000），CORS 已設定允許 localhost:5173。
