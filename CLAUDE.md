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
            └─ LearningOrchestrator（協調所有 Agent）
                 ├─ ContentSplitterAgent → 切割材料為 stages
                 ├─ TeacherAgent         → 串流生成講解
                 ├─ QuestionGeneratorAgent → 布魯姆分類題目
                 ├─ EvaluatorAgent        → 評分 0.0~1.0
                 └─ ProgressManagerAgent  → advance/retry/remediate/reteach
```

### REST API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/auth/register` | 註冊，回傳 JWT |
| POST | `/auth/login` | 登入，回傳 JWT |
| GET | `/auth/me?token=...` | 取得當前使用者 |
| POST | `/upload` | 上傳檔案（需 `Authorization: Bearer <token>`），回傳 `file_id` |
| GET | `/health` | 健康檢查 |

### 檔案上傳流程

1. 前端 `POST /upload` → 伺服器用 `backend/files/upload_store.py` 以 pickle 格式儲存在記憶體（單 process 有效）並回傳 `file_id`
2. 前端發送 `start_session` WebSocket 訊息，帶入 `uploaded_file_id`
3. 後端呼叫 `create_provider_file_ref()`（`backend/llm/file_adapter.py`），依 provider 將檔案上傳至對應 LLM 平台：
   - **claude**：Anthropic Files API（`anthropic-beta: files-api-2025-04-14`）
   - **openai**：OpenAI Files API（purpose: assistants）
   - **gemini**：google-genai `client.aio.files.upload()`

### LLM 抽象層（`backend/llm/`）

`BaseLLMProvider` 定義統一介面：`chat()` 和 `stream_chat()`。三個 Provider 的差異：
- **ClaudeProvider**：`system_prompt` 作為獨立參數傳給 Anthropic API
- **OpenAIProvider**：`system_prompt` 插入 messages[0] 作為 system role
- **GeminiProvider**：`system_prompt` 設定為 `config.system_instruction`；使用 `google.genai`（新 SDK，非棄用的 `google.generativeai`）

工廠函式：`create_provider("claude" | "openai" | "gemini")`

### Agent 上下文隔離（`backend/agents/`）

每個 Agent 的 `_messages` 在 `run()` 結束後呼叫 `_reset()` 清除，防止跨呼叫上下文累積。Token 預算（`max_context_tokens`）：ContentSplitter 4000、Teacher 2000、QuestionGenerator 1500、Evaluator 1200、ProgressManager 800。

### 記憶三層（`backend/memory/`）

- **WorkingMemory**（in-process dict）：單次問答輪次狀態；以 `session_id` 為 key 存在 module-level dict；`wm.stages` 存整份 stages 列表，`wm.pending_questions` 存當前 stage 的問題列表，`get_compressed_history(max_turns=3)` 防膨脹
- **SessionMemory**（SQLite）：`sessions`、`stage_progress`、`qa_records` 表
- **LongtermMemory**（SQLite）：`concept_mastery`（EMA 計算掌握度）、`user_learning_profile`

### 重要實作細節

**`wm.stages` 與 `wm.pending_questions` 分離**：`wm.stages`（`list[dict]` with `stage_id`）在 `start_session` 後設定，整個 session 生命週期內不變；`wm.pending_questions` 僅存放當前 stage 的問題列表，`handle_answer` 直接從 `wm.stages` 取 stage 資料。

**Prompt 模板中的 JSON 跳脫**：`backend/utils/prompt_templates.py` 的所有 prompt 使用 Python f-string `.format()` 系統。模板內的 JSON 範例中所有字面大括號必須是 `{{` `}}`，否則 Python 會把 `{` `}` 視為格式佔位符並拋出 `KeyError`。

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
