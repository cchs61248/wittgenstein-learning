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
| 檔案上傳 | `POST /upload` | `.txt .md .pdf .docx .pptx .html .htm .epub`；單檔 10 MB（可由 `UPLOAD_MAX_FILE_MB` 調整） |
| URL 擷取 | `POST /upload/url` | 公開網頁（readability-lxml + 全頁清洗 fallback + strict_main 主文截斷）、YouTube 字幕；單次最多 500,000 字 |
| YouTube ASR | `POST /upload/youtube/asr/stream` | YouTube 影片無字幕時的 fallback：`yt-dlp` 下載音訊 + `faster-whisper`（small/cpu/int8）轉寫，串流回報進度 |
| 純文字 | 前端直接帶入 `start_session.sources` | 不經過上傳 API，貼上即用 |

**EPUB 多章節**：上傳 .epub 時後端用 `split_epub_by_toc` 按 TOC 切成 N 個章節獨立 file_id（檔名 `001_章節標題.txt`），前端清單一次塞 N 行可逐章 ✕ 移除。

**多來源 session**：可同時上傳多份檔案 + URL + 文字，前端會強制使用者選「是否同一教材」radio（1 source 自動視為同一）；切分流程統一走 `per_source_split`（逐檔切 + 程式合併），由 `same_material` 決定後處理模式（見下）。

### 教材切分（2026-05-27 統一架構；2026-05-29 mode-aware 後處理；2026-06-01 Phase 4 跨教材排序；2026-06-02 warn-only 品質偵測器）

- **唯一路徑**：所有 session 走 V2 小檔逐檔切（`single_split` 或 `per_source_split`），V1 / V2 大檔 / Plan B 全部刪除；reducer 改為非主線（unified path `reducer_skipped=True`，`global_curriculum_reducer` / `macro_region_refiner` prompt 仍留存但不呼叫）。SplitterVerifier 非阻塞（soft-pass / false-positive filter / bounded reroll，無 fail-hard）
- **`same_material` 控制 ContentOutline（Phase 3，2026-05-29 起）**：`same_material=True` → **一律跳過 Outline**（含 ≥3 章 EPUB）；只有 `same_material=False`（不同教材）才整批跑一次 Outline 餵給逐檔 Splitter。原「同教材 ≥3 章也跑 Outline」規則已移除——global outline 的跨章 `named_cases` 會把不同章同主題 chunk 併進同一 stage（章節邊界破壞器），章節排序改由確定性 `SourceOrderResolver` 處理
- **Mode-aware 後處理**：`choose_postprocess_mode(n_sources, same_material)` 分流；只有 `cross_material_merge_and_coordinate`（多本不同書）才跑 jaccard / LLM consolidator 等合併層，單 source 與同教材只做確定性排序 + 收尾，不合併 stage
- **Phase 4 跨教材教學循序排序（COMPLETE，預設 off）**：多本不同教材排成「概論→基礎→核心→進階→應用→總結」。`PedagogicalPlannerAgent` 只提 move plan、程式驗證覆蓋後套用、失敗安全 fallback。env `CROSS_MATERIAL_PEDAGOGICAL_PLANNER=1` 啟用，且需 `same_material=False` + 過 activation gate（chunks≥30/stages≥6/sources≥3）；flag-off bit-for-bit 等價。接在 `finalize_curriculum_stages` 之後成為最後動順序者。Live 驗收 `sess_r14gdzg7x` PASS（summary 由中段移到最後）。詳見 [CURRICULUM_SPLIT_FLOWS §七-A](./CURRICULUM_SPLIT_FLOWS.md)
- **Warn-only 品質偵測器家族（2026-06-02）**：切分收尾掛一族純程式、確定性、無 LLM、**不改 stage / 不改路由**的偵測器，命中只寫 `quality_warnings` + log，永不阻斷或變更切分結果——`empty_curriculum`（切出空課綱，補 `zero_stages` 抓不到的盲區）、`large_single_source_risk`（單檔 ≥50 chunks 走無分批 single_split，output-aware severity）、`generic_kc_collapse`（跨 stage 關鍵詞退化成傘狀空詞）、`medium_cross_material_gap`（跨教材但 chunks<30，consolidator/Phase4 都沒跑）。同批並把「單 stage chunk 上限（14）」改為兩條後處理路徑皆**無條件強制**（T-STAGE-CAP）。詳見 [CURRICULUM_SPLIT_FLOWS §九-A](./CURRICULUM_SPLIT_FLOWS.md) / [BACKEND_FLOW §7.2.1](./BACKEND_FLOW.md)
- **EPUB 上傳即切章**：`POST /upload` 收 `.epub` 時呼叫 `split_epub_by_toc` 回 N 個 file_id；前端展開為 N 個 source items
- **閾值 env 化**：`STAGE_TITLE_MERGE_THRESHOLD`（預設 0.85）控制標題去重合併粒度
- **Canonicalize 可選**：`CONCEPT_CANONICALIZE=1` 啟用統一關鍵詞命名（預設 off）
- **Resume**：DB `sessions.same_material` 欄位記錄選擇，重啟後恢復一致流程
- **Checkpoint 斷點續跑**：`curriculum_checkpoints` 記錄已完成切分區段，worker 重啟不從頭生成
- **Arq + Redis 背景 worker**：`CURRICULUM_USE_ARQ=1` 時 uvicorn 只 prepare + enqueue
- **LLM result cache**：`LLM_CACHE_ENABLED=1` 時 curriculum agents 共用 PostgreSQL cache（`llm_result_cache` 表）
- **Docker Compose**：`docker compose up -d` 一鍵啟動 Redis（`:6380`）+ curriculum-worker

> 設計文件：`docs/superpowers/specs/2026-05-27-curriculum-unify-v2-design.md`
> 切分流程白話版：[CURRICULUM_SPLIT_FLOWS.md](./CURRICULUM_SPLIT_FLOWS.md)
> 手動 e2e 清單：[MANUAL_E2E_CHECKLIST.md](./MANUAL_E2E_CHECKLIST.md)

### 學習迴圈（Phase 1–4 完整實作）

- **Source Truth 後端掌控**：上傳後 `text_extractor → chunker` 建立 `source_chunks` 表，LLM 只做語義切分回傳 `chunk_id`，原文一律由後端回填，杜絕幻覺引用
- **Teacher 串流講解**：Markdown 即時渲染，依學生掌握度 / 混淆模式 / 選課理由（Phase 4）自適應，每個概念至少 2 個生活化類比，類比明確標記為「說明工具」不可作為題目素材
- **Citation Accuracy 驗證**：DriftVerifier 逐條 claim 比對原文，同時支援 Markdown `[chunk_id]` 與 JSON 陣列兩種格式；不通過自動重生（附 revision_hint）
- **出題嚴格對齊講解**（2026-05-19）：QuestionGenerator 題目必須測試 `full_explanation` 中明確展開過的概念（chunks 提到但講解略過的概念禁止出題）；DriftVerifier questions 模式以 full_explanation 為唯一對齊基準，漂移時把 `unsupported_claims` 注入 retry prompt 重生一次；retry 後仍漂移的題目以 `[註：本題未對齊講解]` 軟性標記持久化，不阻斷流程
- **「字面提及 vs 有展開」雙管把關**（2026-05-19 A+B）：DriftVerifier 升級為三態判定（沒提 / 字面提及 / 有展開），即便 chunks 有完整說明，只要 explanation 只把名詞當道具引用沒展開運作，題目考運作特性仍視為漂移；Teacher prompt 同時規定「提及的專有名詞必須至少一句解釋運作/特性/意義」，否則應改用淺白詞彙描述、不提名詞，避免後續出題誤把未教概念當已教概念
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
- PostgreSQL 16（或 Docker：`docker compose up -d postgres`）
- 跑後端測試另需 Docker daemon（testcontainers 起拋棄式 PG）

### 後端（Windows PowerShell 範例）

```powershell
cd backend

# 建立虛擬環境
C:\Windows\py.exe -3 -m venv .venv

# 安裝依賴
.\.venv\Scripts\pip install -r requirements.txt

# 設定環境變數
copy .env.example .env
# 編輯 .env，至少填入一組 LLM API key 與 DATABASE_URL

# 起 PostgreSQL（專案根目錄；或自備 PG 並設好 DATABASE_URL）
docker compose up -d postgres

# 啟動伺服器（入口是 run.py 不是 main.py）
.\.venv\Scripts\uvicorn.exe run:app --reload --port 8000
```

> schema 收斂於 `backend/db/schema.sql`（單一 baseline，無歷史 migration）；`init_db` 啟動時冪等套用（`CREATE TABLE IF NOT EXISTS`）。連線用 asyncpg pool，由 `DATABASE_URL` 設定。

> 開發時請務必用 `uvicorn run:app` 而非 `uvicorn main:app`；`run.py` 會把上層目錄加入 `sys.path` 才能正確匯入 `backend.*`。

### Curriculum 背景 worker（Arq 模式，可選）

長教材建議啟用 Arq，讓生成與 API 解耦：

**1. `.env` 設定**
```env
CURRICULUM_USE_ARQ=1
REDIS_URL=redis://localhost:6380/0
LLM_CACHE_ENABLED=1
# 可選微調：
# STAGE_TITLE_MERGE_THRESHOLD=0.85
# CONCEPT_CANONICALIZE=0
```

**2. Docker 啟動 Redis + worker**（專案根目錄）
```powershell
docker compose up -d --build
docker compose logs -f curriculum-worker
```

**3. 重啟 uvicorn**（不帶 `--reload` 較穩）
```powershell
cd backend
.\.venv\Scripts\uvicorn.exe run:app --port 8000
```

| 容器 | 用途 | Host 埠 |
|------|------|---------|
| `wl-postgres` | PostgreSQL 16（資料庫，DBeaver 可連） | `5432` |
| `wl-redis` | Arq 佇列 | `6380` |
| `wl-curriculum-worker` | 執行 `run_curriculum_job` | — |

Compose 會起 `postgres`（資料落在 named volume `pg_data`）、`redis`，並 bind-mount `./backend` 方便開發。api / worker 皆透過 docker network 以 `DATABASE_URL=postgresql://wl:wl@postgres:5432/wl` 連 DB；worker 容器內 `MONICA_BASE_URL` 預設指向 `host.docker.internal:8001`（本機 Monica 代理）。

> **DB 連線**：PostgreSQL 原生處理多 process 併發寫，uvicorn 與 worker 連同一個 DB 即可，無 SQLite 時代的 bind-mount / WAL / 種子複製問題。外部工具（DBeaver 等）連 `localhost:5432`，帳密 `wl/wl`、DB `wl`。
> **純本機開發**也可不用 Docker 跑 worker（DB 仍需一個可連的 PostgreSQL，設好 `DATABASE_URL`）：
> ```powershell
> $env:REDIS_URL="redis://localhost:6380/0"
> $env:DATABASE_URL="postgresql://wl:wl@localhost:5432/wl"
> ..\.venv\Scripts\python.exe -m arq backend.jobs.arq_settings.WorkerSettings
> ```

詳見 [BACKEND_FLOW §12](./BACKEND_FLOW.md#12-curriculum-背景化checkpoint--arq--llm-cache)。

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
| `DATABASE_URL` | PostgreSQL 連線（asyncpg） | `postgresql://wl:wl@localhost:5432/wl` |
| `DB_POOL_MIN_SIZE` | asyncpg pool 最小連線數 | `1` |
| `DB_POOL_MAX_SIZE` | asyncpg pool 最大連線數 | `10` |
| `JWT_SECRET` | JWT 簽名密鑰 | `dev-secret-change-in-production` |
| `JWT_EXPIRE_DAYS` | JWT 有效期（天） | `7` |
| `CORS_ORIGINS` | 額外允許的 CORS 來源（逗號分隔；不設則用 Vite dev server 預設） | — |
| `CORS_ORIGIN_REGEX` | CORS regex 白名單（如 Cloudflare Quick Tunnel） | `https://.*\.trycloudflare\.com` |
| `CURRICULUM_USE_ARQ` | `1` 時 start_session 改 enqueue，由 Arq worker 執行 | `0` |
| `REDIS_URL` | Arq 佇列 URL；docker compose 用 `redis://localhost:6380/0` | `redis://localhost:6379/0` |
| `LLM_CACHE_ENABLED` | `1` 啟用 curriculum LLM result cache | `0` |
| `STAGE_TITLE_MERGE_THRESHOLD` | stage 標題去重合併閾值（0~1）；高=保守、低=積極合併 | `0.85` |
| `CONCEPT_CANONICALIZE` | `1` 時 stage 合併後再跑 ConceptCanonicalize LLM 統一關鍵詞 | `0` |
| `CROSS_MATERIAL_PEDAGOGICAL_PLANNER` | `1` 啟用 Phase 4 跨教材教學循序重排（僅 `same_material=False` + 過 gate；off 時 bit-for-bit 等價） | `0` |

> `PASS_THRESHOLD=0.75` 與 `MAX_STAGE_ATTEMPTS=3` 寫死在 orchestrator 中。Arq 相關：`ARQ_MAX_JOBS`（預設 1）、`ARQ_JOB_TIMEOUT_S`（預設 7200）、`LLM_CACHE_EVICT_DAYS`（預設 90）。完整列表見 [BACKEND_FLOW §10](./BACKEND_FLOW.md#10-設定與環境變數)。

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
│   ├── caching_provider.py     # CachingLLMProvider（LLM_CACHE_ENABLED 時包裝）
│   ├── cache_context.py        # curriculum agent llm_cache_context
│   └── provider_factory.py     # create_provider(name, model?) — 名稱大小寫不敏感
├── jobs/                       # Arq 背景 worker（CURRICULUM_USE_ARQ=1）
│   ├── arq_settings.py           # WorkerSettings（startup 掃 resumable + enqueue）
│   ├── curriculum_job.py         # run_curriculum_job 入口
│   ├── enqueue.py                # enqueue_curriculum_job + inflight_key
│   └── session_prepare.py        # prepare_curriculum_session（寫 DB + checkpoint meta）
├── docker/
│   └── worker-entrypoint.sh      # Docker worker：copy DB 至 volume 再執行
├── agents/                     # 六個功能 Agent + BaseAgent
│   ├── base_agent.py           # _messages 自動 _reset()，token 預算管理
│   ├── content_splitter.py     # 語義切分（只回傳 chunk_id，不生成原文）
│   ├── teacher.py              # 串流講解：stream_explanation_with_intent 內聯抽 INTENT JSON（省 1 次 LLM 來回）+ extract_teaching_intent fallback
│   ├── question_generator.py   # 出題（布魯姆 + teaching_intent 對齊 + JSON repair）
│   ├── evaluator.py            # 評分 + misconception_patterns 結構化診斷（Phase 3）
│   ├── progress_manager.py     # 決策（純規則，high_severity / repeated_patterns，Phase 4）
│   ├── drift_verifier.py       # Citation accuracy 驗證（逐條 claim 核對，Phase 4）
│   └── （curriculum-build agents）content_outline / splitter_verifier / stage_consolidator / concept_canonicalize / pedagogical_planner（跨教材教學循序重排）
├── orchestrator/
│   ├── learning_orchestrator.py  # 協調所有元件的主控流程
│   ├── curriculum_pipeline_v2.py # V2 小檔統一切分 pipeline（single/per-source split；reducer_skipped）
│   ├── curriculum_resume.py      # resume_generating_session（checkpoint 續跑）
│   ├── debounced_writer.py       # DebouncedExplanationWriter：時間 + size 雙閘門 throttle 寫 DB
│   └── context_builder.py        # 學生狀態包組裝
├── ws/
│   └── generation_handle.py    # _GenerationHandle (task + event)；同步 register/finish/cancel +
│                                 #   async register_async/finish_async/cancel_async（同步寫 inflight_locks DB lock）
├── db/
│   ├── database.py             # asyncpg pool（get_db/init_db/close_db）；套用 schema.sql baseline
│   ├── schema.sql              # 單一 baseline schema（13 張表，無歷史 migration）
│   └── inflight_lock.py        # acquire/release/cleanup_stale/cleanup_dead_worker_locks
├── memory/
│   ├── working_memory.py       # 當次輪次狀態（含 current_teaching_intent）
│   ├── session_memory.py       # 本次學習進度 + source_chunks（PostgreSQL）
│   ├── longterm_memory.py      # 跨會話掌握度 + misconceptions（PostgreSQL，EMA α=0.3）
│   ├── curriculum_checkpoint.py # V2 region checkpoint CRUD
│   └── llm_cache.py            # llm_result_cache CRUD
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
│   ├── web_search.py           # DuckDuckGo Instant Answer API（ask_tutor 離題時用）
│   ├── resume_curriculum.py      # CLI：手動 resume generating session
│   ├── llm_cache_stats.py        # CLI：LLM cache 統計
│   └── live_arq_verify.py        # Live 驗證：prepare + enqueue + monitor checkpoint
└── auth/                       # JWT 帳號系統（單裝置強制登出，session_version）
```

根目錄另有：
```
docker-compose.yml              # wl-redis + wl-curriculum-worker
Dockerfile.worker               # worker 映像
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

13 張資料表，集中定義於 `backend/db/schema.sql`（單一 baseline，無歷史 migration）；`init_db` 啟動時以 `CREATE TABLE IF NOT EXISTS` 冪等套用：

| 表 | 用途 |
|----|------|
| `users` | 帳號 + `session_version`（單裝置強制登出） |
| `sessions` | 學習會話、`stages_json`、`provider/model`、`question_mode`、`title`、`target_depth`、`source_file_ids_json` |
| `source_chunks` | 後端 source truth；`chunk_NNNN` 文件層級命名 |
| `stage_progress` | 各 stage 狀態、`full_explanation`、`questions_json` |
| `qa_records` | 答題歷史 |
| `decision_records` | 進度決策歷史（含 `strategy_snapshot_json` 與 selection_reason / high_severity / repeated_patterns） |
| `tutor_records` | ask_tutor 問答（含 `scope` 三態） |
| `concept_mastery` | 跨會話概念掌握度（EMA α=0.3）、`source_signature` 跨教材隔離 |
| `user_learning_profile` | 學習風格、平均嘗試次數、`ui_state_json`（跨裝置 UI 同步） |
| `inflight_locks` | 跨 worker dedup lock；startup 清 stale / dead worker |
| `curriculum_checkpoints` | V2 pipeline region 斷點 |
| `llm_result_cache` | curriculum LLM 結果快取 |
| `email_whitelist` | 註冊白名單 + 角色（`admin` / `user`）；只有白名單 email 可註冊 |

完整 schema、欄位、index 見 [BACKEND_FLOW §3](./BACKEND_FLOW.md#3-資料庫-schema)。

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

# 2. 後端（+ 可選 Arq worker）
cd ..
docker compose up -d          # Redis + curriculum-worker（CURRICULUM_USE_ARQ=1 時）
cd backend; .\.venv\Scripts\uvicorn.exe run:app --port 8000
```

- 只需對外開放 8000 port；REST `/auth/*` / `/sessions/*` 與 WebSocket `/ws/*` 路由不受影響
- 若用 Cloudflare Quick Tunnel：`CORS_ORIGIN_REGEX` 預設已含 `*.trycloudflare.com`
- 自訂網域：用 `CORS_ORIGINS` 環境變數逗號分隔額外網域

---

## 開發注意事項

- 工具選擇與 Windows 路徑陷阱、PowerShell 5.1 陷阱見 [CLAUDE.md](./CLAUDE.md)
- 完整資料流、Agent 細節、決策邏輯、System Prompt 一覽見 [BACKEND_FLOW.md](./BACKEND_FLOW.md)
- WebSocket URL 在 `frontend/src/api/websocket.ts` 中以 `WS_BASE = "ws://localhost:8000"` 硬編碼，部署時請改為環境變數
