# 維特根斯坦學習系統

一套以維特根斯坦哲學為核心的互動式學習平台。透過「講解 → 提問 → 反饋 → 調整」的閘門式循環，確保學習者真正內化當前概念後才進入下一階段。

## 核心理念

系統遵循三個哲學原則：

- **語言遊戲單元**：每個學習階段都是一個可獨立理解的完整概念單位
- **家族相似性**：Teacher Agent 從至少兩個不同角度提供比喻，建立概念網絡
- **理解是光譜**：評估不採用通過/失敗二元判定，而是 0.0–1.0 的連續分數，搭配四種決策路徑（前進、重試、補充、重新講解）

---

## 功能總覽

### 教材輸入：三種來源

| 來源 | 入口 | 支援格式 / 限制 |
|------|------|-----------------|
| 檔案上傳 | `POST /upload` | `.txt .md .pdf .docx .pptx .html .htm`；單檔 10 MB |
| URL 擷取 | `POST /upload/url` | 公開網頁（readability-lxml + 全頁清洗 fallback + strict_main 主文截斷）、YouTube 字幕；單次最多 500,000 字 |
| YouTube ASR | `POST /upload/youtube/asr/stream` | YouTube 影片無字幕時的 fallback：`yt-dlp` 下載音訊 + `faster-whisper`（small/cpu/int8）轉寫，串流回報進度 |
| 純文字 | 前端直接帶入 `start_session.sources` | 不經過上傳 API，貼上即用 |

多來源 session：可同時上傳多份檔案 + URL + 文字，ContentSplitter 跨來源語義聚合，同主題 chunks 收進同一 stage。

### 學習迴圈（Phase 1–4 完整實作）

- **Source Truth 後端掌控**：上傳後 `text_extractor → chunker` 建立 `source_chunks` 表，LLM 只做語義切分回傳 `chunk_id`，原文一律由後端回填，杜絕幻覺引用
- **Teacher 串流講解**：Markdown 即時渲染，依學生掌握度 / 混淆模式 / 選課理由（Phase 4）自適應，每個概念至少 2 個生活化類比，類比明確標記為「說明工具」不可作為題目素材
- **Citation Accuracy 驗證**：DriftVerifier 逐條 claim 比對原文，同時支援 Markdown `[chunk_id]` 與 JSON 陣列兩種格式；不通過自動重生（附 revision_hint）
- **教學意圖對齊**：TeacherAgent 講解串流結尾共生 `<<INTENT_JSON>>{key_concepts, expected_misunderstandings, evidence_chunk_ids}<<END_INTENT>>` 區塊（`stream_explanation_with_intent` 內聯抽取、不外送給前端、不進 DB），QuestionGenerator 至少一題對齊；LLM 偶發未輸出區塊時 fallback 走獨立 `extract_teaching_intent` LLM call（罕見）
- **結構化錯誤診斷**：Evaluator 輸出 `misconception_patterns`（concept / pattern / severity / repair_strategy），存入長期記憶供跨會話追蹤
- **智能進度決策**（純規則，純程式邏輯，不呼叫 LLM）：高嚴重度根本誤解或同一錯誤重複 ≥ 2 次立即觸發換框架重教；詳細 10 條優先序見 [BACKEND_FLOW §7.6](./BACKEND_FLOW.md#76-進度決策_make_progress_decision)
- **retry / remediate 不清空原文**：retry 在原文尾端附加「第 N 次嘗試」標題；remediate 完整串流補強教學文章並持久化，頁面重整後不重新生成
- **跨會話長期記憶**：EMA（α=0.3）追蹤每個概念掌握度、累積結構化混淆模式、成功類比，選課時把弱點與選擇理由傳給 TeacherAgent

### 學生介面

- **書櫃**：列出所有 session，含自訂標題、刪除（保留長期掌握度）、生成中 stub（status='generating'，LLM 處理期間就能看到）
- **學生追問（Ask Tutor）**：LLM 三態邊界判定 `{current_chapter, other_chapter, out_of_scope}`，對應使用不同 source（當前章 chunks / 其他章 chunks / 全文 + DuckDuckGo 網搜）；後端持久化於 `tutor_records` 表，前端可單筆刪除
- **回顧與恢復**：任何階段（含已完成章節）皆可回顧，後端從 `stage_progress.full_explanation` / `qa_records` 重建；資料庫為唯一 source of truth
- **學習統計頁**：`GET /learner/stats` 回傳概念掌握度、結構化混淆模式、弱點概念數；前端 `LearningStatsPage` 用 recharts 呈現
- **深色模式**：Header icon 切換淺色 / 暖紙黑，localStorage 持久化，`main.tsx` 在 render 前同步套用避免閃屏

### 跨裝置與多連線

- **單裝置強制登出**（Migration 011）：JWT 帶 `sv`（session_version）；新登入 `session_version += 1`，舊 token `sv` 不符即無效。多裝置切換時舊連線收 `kicked`（WebSocket code 4002）後關閉；同瀏覽器多分頁共用 `client_id`、允許並存（不會被 4002 踢）
- **跨裝置 UI 狀態同步**（Migration 012）：書櫃排序、版面 prefs 寫入 `user_learning_profile.ui_state_json`，`GET/PUT /user/ui-state` 同步
- **跨 worker dedup**（Migration 016）：`inflight_locks` 表 + `_GenerationHandle` 同步寫 DB；同 session 重複觸發或多 worker 同時收到請求時，後者走「先查 DB cache hit → 否則放棄 race」路徑，避免重複 LLM call
- **取消機制**：前端「停止生成」送 `cancel_generation` → 後端 task.cancel() + release DB lock → 回 `generation_cancelled` + 已生成部分留在 DB（拜 `DebouncedExplanationWriter` 持久化）
- **WS 自動重連**：指數退避 1/2/4/8/16/32s（上限 6 次共 63 秒），重連後自動 replay `resume_session`；onGiveUp 後紅色 banner 提示手動重整；`verifyAuth` 區分網路斷與 token 失效（網路問題不誤踢登出）

### LLM Provider（5 個）

| Provider | Endpoint | 預設 model |
|----------|----------|-----------|
| Claude | Anthropic SDK | `claude-sonnet-4-6` |
| OpenAI | OpenAI SDK | `gpt-5.4-mini`（GPT-5 系列自動改用 `max_completion_tokens`） |
| Gemini | `google.genai` SDK | `gemini-3-flash-preview` |
| Monica | OpenAI 相容代理（自訂 `MONICA_BASE_URL`） | `claude-4.6-sonnet` |
| DeepSeek | `https://api.deepseek.com`（OpenAI 相容） | `deepseek-v4-flash`，推理模型自動 fallback 取 `reasoning_content` |

---

## 快速開始

### 環境需求

- Python 3.11+
- Node.js 18+

### 後端（Windows PowerShell 範例）

```powershell
cd backend

# 建立虛擬環境
C:\Windows\py.exe -3 -m venv .venv

# 安裝依賴
.\.venv\Scripts\pip install -r requirements.txt

# 設定環境變數
copy .env.example .env
# 編輯 .env，至少填入一組 LLM API key

# 啟動伺服器（入口是 run.py 不是 main.py）
.\.venv\Scripts\uvicorn.exe run:app --reload --port 8000
```

> 開發時請務必用 `uvicorn run:app` 而非 `uvicorn main:app`；`run.py` 會把上層目錄加入 `sys.path` 才能正確匯入 `backend.*`。

### 前端

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
npm run build     # tsc + Vite 打包至 dist/
npm run lint
```

開啟瀏覽器至 `http://localhost:5173`，註冊帳號後即可開始使用。

> 後端會自動偵測 `frontend/dist/`，存在即掛載到 `/`（單一 port 部署）；開發期間前後端分跑，CORS 已預設允許 `http://localhost:5173`。

---

## 環境變數（`backend/.env`）

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `ANTHROPIC_API_KEY` | Claude API 金鑰 | — |
| `OPENAI_API_KEY` | OpenAI API 金鑰 | — |
| `GOOGLE_API_KEY` | Gemini API 金鑰 | — |
| `MONICA_API_KEY` | Monica 代理 API 金鑰（可選） | — |
| `MONICA_BASE_URL` | Monica 代理基底 URL（可選） | — |
| `DEEPSEEK_API_KEY` | DeepSeek API 金鑰（可選） | — |
| `DEFAULT_PROVIDER` | 預設 LLM（`claude` / `openai` / `gemini` / `monica` / `deepseek`） | `claude` |
| `DB_PATH` | SQLite 路徑（相對路徑以 `backend/` 為基準） | `../data/learning.db` |
| `JWT_SECRET` | JWT 簽名密鑰 | `dev-secret-change-in-production` |
| `JWT_EXPIRE_DAYS` | JWT 有效期（天） | `7` |
| `CORS_ORIGINS` | 額外允許的 CORS 來源（逗號分隔；不設則用 Vite dev server 預設） | — |
| `CORS_ORIGIN_REGEX` | CORS regex 白名單（如 Cloudflare Quick Tunnel） | `https://.*\.trycloudflare\.com` |

> `PASS_THRESHOLD=0.75` 與 `MAX_STAGE_ATTEMPTS=3` 現已寫死在 orchestrator 中，不再由環境變數覆寫。

---

## 架構

### 後端（FastAPI + Python）

```
backend/
├── main.py                     # FastAPI 入口、WebSocket 路由、WebSocketManager
├── config.py                   # 環境變數載入（明確指向 backend/.env）
├── run.py                      # uvicorn 入口（修正 sys.path 讓 backend.* 匯入）
├── llm/                        # LLM 抽象層
│   ├── base_provider.py        # BaseLLMProvider 介面（chat / stream_chat）
│   ├── claude_provider.py
│   ├── openai_provider.py
│   ├── gemini_provider.py
│   ├── monica_provider.py      # OpenAI 相容代理
│   ├── deepseek_provider.py    # 繼承 OpenAIProvider，reasoning_content fallback
│   └── provider_factory.py     # create_provider(name, model?)
├── agents/                     # 六個功能 Agent + BaseAgent
│   ├── base_agent.py           # _messages 自動 _reset()，token 預算管理
│   ├── content_splitter.py     # 語義切分（只回傳 chunk_id，不生成原文）
│   ├── teacher.py              # 串流講解：stream_explanation_with_intent 內聯抽 INTENT JSON（省 1 次 LLM 來回）+ extract_teaching_intent fallback
│   ├── question_generator.py   # 出題（布魯姆 + teaching_intent 對齊 + JSON repair）
│   ├── evaluator.py            # 評分 + misconception_patterns 結構化診斷（Phase 3）
│   ├── progress_manager.py     # 決策（純規則，high_severity / repeated_patterns，Phase 4）
│   └── drift_verifier.py       # Citation accuracy 驗證（逐條 claim 核對，Phase 4）
├── orchestrator/
│   ├── learning_orchestrator.py  # 協調所有元件的主控流程
│   ├── debounced_writer.py       # DebouncedExplanationWriter：時間 + size 雙閘門 throttle 寫 DB
│   └── context_builder.py        # 學生狀態包組裝
├── ws/
│   └── generation_handle.py    # _GenerationHandle (task + event)；同步 register/finish/cancel +
│                                 #   async register_async/finish_async/cancel_async（同步寫 inflight_locks DB lock）
├── db/
│   ├── database.py             # SQLite 連線、16 個 migration（內嵌）；PRAGMA WAL
│   └── inflight_lock.py        # acquire/release/is_active/cleanup_stale（startup 清孤兒）
├── memory/
│   ├── working_memory.py       # 當次輪次狀態（含 current_teaching_intent）
│   ├── session_memory.py       # 本次學習進度 + source_chunks（SQLite）
│   └── longterm_memory.py      # 跨會話掌握度 + misconceptions（SQLite，EMA α=0.3）
├── routers/
│   ├── session.py              # 書櫃：sessions list / detail / title PATCH / DELETE
│   ├── upload.py               # 三種上傳：file / url / youtube_asr (NDJSON 串流)
│   ├── learner.py              # 學習統計（concepts / misconceptions / weak_count）
│   └── user_ui.py              # 跨裝置 UI 狀態（GET/PUT /user/ui-state，Migration 012）
├── utils/
│   ├── text_extractor.py       # 本地文件解析（PDF/DOCX/PPTX/HTML/MD/TXT）
│   ├── chunker.py              # 機械切分，結構優先（Wittgenstein 命題、Markdown 標題、Word heading）
│   ├── url_fetcher.py          # URL/YouTube 擷取（readability + strict_main + ASR fallback）
│   ├── prompt_templates.py     # 所有 LLM System Prompt
│   ├── token_counter.py        # tiktoken cl100k_base
│   └── logger.py
├── files/
│   └── upload_store.py         # 磁碟讀寫 data/uploads/{file_id}.bin + .meta.json
├── tools/
│   └── web_search.py           # DuckDuckGo Instant Answer API（ask_tutor 離題時用）
└── auth/                       # JWT 帳號系統（單裝置強制登出，session_version）
```

### 前端（React 19 + TypeScript + Vite + Zustand）

```
frontend/src/
├── App.tsx                     # 主畫面 + WebSocket 訊息路由 + UI chrome 持久化
├── main.tsx                    # render 前同步套用主題（避免 FOUC）
├── App.css / index.css         # CSS 變數驅動的雙主題（淺色 / 暖紙黑）
├── api/
│   ├── apiBase.ts              # HTTP base URL（環境變數）
│   ├── config.ts               # GET /config（取 default_provider）
│   ├── auth.ts                 # /auth/register / login / me
│   ├── session.ts              # 書櫃 CRUD（list/detail/rename/delete）
│   ├── upload.ts               # 三種上傳的封裝
│   ├── learner.ts              # 學習統計
│   ├── userUiState.ts          # 跨裝置 UI 狀態
│   └── websocket.ts            # WS 客戶端（WS_BASE 預設 ws://localhost:8000）
├── components/
│   ├── AuthForm.tsx              # 登入 / 註冊（hero illustration）
│   ├── BookshelfPanel.tsx        # 書櫃（多 session 列表、進度條、刪除、重新命名）
│   ├── UploadModal.tsx           # 多來源上傳（檔案 + URL + YouTube ASR + 純文字）
│   ├── KnowledgeMapModal.tsx     # 知識地圖確認（含覆蓋合約說明）
│   ├── StageMap.tsx              # 左側學習進度地圖
│   ├── ExplanationPanel.tsx      # 串流 Markdown 講解（含 KaTeX 數學公式）
│   ├── QuestionPanel.tsx         # 問答 + 掌握度標籤 + 反饋
│   ├── AskTutorPanel.tsx         # 學生提問（三態 scope 標籤、可收縮筆記、單筆刪除）
│   ├── LearningStatsPage.tsx     # 學習統計圖表（recharts，runtime 讀 CSS 變數）
│   ├── LearningCoachPanel.tsx    # 學習教練輔助面板
│   └── ThemeToggle.tsx           # 主題切換 icon 按鈕
├── store/
│   └── sessionStore.ts           # Zustand 全域狀態
├── utils/
│   ├── theme.ts                  # 主題 localStorage + applyTheme + 廣播事件
│   ├── bookshelfOrder.ts         # 書櫃排序（含跨裝置同步）
│   ├── sessionLayoutPrefs.ts     # session 內版面 prefs（含跨裝置同步）
│   └── userUiStateSync.ts        # 跟伺服器同步 UI state（debounce 800ms）
└── types/
    └── messages.ts               # WebSocket 訊息 TypeScript 型別
```

### 資料庫 Schema

十張資料表，由 `database.py` 內嵌的 **16 個 migration** 增量建立（冪等：`try/except ALTER` 與 `CREATE TABLE IF NOT EXISTS`，無 `schema_migrations` 追蹤表）：

| 表 | 用途 |
|----|------|
| `users` | 帳號 + `session_version`（單裝置強制登出，Migration 011） |
| `sessions` | 學習會話、`stages_json`、`provider/model`、`question_mode`、`title`、`source_file_ids_json`（GC 用） |
| `source_chunks` | 後端 source truth；`chunk_NNNN` 文件層級命名（Migration 009） |
| `stage_progress` | 各 stage 狀態、`full_explanation`、`questions_json` |
| `qa_records` | 答題歷史 |
| `decision_records` | 進度決策歷史（含 `strategy_snapshot_json` 與 selection_reason / high_severity / repeated_patterns） |
| `tutor_records` | ask_tutor 問答（含 `scope` 三態，Migration 013–014） |
| `concept_mastery` | 跨會話概念掌握度（EMA α=0.3）、結構化 misconception_patterns、成功 analogies |
| `user_learning_profile` | 學習風格、平均嘗試次數、`ui_state_json`（跨裝置 UI 同步，Migration 012） |
| `inflight_locks` | 跨 worker dedup lock：`key`/`session_id`/`kind`/`started_at`/`worker_pid`（Migration 016）；startup 清 stale ≥ 10 分鐘的孤兒 |

完整 schema、欄位、index、migration 列表見 [BACKEND_FLOW §3](./BACKEND_FLOW.md#3-資料庫-schema)。

### WebSocket 訊息協定

連線：`ws://localhost:8000/ws/{session_id}?token=JWT`

**Client → Server**

| type | payload |
|------|---------|
| `start_session` | `sources: [{type, file_id\|content, label}]`、`provider`、`target_depth`、`question_mode?`、`model?`；舊版單來源 `uploaded_file_id` / `content` 仍向下相容 |
| `confirm_map` | `provider?`、`model?` |
| `submit_answer` | `question_id`、`answer` |
| `resume_session` | `session_id`、`provider?`、`model?` |
| `ask_tutor` | `question`、`stage_id?` |
| `cancel_generation` | `key?`（不指定則 fallback 嘗試取消該 session 任何 in-flight） |
| `request_hint` | （目前固定回「即將開放」） |

**Server → Client**

| type | 主要欄位 | 說明 |
|------|----------|------|
| `kicked` | `message` | 同帳號其他裝置重連，code 4002 |
| `session_generating` | — | start_session 解析中 |
| `knowledge_map` | `nodes`、`summary` | 切割完成，等待確認 |
| `session_started` | `session_id`、`total_stages`、`stages`、`stage_statuses?` | 啟動 / 恢復 / advance 後刷新 |
| `session_snapshot` | `stage_explanations`、`stage_qa_histories`、`decision_history`、`tutor_histories` | resume 時全量推送 |
| `explanation_chunk` | `chunk`、`is_final` | 串流講解片段 |
| `explanation_complete` | `stage_id`、`stage_title`、`full_explanation` | 本 stage 講解完成 |
| `explanation_reset` | — | 僅 reteach 換框架時送出（現已不主動 reset，保留欄位向下相容） |
| `question` | `question_id`、`text`、`type`、`answer_mode`、`options`、`evidence_chunk_ids`、`stage_id`、`attempt_number` | 發送題目 |
| `feedback` | `question_id`、`score`、`feedback_text`、`needs_clarification`、`clarification_question?` | 評分結果 |
| `stage_decision` | `decision`、`message`、`next_stage_id?`、`best_score`、`reason_lines`、`strategy_snapshot` | 進度決策；`strategy_snapshot` 含 selection_reason、high_severity_misconceptions、repeated_patterns_detected |
| `qa_history` | `records` | resume 時的歷史答題 |
| `resume_state` | `current_question?`、`last_feedback?` | resume 時送出 |
| `tutor_chunk` | `chunk`、`stage_id`、`question` | ask_tutor 串流片段（前端逐字渲染） |
| `tutor_reply_complete` | `stage_id`、`question`、`full_answer` | tutor 串流結束 |
| `tutor_reply` | `question`、`answer`、`in_scope`、`scope`、`stage_id`、`id?` | 用於 cache hit（DB 已有同問題紀錄）直接 emit、不走 stream；`scope ∈ {current_chapter, other_chapter, out_of_scope}`；`id` 為 tutor_records.id（供前端刪除） |
| `generation_cancelled` | `key`、`kind ∈ {ask_tutor, other}` | 對應 client `cancel_generation` 的回應；前端用 `kind === 'ask_tutor'` 判斷是否清 streaming bubble |
| `hint` | `message` | 回應 `request_hint` |
| `course_completed` | `message` | 所有 stage 完成 |
| `error` | `message` | 錯誤通知 |

### 進度決策

`ProgressManagerAgent` 純規則計算（不呼叫 LLM）。主章節 10 條 + 子章節 6 條優先序，動態節點上限保護避免無限套娃。

| 決策 | 觸發要點 | 後續行為 |
|------|---------|---------|
| `advance` | 真正掌握（`best ≥ 0.75` 且無 confused） | 依 mastery_map / weak_overlap 排名選下一節點 |
| `reteach` | high severity OR 重複 pattern ≥ 2 OR mastery=none | 插入 `T.source.N` 重教子章節（最多 2 次） |
| `remediate` | best ≥ 0.75 但有 confused，或最後 fallback | 插入 `R.source.N` 補強子章節（最多 2 次） |
| `retry` | mastery=partial 且 attempts < 3 且 best ≥ 0.5 | 不清原文，附加「第 N 次嘗試」標題 + 新題 |

完整 10 條優先序、`mastery_state` 計算、子章節決策見 [BACKEND_FLOW §7.6](./BACKEND_FLOW.md#76-進度決策_make_progress_decision)。

### REST API 速覽

| 端點 | 說明 |
|------|------|
| `POST /auth/register` / `login` | 註冊 / 登入，回傳 JWT；登入會 `session_version += 1` |
| `GET /auth/me` | 驗證 JWT 並回傳當前使用者 |
| `POST /upload` | 檔案上傳，回 `file_id` |
| `POST /upload/url` | URL 擷取（YouTube 字幕缺失時回 HTTP 409） |
| `POST /upload/youtube/asr/stream` | YouTube ASR fallback（NDJSON 串流） |
| `GET /sessions/active` | 取得使用者最新 active / pending session |
| `GET /sessions/list` | 書櫃 |
| `GET /sessions/{sid}` | 單一 session 詳細 |
| `PATCH /sessions/{sid}/title` | 自訂標題 |
| `DELETE /sessions/{sid}` | 刪除 session + 相關紀錄 + GC 磁碟 blob |
| `GET /sessions/{sid}/stages/{sid}/explanation` | 回顧講解（從 DB 直接讀） |
| `GET /sessions/{sid}/stages/{sid}/qa_history` | 回顧答題 |
| `DELETE /sessions/{sid}/tutor/{rid}` | 刪除單筆 ask_tutor 紀錄 |
| `GET /learner/stats` | 學習統計 |
| `GET/PUT /user/ui-state` | 跨裝置 UI 狀態 |
| `GET /health` | 健康檢查 |
| `GET /config` | 取得 `default_provider`（供前端決定預設 provider） |

完整 schema 與行為見 [BACKEND_FLOW §5](./BACKEND_FLOW.md#5-rest-api-端點)。

---

## 生產部署

```powershell
# 1. 打包前端
cd frontend; npm run build

# 2. 後端自動偵測 frontend/dist/ 並掛載 /
cd ../backend; .\.venv\Scripts\uvicorn.exe run:app --port 8000
```

- 只需對外開放 8000 port；REST `/auth/*` / `/sessions/*` 與 WebSocket `/ws/*` 路由不受影響
- 若用 Cloudflare Quick Tunnel：`CORS_ORIGIN_REGEX` 預設已含 `*.trycloudflare.com`
- 自訂網域：用 `CORS_ORIGINS` 環境變數逗號分隔額外網域

---

## 開發注意事項

- 工具選擇與 Windows 路徑陷阱、PowerShell 5.1 陷阱見 [CLAUDE.md](./CLAUDE.md)
- 完整資料流、Agent 細節、決策邏輯、System Prompt 一覽見 [BACKEND_FLOW.md](./BACKEND_FLOW.md)
- WebSocket URL 在 `frontend/src/api/websocket.ts` 中以 `WS_BASE = "ws://localhost:8000"` 硬編碼，部署時請改為環境變數
