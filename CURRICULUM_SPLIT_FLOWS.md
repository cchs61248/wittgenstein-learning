# 教材切分流程說明（白話版）

> 最後更新：2026-05-28（**P4 排序層擴充**：`enforce_stage_ordering` 認「（續 N）」follow_up_orphan 強制緊隨 base stage；新 `merge_singleton_chunk_stages` 中間 1-chunk stage 自動併入鄰近；`_summary_kc_from_title` fallback 把「：」優先級提到「與」之前，避免「X：Y 與 Z」被誤切成「X：Y 的前 8 字」）
> 2026-05-28（P3 stage 排序硬約束：consolidator prompt 加 rule F + payload 帶 first_chunk_id；新 `enforce_stage_ordering` 程式層在 consolidator 前後各跑一次，強制（一）→（二）順序 + 按閱讀順序）
> 2026-05-27（P0/P1/P2 品質改善：Outline 觸發加 sources>=3、加跨 source jaccard 合併 + LLM Stage Consolidator、Splitter title hard rules、EPUB chapter hint、Verifier 鬆綁、orphan attach 改善）
> 統一架構 spec：`docs/superpowers/specs/2026-05-27-curriculum-unify-v2-design.md`
> 技術細節請對照 `BACKEND_FLOW.md`；本文件只講「走哪條路、做了什麼」。

---

## 一、全流程：所有上傳都一樣的事

前端按「開始學習」後，後端做：

1. **收 sources**（檔案 / URL / 純文字 / EPUB 章節）
2. **程式切 chunk**
   - 每個來源各自切段落
   - 全域編號 `chunk_0000`、`chunk_0001`…
   - 每個 chunk 標記來源 (`source_label`、`source_index`、`source_id`)
3. **寫入 DB**（session stub、source_chunks、`same_material` 欄位）
4. 前端看到 **「分析教材中」**（`session_generating`）
5. 丟 **Arq job** 到 Redis（`CURRICULUM_USE_ARQ=1` 時，由 Docker worker 跑；否則 uvicorn in-process）

---

## 二、唯一的切分路線

| 條件 | 動作 |
|------|------|
| `n_sources == 1` | **single_split**：整包 chunk 一次 Splitter + Verifier |
| `n_sources > 1` | **per_source_split**：每個來源各跑一次 Splitter + Verifier |

兩條路徑都跳過 `MacroRegionPlanner / GlobalCurriculumReducer / Plan B`（已刪）。

是否跑 `ContentOutline` 由前端「是否同一教材」決定，見 §三。

---

## 三、ContentOutline 何時跑（看 `same_material` + sources 數）

前端 UploadModal 在 ≥ 2 個 source 時強制使用者選 radio。實際觸發條件：

| 條件（任一成立即跑 Outline） | 例子 |
|---|---|
| `same_material is False` | 多個獨立主題（前端勾「不同教材」） |
| `n_sources >= 3` | EPUB 多章節（即使勾「同一教材」），多份檔案拼湊 |

**只有「1~2 個 source + same_material=True」會跳過 Outline**（單檔或雙檔同教材，省 LLM）。

跑 Outline 時，產物 `required_outline = {named_cases, required_stage_titles, must_cover_topics}` 會餵給接下來的逐檔 Splitter 當骨架提示。

P0a 改善後（2026-05-27）：原本「同教材一律跳 Outline」的設計在長 EPUB（8 章 100+ chunks）反而傷品質——8 個 Splitter 各自編 prefix，跨章命名失調。新規則確保長教材一定有全局骨架。

---

## 四、實際範例：五個法則 .txt + 勾「不同教材」

```
[uvicorn 收 start_session]
  → 組 chunks（5 sources × 平均 1-3 chunks = ~7 chunks 全域編號）
  → DB 寫 same_material=False
  → 丟 Arq job

[worker]
  → ContentOutline（LLM × 1）：看完 7 chunks 抽骨架
  → for source in [法則1, 法則2, 法則3, 法則4, 法則5]:
       ContentSplitter（LLM）        ← 用 outline_hint 切該檔
       SplitterVerifier（LLM）       ← 檢查該檔有沒漏 chunk
       reroll 一次（如需）
  → 攤平候選 → merge_duplicate_topic_stages(threshold=env)
  → finalize_small_file_stages（程式收尾）
  → 條件式 ConceptCanonicalize（LLM × 1，預設關）
  → 推 knowledge_map → pending_confirmation
```

**LLM 次數估計**：1 (Outline) + 5 × 2 (Splitter + Verifier) = ~11 次

## 五、實際範例：五個法則 .txt + 勾「同一教材」

跟 §四 一樣，但跳過 ContentOutline：

```
[worker]
  → （跳過 ContentOutline）
  → for source in [...]:
       ContentSplitter / SplitterVerifier
  → 合併 / finalize
```

**LLM 次數估計**：5 × 2 = 10 次

---

## 六、EPUB 上傳的特殊處理

EPUB 在 **upload 階段**就被切成多章節，不會以單一檔進到切分流程：

```
[POST /upload  file=book.epub]
  → split_epub_by_toc(raw) → [(章節標題, 純文字), ...]
  → 每章獨立寫成 .txt 上傳（file_id_001, file_id_002, ...）
  → 回傳 {epub_chapters: [...], total_chapters: N, parent_filename: "book.epub"}

[前端 UploadModal]
  → 偵測 epub_chapters → 把單一拖曳變成 N 個 source items（每行可單獨 ✕ 移除）
  → 使用者刪不想要的章節後，按「開始學習」
  → N 個章節進入切分流程（per_source_split）
```

EPUB 沒有 TOC 時，`split_epub_by_toc` fallback 為「每個 spine document 一章」。0 章 → 422。

---

## 七、合併 stage：程式 + 可選 LLM

切分流程在 per-source split 攤平後跑「三層合併」：

| 層 | 觸發條件 | 工具 | 合併依據 |
|---|---|---|---|
| 1. 標題去重 | 一律跑 | 程式 `merge_duplicate_topic_stages` | 標題字面相似度 ≥ `STAGE_TITLE_MERGE_THRESHOLD`（預設 0.85） |
| 2. 跨 source 概念合併 | 一律跑（P0b-1，2026-05-27 加） | 程式 `merge_by_concept_overlap` | key_concepts jaccard ≥ `STAGE_CONCEPT_OVERLAP_THRESHOLD`（預設 0.6） |
| 3a. 程式硬排序 | 一律跑（P3b，2026-05-28 加） | 程式 `enforce_stage_ordering` | 按 min chunk_id 升序 + 「（一）（二）」group 強制連續按編號 + **「（續 N）」follow_up_orphan 強制緊隨 base stage（P4a）** |
| 3b. LLM 全局協調 | `chunks_total >= 30`（P0b-2，2026-05-27 加） | LLM `StageConsolidatorAgent` | 語意理解：跨章 rename + reorder + 同類合併 |
| 3c. 再次硬排序 + 單 chunk 合併 | LLM consolidator 跑完後（P3b + P4c） | 程式 `enforce_stage_ordering` + `merge_singleton_chunk_stages` | 順序兜底；中間 1-chunk stage 併入前一節（頭尾、`kind=follow_up_orphan/summary` 保留）|
| 收尾 | 一律跑 | 程式 `finalize_small_file_stages` / `finalize_curriculum_stages` | orphan attach、拆超大節、kc 規則 |

**設計動機**：
- 層 1（字面）抓不到「借錢外掛（一）」vs「借錢工具解析（一）」這種跨章命名漂移
- 層 2（jaccard）抓得到，但只能合「概念重疊度高」的；標題仍混亂
- 層 3a/3c（程式排序）：LLM 出來的順序常違反「（一）必先於（二）」與「按閱讀順序」，純程式兜底
- 層 3b（LLM）能做語意整理：統一 prefix、把同類 stages 排在一起、跨章合併

**Stage Consolidator 硬約束**：不可新增/移除任何 chunk_id（驗證失敗 fallback 沿用原 stages，記到 `quality_warnings.stage_consolidator_fallback`）。

閾值調整：
- `STAGE_TITLE_MERGE_THRESHOLD` 高（0.95）→ 保守、stage 多；低（0.70）→ 積極合
- `STAGE_CONCEPT_OVERLAP_THRESHOLD` 高（0.8）→ 只合幾乎同主題；低（0.4）→ 鬆散合併
- EPUB 多章節若標題有共同前綴（「第三章 — 起手式」）可能誤合，可調高 title 閾值

---

## 八、ContentOutline 是什麼

**在 Splitter 切 stage 之前，先讓 LLM 讀完全文，寫「目錄草稿」。**

- **不產 stage**，只產結構化大綱：
  - 有哪些**具名案例**要各自一節
  - **建議 stage 標題**順序
  - 哪些 chunk 一定要被 cover
- 用途：Splitter 行為不穩，容易漏切並列案例或混切（mash-up）。Outline 先定骨架，Splitter 照著切。

**何時跑**：見 §三，由 `same_material` 控制。

---

## 九、可選的後處理：ConceptCanonicalize

預設關閉。設 `CONCEPT_CANONICALIZE=1` 啟用：

- 攤平所有 stages 的 `key_concepts` → 收集所有概念名詞
- 拿過去同一 `content_hash` 的歷史 canonical pool
- 用 LLM 對齊：把不同檔出現的「同義詞」統一命名（例如「複利」「複利效應」→ 統一）
- 跨檔教材（per_source_split）特別有用

LLM 成本：1 次。

---

## 十、相關環境變數（速查）

| 變數 | 預設 | 白話 |
|------|------|------|
| `CURRICULUM_USE_ARQ` | `0` | 1=切分丟 Arq worker 跑；0=uvicorn in-process |
| `STAGE_TITLE_MERGE_THRESHOLD` | `0.85` | 標題去重合併閾值（0~1），高=保守、低=積極 |
| `STAGE_CONCEPT_OVERLAP_THRESHOLD` | `0.6` | 跨 source 用 key_concepts jaccard 合併閾值（0~1）|
| `SPLITTER_VERIFIER_MIN_MISSES` | `2` | Splitter 觸發 reroll 的最少 missing 數，missing ≤ 1 直接 soft-pass |
| `CONCEPT_CANONICALIZE` | `0` | 1=合併後跑 LLM 統一關鍵詞命名 |
| `SPLITTER_FAIL_MODE` | `hard` | verifier 失敗：hard=拒絕，soft=放行+寫 `quality_warnings` |
| `LLM_CACHE_ENABLED` | `0` | 1=啟用 curriculum LLM 結果 cache |

**已刪除**（從 2026-05-27 unify-v2-small-file-pipeline 起不存在）：

- `CURRICULUM_PIPELINE_V2`
- `SMALL_FILE_CHUNK_THRESHOLD`
- `SMALL_FILE_FORCE_OUTLINE`
- `MACRO_REGION_USE_LLM`
- `CURRICULUM_V2_PLAN_B`
- `CURRICULUM_V2_PLAN_B_AUTO`
- `REDUCER_FAIL_MODE`

---

## 十一、除錯時看哪裡

| 現象 | 可能原因 | 看哪 |
|------|----------|------|
| 按開始後完全沒動 | uvicorn 缺 `arq`、WS crash | uvicorn terminal traceback |
| 有 `session_generating` 但 worker 0 job | Redis / worker 沒起 | `docker compose logs curriculum-worker` |
| 切完沒地圖 | prepare 或 worker 例外 | worker log、DB `sessions` 狀態 |
| Windows DB disk I/O | WAL + bind-mount | `SQLITE_JOURNAL_MODE=DELETE` |
| 教材該合的沒合 / 不該合的合了 | 閾值不對 | 調 `STAGE_TITLE_MERGE_THRESHOLD` |
| 多檔關鍵詞一直分歧 | canonicalize 沒開 | 設 `CONCEPT_CANONICALIZE=1` |

---

## 十二、Resume 流程

中斷的 session（worker 重啟、API 重啟）可從 checkpoint 恢復：

1. 從 DB session row 讀 `same_material`（NULL = legacy → 預設 True）
2. 讀 `curriculum_checkpoints` 看哪些區塊已完成
3. `single_split` / `per_source_split` 已完成 → skip 整段切分，直接 finalize + 推 knowledge_map
4. 沒切分過 → 從頭跑

---

## 十三、一句話記流程

> **N 個 sources → 程式切 chunk → （same_material=False 或 sources≥3 時跑 Outline）→ 逐檔 Splitter（帶 chapter hint）+Verifier（missing≤1 soft-pass）→ 程式 title 合併 → 程式 jaccard 合併 → 程式硬排序 →（chunks≥30 跑 LLM Consolidator → 程式硬排序兜底）→ finalize → （可選 Canonicalize）→ 推 knowledge map**

只此一條，沒有大檔、沒有 V1、沒有 Plan B、沒有 Reducer。
