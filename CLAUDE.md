# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend（在 `backend/` 目錄執行）

```powershell
# 建立虛擬環境
C:\Windows\py.exe -3 -m venv .venv

# 安裝依賴
.\.venv\Scripts\pip install -r requirements.txt

# 啟動伺服器（啟動虛擬環境後）
uvicorn run:app --reload --port 8000
```

- **虛擬環境位置**：`backend/.venv`（**僅此一處**，不在 repo 根目錄、也不在上層 `learn/`）。
- **入口點是 `run.py`，不是 `main.py`**：`run.py` 將上層目錄加入 `sys.path`，讓 `backend.*` 相對匯入正常運作。用 `uvicorn main:app` 會拋 `ImportError: attempted relative import with no known parent package`。

### Frontend（在 `frontend/` 目錄執行）

```bash
npm install
npm run dev       # 開發伺服器 http://localhost:5173
npm run build     # TypeScript 編譯 + Vite 打包至 dist/
npm run lint      # ESLint 檢查
```

### 環境變數

複製 `backend/.env.example` 為 `backend/.env`：

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
MONICA_API_KEY=...             # Monica OpenAI 相容代理（可選）
MONICA_BASE_URL=...            # Monica API 基底 URL（可選）
DEFAULT_PROVIDER=claude        # claude | openai | gemini | monica
DB_PATH=../data/learning.db    # 相對路徑以 backend/ 為基準
JWT_SECRET=change-me
```

---

## Claude Code 工具使用注意事項（Windows）

### 工具選擇

| 操作 | 工具 | 原因 |
|------|------|------|
| pytest / pip / python | **PowerShell** | Bash 工具不接受 Windows 路徑 |
| git | Bash 或 PowerShell 均可 | — |
| npm 前端指令 | Bash 或 PowerShell 均可 | — |
| 讀寫搜尋檔案 | 專用工具（Read/Write/Grep/Glob） | — |

### Bash 工具的 Windows 路徑陷阱

Bash 工具底層為 Git Bash，**只接受 Unix 格式路徑**（`/c/Users/...`）。傳 Windows 路徑（`c:\...` 或 `..\..\.venv\...`）會得到 **exit code 49** 或 `command not found`。

```bash
# ❌ Windows 路徑
ls C:\Users\<username>\Documents\aaron\learn\

# ✅ Unix 絕對路徑
ls /c/Users/<username>/Documents/aaron/learn/wittgenstein-learning/
```

### 執行 pytest / Python（必須用 PowerShell）

```powershell
cd "c:\Users\<username>\Documents\aaron\learn\wittgenstein-learning\backend"
& ".\.venv\Scripts\pytest.exe" tests/ -v

# 只取最後幾行輸出
& ".\.venv\Scripts\pytest.exe" tests/ -v 2>&1 | Select-Object -Last 20

# 首次安裝依賴（若 pytest 缺失）
& ".\.venv\Scripts\pip.exe" install -r requirements.txt -q
```

**常見錯誤對照：**

| 錯誤 | 原因 |
|------|------|
| `No module named pytest` | pytest 未安裝（執行上方 pip 指令）|
| `No module named 'tiktoken'` | requirements.txt 依賴未完整安裝 |
| Bash exit code 49 / `command not found` | 誤用 Bash 工具，改用 PowerShell |

> 測試目錄：`backend/tests/`，以 repo 根為 `sys.path` 基準，匯入用 `from backend.xxx import ...`。

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

> 詳細 Agent 說明、DB Schema、Migration 列表、WebSocket 協定、完整 REST API 規格、學習流程等見 [BACKEND_FLOW.md](./BACKEND_FLOW.md)。

### 靜態檔服務

後端若偵測到 `frontend/dist/` 存在，會自動將前端 build 掛載到 `/`。開發時前後端分開跑（前端 :5173，後端 :8000），CORS 已設定允許 localhost:5173。

---

## 重要編碼注意事項

**`wm.stages` 與 `wm.pending_questions` 分離**：`wm.stages` 存整份 stage 列表，`handle_answer` 直接從 `wm.stages` 取資料；`wm.pending_questions` 僅存當前 stage 的問題，兩者不可混用。

**chunk_id 命名空間**：Phase 1 起格式為 `chunk_NNNN`（文件層級），不含 stage 前綴。舊格式 `s2_c1` 僅在 `_normalize_stage_source_chunks` fallback 路徑出現，不應新增。

**Source Truth 流向**：`upload → text_extractor → chunker → source_chunks 表 → ContentSplitter（只讀 chunk_id）→ stage 回填`。LLM 永遠不生成原文引用，後端從 DB 回填確保一致性。

**Prompt 模板 JSON 跳脫**：`backend/utils/prompt_templates.py` 使用 f-string `.format()`，模板內 JSON 範例的字面大括號必須是 `{{` `}}`，否則拋 `KeyError`。例外：`extract_teaching_intent` 的 system 字串不呼叫 `.format()`，可用真實 `{}`。

**`allowed_evidence` vs `source_chunks` key 差異**：DB 查詢的 source_chunks 用 `text` key；stage 內嵌舊格式用 `quote` key。`TeacherAgent._format_allowed_evidence()` 與 `QuestionGeneratorAgent._format_evidence()` 以 `c.get("text") or c.get("quote")` 相容兩者。

**`confusion_patterns` 格式演進**：Phase 3 起寫入結構化 dict（concept / pattern / severity / repair_strategy），`get_misconceptions()` 同時相容舊字串列表。新代碼一律傳 `misconception_pattern=dict`，不傳 `confused_concepts`。

**`DB_PATH` 解析**：`config.py` 用 `Path(__file__).parent` 明確載入 `backend/.env`，相對路徑以 `backend/` 為基準。從不同 CWD 啟動若出現路徑找不到，先確認 `.env` 載入來源。

**ProgressManager `attempts` 來源（高頻 bug 源）**：`attempts` 必須來自 task_payload 的 `current_attempt`（即 `wm.current_attempt`），**不可用 `len(stage_evaluations)`**。`wm.stage_evaluations` 每輪 retry/remediate 後重置為 `[]`，`len` 永遠等於當輪題目數，與嘗試輪次無關。Orchestrator 在 `_make_progress_decision` 的 task_payload 中必須傳 `"current_attempt": wm.current_attempt`。

**WebSocket URL 硬編碼**：`frontend/src/api/websocket.ts` 中 `WS_BASE` 固定為 `ws://localhost:8000`，部署時需手動改或改成環境變數。
