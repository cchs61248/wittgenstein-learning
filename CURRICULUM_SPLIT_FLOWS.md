# 教材切分流程說明（白話版）

> 2026-06-02（**Warn-only 品質偵測器家族**）：在切分收尾段新增一族「只記不改」的偵測器（純程式、無 LLM、不動 stage、不改路由）——`empty_curriculum`（切出空課綱）、`large_single_source_risk`（單檔太大、一次丟 splitter 無分批）、`generic_kc_collapse`（關鍵詞退化成傘狀詞）、`medium_cross_material_gap`（中型跨教材落在沒人整理的夾縫）。命中只寫 `quality_warnings` + log，**永遠不阻斷或改變切分結果**。同批還把「單 stage chunk 上限（14）」改為**無條件強制**（修 consolidator 合併/fold 產生的超量 stage 繞過 cap）。詳見 §九-A。
> 2026-06-01（**Phase 4 跨教材教學循序排序，COMPLETE**）：新增 `PedagogicalPlannerAgent` + 確定性 plan applier，把多本不同教材排成「概論→基礎→核心→進階→應用→總結」的循序課綱。**預設 off**（env `CROSS_MATERIAL_PEDAGOGICAL_PLANNER`），flag-off 與現況 bit-for-bit 等價。只在 `same_material=False` 且過 activation gate（chunks≥30、stages≥6、sources≥3、有重排建議、無循環）時呼叫 LLM；LLM 只提「move plan」，程式驗證覆蓋/不增不減後才套用，任何失敗安全 fallback 回原序。**接線點在 `finalize_curriculum_stages` 之後**（成為最後一個動 stage 順序者，避免被 finalize 的閱讀序 sort 蓋掉）。詳見 §七-A。
> 最後更新：2026-05-30（**runtime 校正**：`SPLITTER_FAIL_MODE` 已非阻塞、只切 warning 通道不再中止；reducer agent 已刪但 `global_curriculum_reducer` / `macro_region_refiner` prompt 仍留存且非主線（`reducer_skipped=True`）；新增 same_material-only 的 `cleanup_orphan_enumerator_titles` 孤兒序號標題清理；`CONCEPT_CANONICALIZE` 預設 0、canonicalize 非主線）
> 2026-05-29（**Phase 1–3 mode-aware 後處理**：新增 `choose_postprocess_mode`，單 source / 同教材跳過所有語意合併（`allow_merge=False`）；新增 `SourceOrderResolver` 確定性章序；**Phase 3 收斂 Outline 觸發為 `run_outline = not same_material`——同教材一律不跑 Outline**（含 ≥3 章），因 global outline 的跨章 named_cases 會破壞章節邊界。詳見 `docs/implementation_status.md`）
> 2026-05-28（**P4 排序層擴充**：`enforce_stage_ordering` 認「（續 N）」follow_up_orphan 強制緊隨 base stage；新 `merge_singleton_chunk_stages` 中間 1-chunk stage 自動併入鄰近；`_summary_kc_from_title` fallback 把「：」優先級提到「與」之前，避免「X：Y 與 Z」被誤切成「X：Y 的前 8 字」）
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

兩條路徑都跳過 reducer planning：`candidates_to_stages_flat` 直接攤平候選，`quality_warnings = {small_file_path: True, reducer_skipped: True}`。舊的 `MacroRegionPlannerAgent` / `GlobalCurriculumReducer` agent 與 Plan B 已刪；但 `global_curriculum_reducer` / `macro_region_refiner` 兩個 **prompt 仍留在 `prompt_templates.py`**（legacy、非主線、現行 unified path 不呼叫）。看到 `reducer_skipped=True`、沒有 reducer log 是正常的。

是否跑 `ContentOutline` 由前端「是否同一教材」決定，見 §三。

---

## 三、ContentOutline 何時跑（只看 `same_material`）

前端 UploadModal 在 ≥ 2 個 source 時強制使用者選 radio。**Phase 3（2026-05-29）起規則收斂為單一判斷**：

```python
run_outline = not same_material
```

| 條件 | 跑 Outline？ | 例子 |
|---|---|---|
| `same_material is False` | ✅ 跑 | 多個獨立主題（前端勾「不同教材」） |
| `same_material is True` | ❌ 一律不跑（含 ≥3 章 EPUB） | 同一本書多章、單檔、雙檔同教材 |

跑 Outline 時，產物 `required_outline = {named_cases, required_stage_titles, must_cover_topics}` 會餵給接下來的逐檔 Splitter 當骨架提示。

**為什麼砍掉舊的「`n_sources >= 3` 也跑」規則（重要，非漏做）**：P0a（2026-05-27）曾讓同教材 ≥3 章也跑 Outline，想補長 EPUB 的全局骨架。但 live 8 章案例（`sess_f9qt8rac9`）證明：global outline 把全部 chunk 的同主題內容歸成跨章 `named_cases`，per-source splitter 共用後，**不同章的同主題 chunk 被併進同一 stage**（例：7.1 = 第 6 章 + 第 8 章）。對同教材而言 outline 不是排序提示，而是**章節邊界破壞器**。章節順序改由確定性的 `SourceOrderResolver` 處理（見 §七與 `docs/implementation_status.md`）。修後 live `sess_hpgx5vcyi`（8 章 / 148 chunks）跨章 stage = 0 / 29。

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

跟 §四 一樣，但**永遠跳過 ContentOutline**（不管幾個 source），且後處理走 `same_material_coordinate_only` 模式——`allow_merge=False`，不跑 jaccard / consolidator，只做確定性排序（`SourceOrderResolver` + `enforce_stage_ordering`）+ 收尾：

```
[worker]
  → （跳過 ContentOutline；run_outline = not same_material = False）
  → SourceOrderResolver 定章序
  → for source in [...]:
       ContentSplitter / SplitterVerifier
  → 標題去重 + 確定性排序 + finalize（不跑語意合併）
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

切分流程在 per-source split 攤平後跑後處理。**Phase 1（2026-05-29）起為 mode-aware**：`choose_postprocess_mode(n_sources, same_material)` 決定 `allow_merge`，只有 `cross_material_merge_and_coordinate`（多本不同書）才跑語意合併層；單 source 與同教材只跑確定性排序 + 收尾，保留 splitter 切出的 stage 邊界。

| 層 | 觸發條件 | 工具 | 合併依據 |
|---|---|---|---|
| 1. 標題去重 | 一律跑 | 程式 `merge_duplicate_topic_stages` | 標題字面相似度 ≥ `STAGE_TITLE_MERGE_THRESHOLD`（預設 0.85） |
| 2. 跨 source 概念合併 | **僅 `allow_merge`**（跨教材；P0b-1） | 程式 `merge_by_concept_overlap` | key_concepts jaccard ≥ `STAGE_CONCEPT_OVERLAP_THRESHOLD`（預設 0.6） |
| 3a. 程式硬排序 | 一律跑（P3b） | 程式 `enforce_stage_ordering` | 按 min chunk_id 升序 + 「（一）（二）」group 強制連續按編號 + **「（續 N）」follow_up_orphan 強制緊隨 base stage（P4a）** |
| 3b. LLM 全局協調 | **僅 `allow_merge` 且** `chunks_total >= 30`（P0b-2；legacy，Phase 4 將改 plan-based） | LLM `StageConsolidatorAgent` | 語意理解：跨章 rename + reorder + 同類合併 |
| 3c. 再次硬排序 + 單 chunk 合併 | 一律跑（P3b + P4c） | 程式 `enforce_stage_ordering` + `merge_singleton_chunk_stages` | 順序兜底；中間 1-chunk stage 併入前一節（頭尾、`kind=follow_up_orphan/summary` 保留）|
| 收尾 | 一律跑 | 程式 `finalize_small_file_stages` / `finalize_curriculum_stages` | orphan attach、拆超大節、kc 規則。**單 stage chunk 上限（14）無條件強制**（2026-06-02 T-STAGE-CAP）：compact 與 deterministic cleanup 兩條路徑都跑 `split_oversized_stages`，修掉「consolidator 合併 / interior fold 產生、不留 orphan 的超量 stage 繞過 cap」（live `exmiz273r` stage9=24、`hi7ob3ydm` stage4=18）；超量者拆成「（續 N）」`follow_up_orphan` |
| 孤兒序號標題清理 | 一律跑（**僅 `same_material=True`**） | 程式 `cleanup_orphan_enumerator_titles` | finalize 匯流點（compact / 非 compact 兩分支之後）移除無 sibling 的孤兒序號標題（如「模式二：X」但全課綱無「模式一」、「主題：（二）X」無「（一）」）；**title-only、確定性、不 relabel、不動 chunk / kc**。寫 `quality_warnings.title_cleanup_removed_orphan_enumerators` + log `v2 title cleanup removed orphan enumerators count=N`。本來就沒孤兒序號 → count=0、無 log 屬正常 |

> **同教材排序靠誰？** 既然同教材不跑層 2/3b，章節順序由 Phase 2 的 `SourceOrderResolver`（確定性，依 EPUB TOC / 檔名章節號 / source_index）+ 層 3a 的 `enforce_stage_ordering` 共同保證，不依賴 LLM。文件設計的 `SameMaterialCoordinatorAgent`（LLM coordinator）刻意未實作——見 `docs/implementation_status.md`。

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

## 七-A、Phase 4：跨教材教學循序排序（flag-gated，預設 off）

§七 的合併層解決「同主題該不該合」，但**不解決「讀的順序對不對」**。Live `sess_07oumwek0`（21 來源 / 73 chunks）證明：即使 consolidator 跑了，最終 stage 序仍大致沿用上傳/閱讀順序——概論排在進階之後、總結卡在中段。Phase 4 補的就是這層「pedagogical progression」。

**原則：LLM 提案、程式套用。** LLM 只輸出 stage 的搬移計畫（`{moves, rationale}`），不重寫內容、不增刪 chunk；程式驗證覆蓋/不增不減/不重複後才套用，任一環失敗安全 fallback 回原序，絕不中止 build。符合「確定性/保守優先」原則。

**啟用條件（全部成立才呼叫 LLM）**：
```
flag CROSS_MATERIAL_PEDAGOGICAL_PLANNER on（預設 off）
same_material is False（只跨教材）
chunks >= 30   stages >= 6   sources >= 3
deterministic ordering plan 有重排建議（order_changed）
prerequisite graph 無循環
```
未過閘 → 只記 diagnostics（不重排、不呼叫 LLM）；flag off → 完全不跑、不寫 warning、與現況 bit-for-bit 等價。

**流程**（皆在 §七 收尾 + `finalize_curriculum_stages` **之後**）：
```
build_stage_cards          # 每 stage 標 role(overview/foundation/core/advanced/application/summary/...)+ difficulty
build_prerequisite_graph   # 關鍵字種子 + role nearest-prior 邊；偵測循環/孤立群
build_ordering_plan        # 確定性 Kahn 拓樸推薦序（純診斷）
→ gate 過 → PedagogicalPlannerAgent.propose_plan  # LLM 出 move plan
→ apply_pedagogical_plan + 3 verifier（coverage / stage-id set / content）
→ applied → 重新 renumber stage_id/node_id（沿用 finalize 的 chapter.section 慣例）
```

**為什麼接在 finalize 之後**：`finalize_curriculum_stages` 第一步 `sort_stages_by_chunk_order` 會依 chunk 閱讀序重排。早期版本（T4c）把 planner 放在 finalize **前**，重排當場被蓋掉（live `sess_p89iebyfw` 證實）。T4e 把 seam 移到 finalize **後**，planner 成為最後一個動順序者，重排才會真正寫進 persisted `stages_json`。

**Live 驗收（`sess_r14gdzg7x`，PASS）**：8 stages / 73 chunks → `planner_mode=applied`、4 moves；persisted 序 = planner applied 序；before（閱讀序 advanced 排#1、summary 卡#6）→ after（foundation→core→advanced→application→**summary 最後**）。

**observability**：寫 `quality_warnings.cross_material_pedagogical_planner`（schema v1：`planner_mode ∈ {diagnostics_only|applied|fallback|error_fallback}`、`run_id`、`gate_reasons`、`stage_order_before/after`、`plan_moves_redacted`（只 stage_id/after_stage_id，**不存 raw LLM 文字/rationale**）、各層 diagnostics、applied 時 `renumbered_after_apply`）。log：`v2 pedagogical planner applied|fallback|diagnostics_only|error_fallback session=.. run_id=..`。

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

## 九-A、品質警示：只記不改的偵測器（warn-only）

切分收尾時跑一族**純程式、確定性、不呼叫 LLM、不改 stage、不改路由**的偵測器。命中時只做兩件事：寫一筆 `quality_warnings.<名稱>`、印一行 `v2 quality warning <名稱> ...` log。**永遠不會阻斷切分、不會改變課綱結果**——它們是「儀表板」而非「煞車」。設計上偏保守（寧可漏報不要誤報），閾值與白名單等遇到實際案例再放寬。

| 警示 | 白話：在偵測什麼 | 什麼時候亮 |
|------|----------------|-----------|
| `empty_curriculum` | 切到最後一個 stage 都沒有，準備存一份「空課綱」 | persist 前 `stages` 為空。補既有 `zero_stages` 監控的盲區：splitter 回 0 個候選時，候選數和 stage 數一起變 0，舊監控（要「候選數>0」才報）抓不到，會默默存空課綱 |
| `large_single_source_risk` | **單一**檔案太大，整包 chunk 一次丟進 splitter（沒有分批、沒有上限），可能被 LLM 輸出長度截斷、甚至切出空課綱 | 單 source 且 chunk 數 ≥ 50。嚴重度看預估輸出 token vs provider 上限：超過上限或 ≥150 chunk = `high_risk`、≥0.8 倍或 ≥100 = `risk`、其餘 = `observe`。分批切分是後續工作，現在先標風險 |
| `generic_kc_collapse` | splitter 把具體關鍵詞退化成「概念 / 內容 / 重點 / 概述」這類**傘狀空詞** | 跨 stage 看比例：單一 stage 裡傘狀詞 ≥2 個且占比 ≥50%（規則 A），或整份課綱傘狀詞占比 ≥30%（規則 B）。用固定白名單精準比對，follow-up 補充節不算 |
| `medium_cross_material_gap` | 多本不同教材，但總量太小，落在「沒人幫它跨教材整理」的夾縫 | `same_material=False` 且來源 ≥3 本 且 chunk <30。因為 chunk<30，跨教材的 LLM Consolidator（要 ≥30）和 Phase 4 教學排序（也要 ≥30）都不會跑，多本來源從沒被整合。會附帶「標題正規化後重複的主題群組」當線索 |

> 另有一組「單一 stage 內」的關鍵詞衛生稽核（`meta_only_key_concepts` / `malformed_key_concept`），跟上面「跨 stage」的偵測器不同。2026-06-02 把「章節補充 / 補充說明 / 補充內容」也列入 meta filler 黑名單（精確比對；「補充保費」「營養補充品」這種真概念不會誤判），與原本的「章節總結」同類。

---

## 十、相關環境變數（速查）

| 變數 | 預設 | 白話 |
|------|------|------|
| `CURRICULUM_USE_ARQ` | `0` | 1=切分丟 Arq worker 跑；0=uvicorn in-process |
| `STAGE_TITLE_MERGE_THRESHOLD` | `0.85` | 標題去重合併閾值（0~1），高=保守、低=積極 |
| `STAGE_CONCEPT_OVERLAP_THRESHOLD` | `0.6` | 跨 source 用 key_concepts jaccard 合併閾值（0~1）|
| `SPLITTER_VERIFIER_MIN_MISSES` | `2` | Splitter 觸發 reroll 的最少 missing 數，missing ≤ 1 直接 soft-pass |
| `CONCEPT_CANONICALIZE` | `0` | 1=合併後跑 LLM 統一關鍵詞命名 |
| `CROSS_MATERIAL_PEDAGOGICAL_PLANNER` | `0`（off） | 1=啟用 Phase 4 跨教材教學循序排序（見 §七-A）。off 時 bit-for-bit 等價；on 仍需過 gate（同教材/小教材自動不跑）。接受 `1/true/yes/on` |
| `SPLITTER_FAIL_MODE` | `hard` | **已非阻塞，不再中止 session**。global verify 不對齊時只切換 warning 通道：`soft`=寫 `quality_warnings.splitter_verifier_failed`；`hard`（預設）=寫 WARNING log。兩者都繼續 fold interior orphan + follow-up 補節 + 確定性收尾。舊的 fail-hard 拒絕（`SplitterVerificationRejected` / `MAX_SPLITTER_VERIFY_RETRIES`）已移除 |
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
| 連不上 DB / 連線被拒 | PostgreSQL 沒起、`DATABASE_URL` 錯 | `docker compose ps postgres`、確認 `DATABASE_URL` |
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

> **N 個 sources → 程式切 chunk → （只有 same_material=False 才跑 Outline）→ SourceOrderResolver 定章序 → 逐檔 Splitter（帶 chapter hint）+Verifier（missing≤1 soft-pass）→ 程式 title 合併 → 程式硬排序 →（僅跨教材：jaccard 合併、chunks≥30 跑 LLM Consolidator）→ finalize →（flag-on 且過閘的跨教材：Phase 4 LLM 教學循序重排 + renumber）→ （可選 Canonicalize）→ 推 knowledge map**

只此一條，沒有大檔、沒有 V1、沒有 Plan B、沒有 Reducer。同教材保章節邊界（不跑 Outline、不跑語意合併），只有多本不同書才跨 source 合併；多本不同書且開 `CROSS_MATERIAL_PEDAGOGICAL_PLANNER` 才會進一步做教學循序重排（§七-A，預設 off）。
