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
DATABASE_URL=postgresql://wl:wl@localhost:5432/wl   # PostgreSQL 連線（asyncpg）
DB_POOL_MIN_SIZE=1
DB_POOL_MAX_SIZE=10
JWT_SECRET=change-me
```

> **資料庫已從 SQLite 遷移到 PostgreSQL（asyncpg）。** 本機開發先 `docker compose up -d postgres`（或自備 PG 並設 `DATABASE_URL`）。schema 收斂於 `backend/db/schema.sql`（單一 baseline，無歷史 migration）。後端測試用 **testcontainers** 自動起拋棄式 postgres，故**跑測試需 Docker daemon**；測試依賴在 `backend/requirements-dev.txt`（`pip install -r requirements-dev.txt`）。

---

## Claude Code 工具使用注意事項（Windows）

### 工具選擇對照表

| 操作 | 工具 | 原因 |
|------|------|------|
| pytest / pip / python | **PowerShell** | Bash 工具不接受 Windows 路徑 |
| git | Bash 或 PowerShell | — |
| npm 前端指令 | Bash 或 PowerShell | — |
| 讀寫搜尋檔案 | 專用工具（Read/Write/Grep/Glob） | 比 shell 更穩、無路徑陷阱 |

### Bash 工具陷阱：只接受 Unix 風格路徑

Bash 工具底層為 Git Bash，**只接受 Unix 格式路徑**（`/c/Users/...`）。傳 Windows 路徑（`c:\...` 或 `..\..\.venv\...`）會得到 **exit code 49** 或 `command not found`。

```bash
# ❌ Windows 路徑
ls C:\Users\<username>\Documents\aaron\wittgenstein-learning\

# ✅ Unix 絕對路徑
ls /c/Users/<username>/Documents/aaron/wittgenstein-learning/
```

### PowerShell 5.1 陷阱（Windows 內建 powershell.exe）

#### 1. `2>&1` 對 native exe 會把 stderr 包成 ErrorRecord

跑 `node.exe` / `vite` / `pytest.exe` / `python.exe` 等 native 工具時加 `2>&1`，PowerShell 5.1 會把每一行 stderr 包成 `NativeCommandError`，輸出像：

```
node.exe : [vite] ...
At line:1 char:1
+ & "C:\Program Files\nodejs/node.exe" ...
    + CategoryInfo : NotSpecified: (... :String) [], RemoteException
```

**這不代表命令真的失敗**。判斷成敗：

- 看 `$LASTEXITCODE`（**不是 `$?`**，後者對 native exe 不可靠，有 stderr 時常被誤判為失敗）
- 觀察輸出最後一行（如 `✓ built in 474ms` 即 build 成功）

```powershell
# ❌ stderr 被 wrap 成 NativeCommandError，誤判失敗
npm run build 2>&1 | Select-Object -Last 25

# ✅ 不加 2>&1，stderr 自動顯示但不被 wrap
npm run build | Select-Object -Last 25

# ✅ 確認真實退出碼
npm run build; "EXIT=$LASTEXITCODE"
```

#### 2. 沒有 `&&` / `||` / `?:` / `??` / `?.`

PowerShell 5.1 **沒有**這些運算子，使用會拋 parser error。

```powershell
# ❌ 不可用
npm run lint && npm run build

# ✅ cmdlet 條件串接
npm run lint; if ($?) { npm run build }

# ✅ native exe 條件串接（$? 不可靠，用 $LASTEXITCODE）
& ".\.venv\Scripts\pytest.exe" tests/; if ($LASTEXITCODE -eq 0) { Write-Output "ok" }

# ✅ 無條件串接
Set-Location frontend; npm install
```

#### 3. `Get-ChildItem -Recurse` 不會遞迴非目錄項目

若路徑下的「子項」其實是檔案 stub（或符號連結），`-Recurse -Depth N | Select-Object FullName` 可能完全無輸出。先用不加 `-Recurse` 的 `Get-ChildItem` 看 Mode 欄位（`d----` 目錄、`-a---` 檔案）。

```powershell
# ❌ 無輸出時無法分辨「沒匹配」vs「不是目錄」
Get-ChildItem C:\foo\bar -Recurse -Depth 2 | Select-Object FullName

# ✅ 先確認結構
Get-ChildItem C:\foo\bar
```

#### 4. `Test-Path` / `Get-ChildItem` 空結果不丟錯

回傳 `$false` 或無輸出皆是「無事發生」，**不會 exit 1**。腳本要自行判斷：

```powershell
if (Test-Path $path) { ... } else { "not found" }
```

#### 5. 執行不存在的腳本可能得到 misleading exit code

`python missing_script.py` 等情境在 Windows + PowerShell 可能以 **exit 49**（或其他非預期碼）結束。看到陌生 exit code 時：

1. 先 `Test-Path` 確認檔案存在
2. 確認直譯器存在（`Get-Command python` / `py`）
3. 別把 exit code 直接套到「Python 環境壞了」的判斷

### 執行 pytest / Python（必須用 PowerShell）

```powershell
Set-Location "c:\Users\<username>\Documents\aaron\wittgenstein-learning\backend"
& ".\.venv\Scripts\pytest.exe" tests/ -v

# 只取最後幾行輸出（不加 2>&1，避免 stderr 被 wrap）
& ".\.venv\Scripts\pytest.exe" tests/ -v | Select-Object -Last 20

# 首次安裝依賴（若 pytest 缺失）
& ".\.venv\Scripts\pip.exe" install -r requirements.txt -q
```

> 測試目錄：`backend/tests/`，以 repo 根為 `sys.path` 基準，匯入用 `from backend.xxx import ...`。

### 常見錯誤對照表

| 錯誤訊息 / 現象 | 真正原因 | 對策 |
|----------------|---------|------|
| Bash 工具 exit code 49 / `command not found` | 傳了 Windows 路徑 | 改用 PowerShell，或用 Unix 風格路徑（`/c/Users/...`）|
| PS 輸出 `node.exe : ... RemoteException` | `2>&1` 把 native stderr wrap 成 ErrorRecord | 移除 `2>&1`，用 `$LASTEXITCODE` 與輸出最後一行判斷成敗 |
| `npm run X` 看似失敗但實際成功 | 同上 | `npm run X; "EXIT=$LASTEXITCODE"` 確認 |
| `Get-ChildItem ... \| Select FullName` 無輸出 | 目標是檔案 stub 不是目錄，或無匹配 | 先用無 `-Recurse` 的 `Get-ChildItem` 看 Mode |
| `python script.py` 拋 exit 49 | 腳本不存在 / 路徑錯 | 先 `Test-Path` 確認 |
| `No module named pytest` | venv 未安裝 pytest | `& .\.venv\Scripts\pip.exe install -r requirements.txt` |
| `No module named 'tiktoken'` | requirements.txt 未完整安裝 | 同上 |
| `ImportError: attempted relative import...` | 用 `uvicorn main:app` 啟動 | 改用 `uvicorn run:app`（`run.py` 已處理 sys.path）|
| PS 拋 parser error 在 `&&` / `?:` | PowerShell 5.1 不支援這些運算子 | 改用 `; if ($?) { ... }` 或 `if/else` |

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

**`DATABASE_URL` / 連線池**：`config.py` 載入 `backend/.env` 後讀 `DATABASE_URL`（預設 `postgresql://wl:wl@localhost:5432/wl`）。`db/database.py` 用 **asyncpg pool**（`get_db()` 回傳 pool；單句 autocommit、多句用 `async with conn.transaction()` 且**交易內只能用同一個 `conn`**）。**注意**：`config.DATABASE_URL` 在 import 時凍結；`main.py` lifespan 與 arq worker startup 都改讀 `os.getenv("DATABASE_URL", ...)`（runtime），因為 testcontainers 測試在 import 後才設 env。`init_db(dsn, *, reset=False)`：`reset=True` 僅測試環境（`WL_TEST_ENV=1`）允許，會 drop/recreate public schema。離線 `tools/*` 部分仍標 `TODO(pg)` 未移植（不在 app/worker/測試路徑）。

**ProgressManager `attempts` 來源（高頻 bug 源）**：`attempts` 必須來自 task_payload 的 `current_attempt`（即 `wm.current_attempt`），**不可用 `len(stage_evaluations)`**。`wm.stage_evaluations` 每輪 retry/remediate 後重置為 `[]`，`len` 永遠等於當輪題目數，與嘗試輪次無關。Orchestrator 在 `_make_progress_decision` 的 task_payload 中必須傳 `"current_attempt": wm.current_attempt`。

**WebSocket URL 硬編碼**：`frontend/src/api/websocket.ts` 中 `WS_BASE` 固定為 `ws://localhost:8000`，部署時需手動改或改成環境變數。
