# 維特根斯坦學習系統

一套以維特根斯坦哲學為核心的互動式學習平台。透過「講解 → 提問 → 反饋 → 調整」的閘門式循環，確保學習者真正內化當前概念後才進入下一階段。

## 核心理念

系統遵循三個哲學原則：

- **語言遊戲單元**：每個學習階段都是一個可獨立理解的完整概念單位
- **家族相似性**：Teacher Agent 從至少兩個不同角度提供比喻，建立概念網絡
- **理解是光譜**：評估不採用通過/失敗二元判定，而是 0.0–1.0 的連續分數，搭配四種決策路徑（前進、重試、補充、重新講解）

---

## 功能

- 上傳任意學習材料（PDF / DOCX / PPTX / MD / TXT），系統本地解析並建立 source_chunks（後端掌控 source truth），自動切割為有序學習階段
- Teacher 串流講解（Markdown 即時渲染），根據學生掌握度、混淆模式、選課理由自適應調整重點，每個概念至少 2 個生活化類比
- 問題生成與 TeacherAgent 的教學意圖（teaching_intent）對齊：直接測試修正目標概念的核心原理；TeacherAgent 自創類比明確標記為「說明工具，禁止作為題目素材」，確保所有問題均可回溯至 source_chunks
- EvaluatorAgent 輸出結構化 misconception_patterns（concept / pattern / severity / repair_strategy），供長期診斷使用
- DriftVerifier Citation Accuracy（Phase 4）：逐條驗證引用是否真實支撐對應主張，同時支援 Markdown `[chunk_id]` 與 JSON 陣列兩種格式的 chunk 引用解析
- ProgressManager 智能決策（Phase 4）：高嚴重度根本誤解或同一錯誤重複 ≥ 2 次，立即觸發換框架重教
- retry / remediate 決策不清除原文，改為在現有講解尾端附加內容；remediate 會完整串流補強教學文章（TeacherAgent），並持久化至 DB，頁面重整後不重新生成
- 跨會話長期記憶：結構化追蹤每個概念的掌握度（EMA）、混淆模式、成功類比，選課時傳遞理由給 TeacherAgent
- 支援四個 LLM Provider（Claude、OpenAI、Gemini、Monica）
- 學生追問（Ask Tutor）：可詢問教材內外問題，回答以可收縮筆記方式記錄，透過 localStorage 持久化，頁面重整後自動恢復
- 頁面重整、多裝置切換後完整恢復學習進度（含講解、答題歷史、追問記錄）

---

## 快速開始

### 環境需求

- Python 3.11+
- Node.js 18+

### 後端

```bash
cd backend

# 建立虛擬環境（Windows）
C:\Windows\py.exe -3 -m venv .venv

# 安裝依賴
.venv\Scripts\pip install -r requirements.txt

# 設定環境變數
copy .env.example .env
# 編輯 .env，填入 API 金鑰

# 啟動伺服器
.venv\Scripts\uvicorn.exe run:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

開啟瀏覽器至 `http://localhost:5173`，註冊帳號後即可開始使用。

---

## 環境變數（`backend/.env`）

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `ANTHROPIC_API_KEY` | Claude API 金鑰 | — |
| `OPENAI_API_KEY` | OpenAI API 金鑰 | — |
| `GOOGLE_API_KEY` | Gemini API 金鑰 | — |
| `DEFAULT_PROVIDER` | 預設 LLM | `claude` |
| `PASS_THRESHOLD` | 進入下一階段的分數門檻 | `0.75` |
| `MAX_STAGE_ATTEMPTS` | 同一階段最大嘗試次數 | `3` |
| `DB_PATH` | SQLite 資料庫路徑 | `../data/learning.db` |
| `JWT_SECRET` | JWT 簽名金鑰 | — |
| `JWT_EXPIRE_DAYS` | Token 有效天數 | `7` |

---

## 架構

### 後端（FastAPI + Python）

```
backend/
├── main.py                   # FastAPI 入口、WebSocket 路由
├── config.py                 # 環境變數載入
├── run.py                    # uvicorn 入口（修正相對匯入問題）
├── llm/                      # LLM 抽象層
│   ├── base_provider.py      # BaseLLMProvider 介面
│   ├── claude_provider.py
│   ├── openai_provider.py
│   ├── gemini_provider.py
│   └── provider_factory.py
├── agents/                   # 六個功能 Agent
│   ├── content_splitter.py   # 語義切分（只回傳 chunk_id，不生成原文）
│   ├── teacher.py            # 串流講解 + extract_teaching_intent（Phase 3）
│   ├── question_generator.py # 出題（布魯姆 + teaching_intent 對齊，Phase 3）
│   ├── evaluator.py          # 評分 + misconception_patterns 結構化診斷（Phase 3）
│   ├── progress_manager.py   # 決策（純規則 + high_severity/repeated_patterns，Phase 4）
│   └── drift_verifier.py     # Citation accuracy 驗證（逐條 claim 核對，Phase 4）
├── orchestrator/
│   ├── learning_orchestrator.py  # 協調所有元件的主控流程
│   └── context_builder.py        # 學生狀態包組裝（Phase 2）
├── memory/
│   ├── working_memory.py     # 當次輪次狀態（含 current_teaching_intent，Phase 3）
│   ├── session_memory.py     # 本次學習進度 + source_chunks（SQLite，Phase 1）
│   └── longterm_memory.py    # 跨會話掌握度 + misconceptions（SQLite，Phase 2+3）
├── utils/
│   ├── text_extractor.py     # 本地文件解析（PDF/DOCX/PPTX/MD/TXT，Phase 1）
│   ├── chunker.py            # 機械切分，建立 source_chunks（Phase 1）
│   └── prompt_templates.py   # 所有 LLM System Prompt
├── auth/                     # JWT 帳號系統
└── db/                       # SQLite 連線與 migrations（含 source_chunks 表，Phase 1）
```

**Agent 運作方式**：每個 Agent 擁有獨立的 `_messages` 列表，`run()` 開始與結束時都呼叫 `_reset()` 清除，避免跨呼叫上下文累積。各 Agent 有各自的 token 預算（800–4000 tokens）。

**五種進度決策（Phase 4 更新）**：

| 決策 | 觸發條件 | 優先序 | 行為 |
|------|---------|--------|------|
| `advance` | best_score ≥ 0.75 | 1（最高） | 依掌握度/弱點/新知排名選下一節點（可插入整合挑戰節點） |
| `reteach` | high severity misconception（任何嘗試次數） | 2 | 先持久化當前講解，再換框架全新串流重教 + 新題 |
| `reteach` | 同一 pattern 重複 ≥ 2 次 | 3 | 先持久化當前講解，再換比喻框架全新講解 + 新題 |
| `retry` | attempts < 3 且 best_score < 0.75 | 4 | 不清除原文，附加「第 N 次嘗試」標題 + 重新出題 |
| `reteach` | attempts == 3 且 latest_score < 0.5 | 5 | 先持久化當前講解，再換框架重教 |
| `remediate` | 其餘情況 | 6（最低） | 不清除原文，串流補強教學文章（TeacherAgent），附加至原文尾端並持久化 |

### 前端（React + TypeScript + Vite）

```
frontend/src/
├── App.tsx                   # 主畫面與 WebSocket 訊息路由
├── api/
│   ├── auth.ts               # REST 登入/註冊
│   └── websocket.ts          # WebSocket 客戶端封裝
├── components/
│   ├── AuthForm.tsx           # 登入/註冊表單
│   ├── UploadModal.tsx        # 上傳學習材料
│   ├── KnowledgeMapModal.tsx  # 知識地圖確認（含覆蓋合約說明）
│   ├── StageMap.tsx           # 左側學習進度地圖
│   ├── ExplanationPanel.tsx   # 串流 Markdown 講解
│   ├── QuestionPanel.tsx      # 問答（含掌握度標籤 + 鼓勵語）與反饋
│   ├── AskTutorPanel.tsx      # 學生提問（範疇內/外，可收縮筆記，localStorage 持久化）
│   └── LearningCoachPanel.tsx # 學習教練輔助面板
├── store/
│   └── sessionStore.ts        # Zustand 全域狀態
└── types/
    └── messages.ts            # WebSocket 訊息 TypeScript 型別
```

### WebSocket 訊息協定

**Client → Server**

```json
{ "type": "start_session", "payload": { "content": "...", "provider": "claude", "target_depth": "intermediate" } }
{ "type": "submit_answer",  "payload": { "question_id": "q_1_0", "answer": "..." } }
```

**Server → Client**

```
session_started     → { session_id, stages, stage_statuses }
session_snapshot    → { stage_explanations, stage_qa_histories, decision_history }
explanation_chunk   → { chunk, is_final }
explanation_complete→ { stage_id, full_explanation }
explanation_reset   → {}   （僅 reteach 換框架時送出）
question            → { question_id, text, type, stage_id, attempt_number, options? }
feedback            → { score, feedback_text, needs_clarification, clarification_question? }
stage_decision      → { decision, message, next_stage_id, best_score, strategy_snapshot }
tutor_reply         → { question, answer, in_scope }
qa_history          → { records }
resume_state        → { current_question?, last_feedback? }
course_completed    → { message }
kicked              → { message }
error               → { message }
```

### 資料庫 Schema

八張資料表：`users`、`sessions`、`stage_progress`、`qa_records`、`concept_mastery`、`user_learning_profile`、`decision_records`、`source_chunks`（Phase 1，後端 source truth）。

資料庫在首次啟動時自動建立（`data/learning.db`），透過 migration 系統增量更新。

---

## 生產部署

```bash
# 打包前端
cd frontend && npm run build

# 後端自動偵測 frontend/dist/ 並掛載
cd backend && .venv\Scripts\uvicorn.exe run:app --port 8000
```

後端掛載前端靜態檔後，只需對外開放 8000 port。REST API（`/auth/*`）與 WebSocket（`/ws/*`）路由不受影響。
