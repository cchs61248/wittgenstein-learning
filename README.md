# 維特根斯坦學習系統

一套以維特根斯坦哲學為核心的互動式學習平台。透過「講解 → 提問 → 反饋 → 調整」的閘門式循環，確保學習者真正內化當前概念後才進入下一階段。

## 核心理念

系統遵循三個哲學原則：

- **語言遊戲單元**：每個學習階段都是一個可獨立理解的完整概念單位
- **家族相似性**：Teacher Agent 從至少兩個不同角度提供比喻，建立概念網絡
- **理解是光譜**：評估不採用通過/失敗二元判定，而是 0.0–1.0 的連續分數，搭配四種決策路徑（前進、重試、補充、重新講解）

---

## 功能

- 上傳任意學習材料，系統自動切割為有序學習階段
- Teacher 串流講解（Markdown，即時渲染）
- 布魯姆分類法出題（理解型 / 應用型 / 創造型）
- 評估後依掌握程度自動決策下一步
- 跨會話長期記憶：追蹤每個概念的掌握度與學習風格
- 支援三個 LLM Provider（Claude、OpenAI、Gemini）

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
├── agents/                   # 五個功能 Agent
│   ├── content_splitter.py   # 切割學習材料
│   ├── teacher.py            # 串流講解生成
│   ├── question_generator.py # 出題
│   ├── evaluator.py          # 評分
│   └── progress_manager.py   # 決策
├── orchestrator/
│   └── learning_orchestrator.py  # 協調所有 Agent 的主控流程
├── memory/
│   ├── working_memory.py     # 當次輪次狀態（in-process）
│   ├── session_memory.py     # 本次學習進度（SQLite）
│   └── longterm_memory.py    # 跨會話概念掌握度（SQLite）
├── auth/                     # JWT 帳號系統
└── db/                       # SQLite 連線與 migrations
```

**Agent 運作方式**：每個 Agent 擁有獨立的 `_messages` 列表，`run()` 結束後呼叫 `_reset()` 清除，避免跨呼叫上下文累積。各 Agent 有各自的 token 預算（800–4000 tokens）。

**四種進度決策**：

| 決策 | 觸發條件 | 行為 |
|------|---------|------|
| `advance` | 平均分 ≥ 0.75 | 進入下一階段 |
| `retry` | 分數不足且嘗試 < 3 次 | 降低難度重新出題 |
| `remediate` | 多次失敗 | 插入補充說明後重試 |
| `reteach` | 完全無法理解 | Teacher 換比喻框架重新講解 |

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
│   ├── StageMap.tsx           # 左側學習進度地圖
│   ├── ExplanationPanel.tsx   # 串流 Markdown 講解
│   └── QuestionPanel.tsx      # 問答與反饋
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
session_started   → { session_id, stages: [{stage_id, title}] }
explanation_chunk → { chunk, is_final }
question          → { question_id, text, type, stage_id, attempt_number }
feedback          → { score, feedback_text, needs_clarification }
stage_decision    → { decision, message, next_stage_id, best_score }
course_completed  → { message }
```

### 資料庫 Schema

六張資料表：`users`、`sessions`、`stage_progress`、`qa_records`、`concept_mastery`、`user_learning_profile`。

資料庫在首次啟動時自動建立（`data/learning.db`）。

---

## 生產部署

```bash
# 打包前端
cd frontend && npm run build

# 後端自動偵測 frontend/dist/ 並掛載
cd backend && .venv\Scripts\uvicorn.exe run:app --port 8000
```

後端掛載前端靜態檔後，只需對外開放 8000 port。REST API（`/auth/*`）與 WebSocket（`/ws/*`）路由不受影響。
