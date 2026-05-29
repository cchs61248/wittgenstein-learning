# 手動 E2E 測試清單（Curriculum Pipeline 統一化）

> 對應 spec：`docs/superpowers/specs/2026-05-27-curriculum-unify-v2-design.md` §7.2
> 對應分支：`feat/unify-v2-small-file-pipeline`
> 最後更新：2026-05-27

統一架構後（V2 小檔逐檔切為唯一路徑）的手動驗證清單。每項在瀏覽器跑過後勾選並記錄結果。

## 啟動環境

```powershell
# 1) Redis
docker compose up -d redis

# 2) Backend uvicorn（terminal 1）
cd backend
..\.venv\Scripts\uvicorn.exe run:app --reload --port 8000

# 3) Arq worker（terminal 2）
cd backend
..\.venv\Scripts\python.exe -m arq backend.jobs.arq_settings.WorkerSettings

# 4) Frontend dev server（terminal 3）
cd frontend
npm run dev
```

開瀏覽器 `http://localhost:5173`，註冊 / 登入。

## 情境表

| # | 操作 | 預期結果 | PASS/FAIL | 備註 |
|---|------|----------|-----------|------|
| 1 | 上傳 **1 個 .txt**（任何單檔教材） | UI 隱藏「是否同一教材」radio；「開始學習」按鈕可按；後端走 `same_material=True` | | 1-source 自動隱藏 |
| 2 | 上傳 **5 個獨立 .txt**（不同主題）+ 勾「不同教材」 | 按鈕可按。Worker log：**1 次 ContentOutline + 5 次 ContentSplitter（每檔一次）** | | 對應 `same_material=False` |
| 3 | 上傳 **5 個獨立 .txt** + 勾「同一教材」 | 按鈕可按。Worker log：**無 ContentOutline，只有 5 次 ContentSplitter** | | 同教材→跳過 Outline |
| 4 | 上傳 **1 個 .epub**（多章 TOC，例如 8 章） | 前端清單塞 **8 行 `001_xxx.txt` ~ `008_xxx.txt`**；可逐章 ✕ 移除 | | 後端 split_epub_by_toc |
| 5 | 從情境 4 留下 **6 章** + 勾「同一教材」按開始 | 走逐檔切，無 Outline。最終 stage 數合理；標題去重閾值 0.85 觀察是否誤合 | | 觀察合併效果 |
| 6 | 設 `$env:STAGE_TITLE_MERGE_THRESHOLD="0.95"` 重啟 worker，重跑情境 5 | 合併變保守，stage 數 ≥ 情境 5 | | 閾值調高=保守 |
| 7 | 設 `$env:CONCEPT_CANONICALIZE="1"` 重啟 worker，重跑情境 5 | Worker log 多出 `ConceptCanonicalizeAgent` 行；stage `key_concepts` 命名跨檔統一 | | 預設 off，env 開啟 |
| 8 | 上傳 **2 個 .txt**，**不選任何 radio** | 「開始學習」按鈕 disabled；選任一後 enable | | UX 防呆 |

## 重點觀察項目

### Worker log 應出現的 agent 順序

- **同一教材（同源多檔）**：
  ```
  start_session_v2 ... resuming=False
  v2 small_file path (per-source-split) sources=N
  ContentSplitter / SplitterVerifier x N（不交叉 Outline）
  reduce_done (small_file_path=True)
  knowledge_map / composer_done
  ```
- **不同教材**：
  ```
  start_session_v2 ...
  v2 outline done cases=... titles=...    ← ContentOutline 跑了
  v2 small_file path (per-source-split)
  ContentSplitter / SplitterVerifier x N
  reduce_done
  knowledge_map / composer_done
  ```

### 不應再出現的 log（已刪除）

- `MacroRegionPlannerAgent`
- `GlobalCurriculumReducerAgent` / `Reducer Step B/C`
- `Plan B` / `plan_b_active`
- `_start_session_v1` / V1 fallback

### 不應再出現的 env 變數

執行任何情境後，跑：
```powershell
docker compose logs curriculum-worker | Select-String "CURRICULUM_PIPELINE_V2|SMALL_FILE_CHUNK_THRESHOLD|MACRO_REGION_USE_LLM|CURRICULUM_V2_PLAN_B|REDUCER_FAIL_MODE"
```
應為空。

## 已知限制

- EPUB 章節數 > 50 不擋（plan §4.5 設計）。前端有 `console.warn` 提示但不阻斷。
- 單檔 chunks > 50 仍走「該檔一次 Splitter」。若 LLM context 不足會 Splitter 失敗 → `quality_warnings.splitter_verifier_failed`。
- 舊 V1 進行中 session（DB 殘留）不可恢復，使用者需重啟新 session。

## 完成簽核

- [ ] 全部 8 個情境 PASS
- [ ] Worker log 無 V1 / 大檔 / Plan B 字樣
- [ ] 無 Console / Network error
- [ ] 簽核日期：______
