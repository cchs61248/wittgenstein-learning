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

---

## Claude Code 工具使用注意事項

### Bash 工具路徑問題（Windows）

本專案在 Windows 環境下，Bash 工具使用 Git Bash（Unix 風格路徑），而非 Windows 原生路徑。

**錯誤用法（會導致 `No such file or directory`）：**
```bash
# ❌ Windows 路徑格式
ls C:\Users\<username>\Documents\aaron\learn\
cd wittgenstein-learning/frontend && npm run build   # 相對路徑從根目錄不存在
```

**正確用法：**
```bash
# ✅ 使用 Unix 絕對路徑
ls /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/frontend && npm run build
```

### Python 語法檢查的路徑問題

若 Bash 工具的 CWD 已在 `backend/` 目錄，語法檢查時路徑不要再加 `backend/` 前綴：

```bash
# CWD 已是 backend/ 時：
# ❌ 路徑錯誤
python.exe -c "with open('backend/main.py') as f: ..."

# ✅ 直接用檔名
python.exe -c "with open('main.py') as f: ..."
```

### npm 建置與前端指令

前端指令必須在絕對路徑下執行，或先用 `cd` 切換到正確目錄：

```bash
# ✅ 正確方式
cd /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/frontend && npm run build

# 或使用 PowerShell 工具（更穩定）
```

### 執行後端測試（pytest）

**pytest 不在 `requirements.txt`**，`.venv` 預設沒有安裝。首次跑測試前必須先裝，且 `.venv` 不一定完整安裝了所有 `requirements.txt` 依賴，連帶缺少 `tiktoken` 等套件也會讓測試 collect 失敗。

**正確流程（在 `backend/` 目錄下）：**
```bash
# 1. 確保 requirements.txt 的依賴全數安裝
.venv/Scripts/pip install -r requirements.txt -q

# 2. 安裝測試框架（不在 requirements.txt 中）
.venv/Scripts/pip install pytest -q

# 3. 跑測試
.venv/Scripts/python.exe -m pytest tests/ -v
```

**常見錯誤訊息與原因：**
| 錯誤 | 原因 |
|------|------|
| `No module named pytest` | pytest 未安裝，執行步驟 2 |
| `No module named 'tiktoken'` | requirements.txt 依賴未完整安裝，執行步驟 1 |

> 測試目錄：`backend/tests/`；測試以 `sys.path` 的 repo 根目錄為基準，使用 `from backend.xxx import ...` 格式匯入。

### Python 模組匯入測試

測試後端模組匯入時，需將 `backend/` 的上層目錄加入 `sys.path`：

```bash
# 在 backend/ 目錄下，測試 backend.* 的相對匯入
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, '..')
from backend.routers.upload import router
print('OK')
"
```

直接匯入（不含 `backend.` 前綴）則不需要修改 sys.path：
```bash
.venv/Scripts/python.exe -c "
from utils.url_fetcher import fetch_url_content
print('OK')
"
```

---

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
MONICA_API_KEY=...             # Monica OpenAI 相容代理（可選）
MONICA_BASE_URL=...            # Monica API 基底 URL（可選）
DEFAULT_PROVIDER=claude        # claude | openai | gemini | monica
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
| GET | `/sessions/active?token=...` | 查詢最新 active/pending_confirmation session |
| GET | `/sessions/list?token=...` | 列出用戶所有 session（書櫃） |
| GET | `/sessions/{session_id}?token=...` | 取得單一 session 詳細資訊 |
| PATCH | `/sessions/{session_id}/title?token=...` | 更新 session 自訂標題 |
| DELETE | `/sessions/{session_id}?token=...` | 刪除 session（含 qa/stage/source_chunks/decision，保留 mastery） |
| GET | `/learner/stats?token=...` | 取得學習統計（concepts + misconceptions + weak_count） |
| GET | `/health` | 健康檢查 |

### 檔案上傳流程（Phase 1 後）

1. 前端 `POST /upload` → 伺服器用 `backend/files/upload_store.py` 儲存至磁碟（`data/uploads/*.bin` + `*.meta.json`）並回傳 `file_id`
2. 前端發送 `start_session` WebSocket 訊息，帶入 `uploaded_file_id`
3. 後端執行本地解析（主路徑）：
   - `text_extractor.extract_text(filename, raw_bytes)` → 純文字（PDF/DOCX/PPTX/MD/TXT）
   - `chunker.build_source_chunks(text, session_id)` → `[{chunk_id: "chunk_NNNN", text, ...}]`
   - `session_memory.insert_source_chunks(session_id, chunks)` → 持久化為 source truth
4. ContentSplitterAgent 接收 chunks，LLM 只回傳 `source_chunk_ids`，不生成原文引用
5. Orchestrator 後端回填 `stage["source_chunks"]`（從 DB 取真實原文）

> Files API（`backend/llm/file_adapter.py`）降級為 fallback，不再是主路徑。

### LLM 抽象層（`backend/llm/`）

`BaseLLMProvider` 定義統一介面：`chat()` 和 `stream_chat()`。四個 Provider 的差異：
- **ClaudeProvider**：`system_prompt` 作為獨立參數傳給 Anthropic API；預設模型 `claude-sonnet-4-6`
- **OpenAIProvider**：`system_prompt` 插入 messages[0] 作為 system role；預設模型 `gpt-5.4-mini`
- **GeminiProvider**：`system_prompt` 設定為 `config.system_instruction`；使用 `google.genai`（新 SDK，非棄用的 `google.generativeai`）；預設模型 `gemini-3-flash-preview`
- **MonicaProvider**：OpenAI 相容格式代理，透過 `MONICA_BASE_URL` / `MONICA_API_KEY` 設定；預設模型 `claude-4.6-sonnet`

工廠函式：`create_provider("claude" | "openai" | "gemini" | "monica")`

### Agent 上下文隔離（`backend/agents/`）

每個 Agent 的 `_messages` 在 `run()` 結束後呼叫 `_reset()` 清除，防止跨呼叫上下文累積。Token 預算（`max_context_tokens`）：ContentSplitter 4000、Teacher 2000、QuestionGenerator 1500、Evaluator 1200、ProgressManager 800、DriftVerifier 1200。

**EvaluatorAgent 掌握度標籤**：`_add_mastery_label()` 在所有三條評分路徑結束後統一注入 `✅/⚠️/❌` 分類標籤，不依賴 LLM 生成。Phase 3 起所有路徑也保證回傳 `misconception_patterns: list`（空列表或結構化診斷）。

**TeacherAgent System Prompt 結構（Phase 2+4）**：`{user_profile_summary}` → `【學生目前狀態】`（mastery_summary、misconceptions_text、recent_qa_text）→ `【本節任務】`（must_reinforce_text、forbidden_future_text、selection_reason_text）→【講解原則】→【重要限制】→【輸出格式】。**格式參數共 7 個**，改動 prompt 時注意順序——講解原則在格式規範之前，確保類比要求優先。

**ContextBuilder（`backend/orchestrator/context_builder.py`）**：Phase 2 新增模組，`build_adaptive_context()` 在每次 TeacherAgent 呼叫前執行，組裝 `allowed_evidence`（DB 原文）、`learner_state`（mastery_map + misconceptions + recent_qa）、`next_lesson_requirements`（must_reinforce + forbidden_future + selection_reason）。

**DriftVerifier Citation Accuracy（Phase 4）**：`_extract_cited_chunks()` 使用 `\bchunk_\w+\b` 正則同時支援 Markdown `[chunk_0001]` 與 JSON `["chunk_0001"]` 兩種格式提取引用，配對原文後傳遞 `cited_chunks_lookup` 給 LLM，實現逐條 claim 驗證而非形式引用檢查。後端強制：`found=False` 的 chunk_id 標記為 `supported=False`。注意：舊版 `\[([^\]]+)\]` 會在 JSON 格式中抓到含引號的 `"chunk_0000"` 導致全部 `found=False`，已於 2026-04-30 修正。

**ProgressManager 決策優先序（Phase 4）**：`high_severity` misconception（任何嘗試次數）或 `_detect_repeated_patterns()`（同一 pattern ≥ 2 次）立即觸發 `reteach`，優先於 `attempts < max_attempts → retry`。**動態節點特例**：`remediate_count >= 2`（同一 stage 已補強 ≥ 2 次）時強制 `advance`，避免無限補強循環。

**ContentSplitter 小 stage 合併（2026-04-30）**：`_normalize_splitter_output()` 完成後呼叫 `_merge_thin_stages()`，前向掃描並將 `source_chunk_ids < 2` 的 stage 合併至後繼 stage（最後一個合往前），合併後重新編號 `stage_id`。這消除了 LLM 把單一 chunk 切成獨立 stage 時產生的 `possibly_too_small` 警告。

**QuestionGenerator 類比隔離（2026-04-30）**：`_format_teaching_intent()` 中，`teaching_intent.analogies_used` 是 TeacherAgent 自創的說明工具，不存在於 source_chunks。現在明確標記「禁止把類比細節當成題目素材」，並移除舊版「至少一題能檢驗學生是否理解文章使用的類比框架」的要求（該指令會讓 LLM 以教師類比為題，DriftVerifier 必然 fail）。改為要求問題測試補強概念的核心原理（依據 source_chunks）。

### 記憶三層（`backend/memory/`）

- **WorkingMemory**（in-process dict）：以 `session_id` 為 key；`wm.stages` 存整份 stage 列表，`wm.pending_questions` 存當前 stage 問題，`wm.current_teaching_intent`（Phase 3）存 TeacherAgent 提取的教學意圖，`wm.question_mode` 存題目模式，`wm.remediate_count` 追蹤當前 stage 已補強次數（`reset_for_new_stage()` 切換 stage 時全數清空）
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

**ProgressManager attempts 來源**：`attempts` 必須來自 task_payload 的 `current_attempt`（第幾輪嘗試，`wm.current_attempt`），**不可用 `len(stage_evaluations)`**（當輪已答題目數）。`wm.stage_evaluations` 在每次 retry/remediate 後重置為 `[]`，所以 `len` 永遠等於當輪題目數（簡答 2、選擇 4），與嘗試輪次無關。Orchestrator 在 `_make_progress_decision` 的 task_payload 中必須傳入 `"current_attempt": wm.current_attempt`。

**retry / remediate 不清除原文**：決策為 `retry` 或 `remediate` 時，**不送 `explanation_reset`**。兩者都在 `wm.current_explanation` 尾端附加新內容（retry 附加「第 N 次嘗試」標題，remediate 串流整篇補強教學文章），然後呼叫 `session_memory.store_stage_explanation(combined)` + `store_stage_questions(questions)` 持久化。`reteach` 則在換框架前先存一次舊版（同樣呼叫 `store_stage_explanation` + `store_stage_questions`），讓 resume 不再觸發重新生成。

**TeacherAgent 呼叫時機**：除了 `run_stage` 串流初次講解和 `reteach` 換框架，**remediate 決策也會呼叫 TeacherAgent.stream_explanation**（附「補強模式」指示，針對 focus 概念從不同角度補充說明），並在完成後執行 `extract_teaching_intent` 更新 `wm.current_teaching_intent`。

**ask_tutor 前端持久化**：`tutor_reply` 訊息到達前端後，`addTutorMessage()` 將回答追加至 Zustand `tutorHistory` 陣列，並同步寫入 `localStorage` key `wl_tutor_history`。`clearAuth()` 與 `clearSession()` 均會清除此 key 並重置 `tutorHistory: []`。`AskTutorPanel` 以可收縮筆記（`HistoryNote` 元件）呈現歷史，最新一筆預設展開，頁面重整後自動恢復。

**WebSocket URL 硬編碼**：`frontend/src/api/websocket.ts` 中 `WS_BASE` 固定為 `ws://localhost:8000`，部署時需手動修改或改成環境變數。

**上傳檔案磁碟持久化**：`backend/files/upload_store.py` 將上傳檔寫入 `data/uploads/{file_id}.bin`（原始 bytes）與 `{file_id}.meta.json`（filename/mime_type/size/extra_meta），跨重啟仍可讀取，`load_upload(file_id)` 從磁碟讀回。`save_upload()` 接受可選的 `extra_meta: dict`，URL 來源會額外存 `source_url` 與 `source_type`。

**多來源 start_session（2026-05-06）**：`start_session` WebSocket payload 新增 `sources` 陣列格式，向下相容舊版 `uploaded_file_id`/`content`。每個 source 獨立執行 `build_source_chunks()`，chunk_id 全域重新編號，並附上 `source_label`（來源名稱）與 `source_index`（來源順序）。這兩個欄位僅存於 in-memory dict，不寫入 DB（`insert_source_chunks` 只取固定欄位）。`_build_source_chunks_from_payload()` 函式封裝此邏輯，位於 `main.py`。

**ContentSplitter 跨來源聚合（2026-05-06）**：`_format_chunks_with_sources()` 函式（位於 `backend/agents/content_splitter.py`）在多來源時以 `=== 來源 N：標題 ===` 分組顯示 chunks。Prompt 新增「跨來源聚合原則」：不同來源涵蓋相同主題的 chunks 應歸入同一 stage，避免重複教學。無來源標記時維持原本平面格式，向下相容。

**URL 擷取**：`POST /upload/url` 接收 URL，後端用 `readability-lxml` 抽取網頁正文、`youtube-transcript-api` 抓 YouTube 字幕，轉為純文字後以 `save_upload()` 儲存，回傳 `file_id`。擷取邏輯在 `backend/utils/url_fetcher.py`。依賴套件：`readability-lxml>=0.8.1`、`youtube-transcript-api>=0.6.2`（已加入 requirements.txt）。

**sessions.title 欄位（Migration 010）**：`sessions` 表新增 `title TEXT DEFAULT NULL`，可透過 `PATCH /sessions/{session_id}/title` 更新。`GET /sessions/list` 供書櫃功能列出所有 session。

**Migration 實際編號**：database.py 中 002–008 用 `try/except` 冪等執行；008 = `sessions.question_mode`（非 `source_chunks`）；009 = `source_chunks` 表；010 = `sessions.title`；006 = `decision_records` 表（程式碼中標記 006，但在 010 之後執行）。

### WebSocket 訊息協定

**客戶端 → 伺服器**：`start_session`、`confirm_map`、`submit_answer`、`resume_session`、`ask_tutor`、`request_hint`

**伺服器 → 客戶端**：`kicked`（同帳號重連踢舊線）、`session_generating`（開始生成中）、`knowledge_map`（等待確認）、`session_started`、`session_snapshot`（全量恢復）、`explanation_chunk`（串流，`is_final: bool`）、`explanation_complete`、`explanation_reset`（僅 reteach 換框架時）、`question`、`feedback`、`stage_decision`（含 `decision: advance|retry|remediate|reteach`）、`qa_history`、`resume_state`、`tutor_reply`、`hint`、`course_completed`、`error`

完整型別定義在 `frontend/src/types/messages.ts`。

### 前端狀態管理（`frontend/src/store/sessionStore.ts`）

Zustand store 管理認證（token 持久化到 localStorage 的 key `wl_token`/`wl_user_id`/`wl_email`）、stages 清單（含 status: `pending|current|completed`）、串流講解文字、當前問題、最新 feedback 與 decision。

### 靜態檔服務

後端若偵測到 `frontend/dist/` 目錄存在，會自動將前端 build 掛載到 `/`。開發時前後端分開跑（前端 :5173，後端 :8000），CORS 已設定允許 localhost:5173。
