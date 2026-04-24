# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend

```bash
# 建立虛擬環境（Windows，需在 backend/ 目錄）
C:\Windows\py.exe -3 -m venv .venv

# 安裝依賴
.venv\Scripts\pip install -r requirements.txt

# 啟動伺服器（從 backend/ 目錄執行）
.venv\Scripts\uvicorn.exe run:app --reload --port 8000
```

**注意**：入口點是 `run.py`，而不是 `main.py`。`run.py` 會將上層目錄加入 `sys.path`，讓 `backend.*` 的相對匯入能正常運作。不能用 `uvicorn main:app`，否則會出現 `ImportError: attempted relative import with no known parent package`。

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
  └─ WebSocket → /ws/{session_id}?token=JWT
       └─ main.py（WebSocketManager）
            └─ LearningOrchestrator（協調所有 Agent）
                 ├─ ContentSplitterAgent → 切割材料為 stages
                 ├─ TeacherAgent         → 串流生成講解
                 ├─ QuestionGeneratorAgent → 布魯姆分類題目
                 ├─ EvaluatorAgent        → 評分 0.0~1.0
                 └─ ProgressManagerAgent  → advance/retry/remediate/reteach
```

### LLM 抽象層（`backend/llm/`）

`BaseLLMProvider` 定義統一介面：`chat()` 和 `stream_chat()`。三個 Provider 的差異：
- **ClaudeProvider**：`system_prompt` 作為獨立參數傳給 Anthropic API
- **OpenAIProvider**：`system_prompt` 插入 messages[0] 作為 system role
- **GeminiProvider**：`system_prompt` 設定為 `config.system_instruction`；使用 `google.genai`（新 SDK，非棄用的 `google.generativeai`）

工廠函式：`create_provider("claude" | "openai" | "gemini")`

### Agent 上下文隔離（`backend/agents/`）

每個 Agent 的 `_messages` 在 `run()` 結束後呼叫 `_reset()` 清除，防止跨呼叫上下文累積。Token 預算（`max_context_tokens`）：ContentSplitter 4000、Teacher 2000、QuestionGenerator 1500、Evaluator 1200、ProgressManager 800。

### 記憶三層（`backend/memory/`）

- **WorkingMemory**（in-process dict）：單次問答輪次狀態；`get_compressed_history(max_turns=3)` 防膨脹；以 `session_id` 為 key 存在 module-level dict 中
- **SessionMemory**（SQLite）：`sessions`、`stage_progress`、`qa_records` 表
- **LongtermMemory**（SQLite）：`concept_mastery`（EMA 計算掌握度）、`user_learning_profile`

### 重要實作細節

**`wm.pending_questions` 雙重用途**：`LearningOrchestrator` 中，這個欄位在 `start_session` 期間暫存 stages 列表（`list[dict]` with `stage_id`），在 `run_stage` 後改為存放問題列表。`handle_answer` 透過 `isinstance(q, dict) and "stage_id" in s` 來區分兩種元素。這是已知的 code smell。

**Prompt 模板中的 JSON 跳脫**：`backend/utils/prompt_templates.py` 的所有 prompt 使用 Python f-string `.format()` 系統。模板內的 JSON 範例中所有字面大括號必須是 `{{` `}}`，否則 Python 會把 `{` `}` 視為格式佔位符並拋出 `KeyError`。

**`DB_PATH` 解析**：`config.py` 明確載入 `backend/.env`（用 `Path(__file__).parent`），並將相對路徑以 `backend/` 為基準解析。這解決了從不同 CWD 啟動時路徑找不到的問題。

### WebSocket 訊息協定

**客戶端 → 伺服器**：`start_session`、`submit_answer`、`request_hint`

**伺服器 → 客戶端**：`session_started`、`explanation_chunk`（串流，`is_final: bool`）、`explanation_complete`、`question`、`feedback`、`stage_decision`（含 `decision: advance|retry|remediate|reteach`）、`course_completed`、`error`

### 前端狀態管理（`frontend/src/store/sessionStore.ts`）

Zustand store 管理認證（token 持久化到 localStorage 的 key `wl_token`/`wl_user_id`/`wl_email`）、stages 清單（含 status: `pending|current|completed`）、串流講解文字、當前問題、最新 feedback 與 decision。

### 靜態檔服務

後端若偵測到 `frontend/dist/` 目錄存在，會自動將前端 build 掛載到 `/`。開發時前後端分開跑（前端 :5173，後端 :8000），CORS 已設定允許 localhost:5173。
