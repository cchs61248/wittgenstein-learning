# 補強／重教流程：體感問題與決策邏輯備忘

> 供後續產品／實作調整參考；內容對應目前程式（`learning_orchestrator.py`、`progress_manager.py`、`QuestionPanel.tsx` 等）。

---

## 1. 使用者觀察到的時間序（與實作對齊）

1. **原章節應保留、新內容進子章節**  
   - `decision == "reteach"` 或 `decision == "remediate"` 時，後端會插入新動態子章節（`kind: "reteach"` / `kind: "remediation"`）。  
   - 使用者直接進入新子章節 `run_stage`；原章節 `full_explanation`、題目與答題紀錄不覆寫。

2. **Retry 僅同章再測，不生成新文章**  
   - `decision == "retry"` 只在同一 `stage_id` 追加「第 N 次嘗試」區塊並重出題。  
   - 不插入子章節，也不重跑 `TeacherAgent` 生成全新教學文章。

3. **重教／補強皆即時分流到子節點**  
   - 動態子節點在 `_make_progress_decision` 中插入，並立即切換 `current_stage_id` 到新子章節。  
   - 不需要先 `advance` 才看到補強內容；重教與補強都會直接進入各自子章節開始講解與答題。

4. **回顧時少顯示後面幾題**（已修正一處前端）  
   - 原因：`stage_qa_histories` 已有該章 key 時曾**跳過** `GET .../qa_history`，快取早於重教後新答的紀錄。  
   - 修正：回顧「已完成」章節時**一律向後端拉最新答題紀錄**（`QuestionPanel.tsx`）。

---

## 2. 相關程式位置（速查）

| 議題 | 檔案／區塊 |
|------|------------|
| 重教：插入子章節並切入 `run_stage` | `backend/orchestrator/learning_orchestrator.py` → `elif d == "reteach"` |
| 進度決策（advance / retry / reteach / remediate） | `backend/agents/progress_manager.py` → `ProgressManagerAgent.run` |
| 插入補強子節點 | `learning_orchestrator.py` → `_insert_remediation_stage`；`_make_progress_decision` 內 `d in ("remediate", "reteach")` 且 `focus` 非空 |
| Retry 同章再測持久化格式 | `learning_orchestrator.py` → `elif d == "retry"` 中 `store_stage_explanation(_pack_persisted_explanation(...))` |
| 回顧答題強制拉 API | `frontend/src/components/QuestionPanel.tsx`（`fetchStageQaHistory`） |

---

## 3. 目標產品行為（優化方向）

### 3.1 核心原則

- **重教與補強解綁**：兩者是獨立流程，不共用「插入補強節點」邏輯，也不把 `reteach` 視為 `remediate` 的前置或變體。
- **所有生成內容都插入新子章節**：重教、補強、再次重教、再次補強都建立全新的動態子章節；不得覆寫原章節的 `full_explanation`、題目、答題紀錄或完成狀態。
- **原章節完整保留**：原本章節仍可回顧，包含初版講解、原題目、原答題狀況與原本進度資料。重教／補強只是在原章節後方插入子章節，作為新的學習路徑。
- **使用者直接進入新子章節**：觸發重教或補強後，系統應立即將目前學習節點切到新插入的子章節，重新開始講解與答題；前端不再對原章節送 `explanation_reset`。

### 3.2 觸發分類

觸發判斷採「真正掌握才前進，有弱點就補強，根本誤解就重教」：

| 使用者狀態 | 目標決策 | 說明 |
|------------|----------|------|
| 完全掌握（best ≥ 0.75 且無混淆概念） | `advance` | 真正掌握才進入下一個章節。 |
| 分數達標（≥ 0.75）但仍有混淆概念 | `remediate` | 表面過關，針對弱點補強後再前進。 |
| 只有少數局部概念未掌握，分數未達標 | `remediate` | 觸發補強流程，插入補強子章節。 |
| 多數題目、核心概念或整體章節結構未掌握 | `reteach` | 觸發重教流程，插入重教子章節。 |
| 出現 high severity misconception 或同一錯誤模式反覆出現 | 升級為 `reteach` | 即使錯題數不多，也代表原本理解框架可能錯位。 |

### 3.2.1 Retry（同章再測）策略（2026-05-06 更新）

`retry` 只代表「主章節內低成本再測一次」，避免與重教／補強重疊：

**允許條件（同時成立）：**
- `mastery == "partial"` 且 `attempts < max_attempts` 且 `best_score >= 0.5`
- 無 `high_severity`、無 `repeated_patterns`

**特例：首次全錯（mastery == "none"）允許一次 retry：**
- `mastery == "none"` 且 `attempts == 1` → `retry`（給一次補救機會）
- `mastery == "none"` 且 `attempts > 1` → `reteach`（確認無法靠自己翻轉，插重教子章節）

**明確不屬於 retry：**
- `mastery == "none"` 且 `attempts > 1` → `reteach`
- `partial` 且已達 retry 上限且仍有弱點 → `remediate`
- 動態子章節（`reteach` / `remediation`）內部不使用 retry

### 3.3 重教流程

1. 判定使用者對整體章節內容學不好時，插入一個新的「重教子章節」。
2. 重教子章節沿用原章節的 source truth 與核心概念，但用新的教學框架重新組織內容。
3. 使用者直接進入重教子章節，開始學習與答題。
4. 重教子章節答題完成後：
   - **完全掌握**：進入下一個章節。
   - **部分掌握、部分未掌握**：進入補強流程，針對未掌握內容插入補強子章節。
   - **完全未掌握**：再次觸發重教流程，再插入一個新的重教子章節。
5. 同一個原章節的重教流程最多觸發 **2 次**。達上限後不得繼續重教，應依剩餘弱點轉補強或由產品定義人工／提示介入策略。

### 3.4 補強流程

1. 判定使用者只有局部內容學不好時，插入一個新的「補強子章節」。
2. 補強子章節只聚焦未掌握概念或錯誤模式，不改寫原章節。
3. 使用者直接進入補強子章節，開始學習與答題。
4. 補強子章節答題完成後：
   - **完全掌握**：進入下一個章節。
   - **部分掌握、部分未掌握**：只針對仍未掌握的部分，再插入新的補強子章節。
   - **完全未掌握**：再生成一筆新的補強內容，插入新的補強子章節。
5. 同一個原章節的補強流程最多觸發 **2 次**。達上限後不得繼續插入補強子章節，應進入下一章或交由產品定義後續協助。

---

## 4. 實際狀態轉移（已實作）

以下為目前程式實際運作的決策模型（2026-05-06 更新）。

### 4.1 主章節答題後

| 結果 | 下一步 |
|------|--------|
| 完全掌握（best ≥ 0.75 且無混淆概念） | 進入下一個待學章節（依弱點/掌握度加權排序）。 |
| 分數達標（≥ 0.75）但仍有混淆概念 | 插入補強子章節，針對混淆概念強化。 |
| 局部未掌握（partial） | 先 retry（若有機會），次數用完後插入補強子章節。 |
| 整體未掌握（none），首次嘗試 | retry 一次（給補救機會）。 |
| 整體未掌握（none），非首次 | 插入重教子章節，換框架重新說明。 |
| 出現 high severity 或 repeated_patterns | 升級為插入重教子章節（優先於 retry 和 remediate）。 |

### 4.2 重教子章節答題後

| 結果 | 下一步 |
|------|--------|
| 完全掌握 | 進入下一個章節。 |
| high_severity 且 source_reteach_count < 2 | 再插入新的重教子章節（升級）。 |
| mastery == "none" 且 source_reteach_count < 2 | 再插入新的重教子章節。 |
| mastery == "none" 且重教已達 2 次，補強未達上限 | 改插入補強子章節。 |
| mastery == "partial"（部分掌握） | 插入補強子章節，只補仍未掌握內容。 |
| 雙上限皆滿（重教 ≥ 2、補強 ≥ 2） | 強制 advance，後續可回顧。 |

### 4.3 補強子章節答題後

| 結果 | 下一步 |
|------|--------|
| 完全掌握 | 進入下一個章節。 |
| high_severity 且 source_reteach_count < 2 | 升級為插入重教子章節。 |
| 未完全掌握，source_remediation_count < 2 | 再插入新的補強子章節。 |
| 補強已達 2 次上限 | 強制 advance，後續可回顧。 |

### 4.4 整合挑戰節點（enrichment）答題後

| 結果 | 下一步 |
|------|--------|
| 任何結果 | 視為課程完成，不觸發任何子章節。 |

> enrichment 是所有主章節完成後才觸發的加分節點，答完即結束課程。

### 4.5 不變條件

- 子章節記錄 `source_stage_id`，重教/補強次數以原章節為統計單位（`_count_child_stages` 計算）。
- `is_child_stage` 只判斷 `kind in {"reteach", "remediation"}`，enrichment 不落入子章節邏輯。
- 插入新子章節後更新 `stages_json` 與 `stage_progress`，原章節的講解、題目與答題紀錄不修改。
- 最壞情況：主章節 → T.1.1 → T.1.2 → R.1.1 → R.1.2 → advance，共 4 個子章節後必定前進。

---

## 5. 實作狀態

### 已完成

- [x] `_insert_reteach_stage` 與 `_insert_remediation_stage` 分開實作，只新增 stage，不覆寫來源 stage。
- [x] `ProgressManagerAgent.run`：`is_child_stage` 改為只看 `kind in {"reteach","remediation"}`，不再用 `is_dynamic` 判斷。
- [x] `source_reteach_count` 與 `source_remediation_count` 由 `_count_child_stages()` 統計，計數範圍包含所有衍生子章節。
- [x] orchestrator 的 `reteach` 分支：插入重教子章節並 `run_stage` 新節點，不再送 `explanation_reset` 覆蓋原畫面。
- [x] orchestrator 的 `remediate` 分支：插入補強子章節並 `run_stage` 新節點，不再附加在原章節尾端。
- [x] **advance bug 修正**：advance 條件改為 `mastery == "complete"`（best ≥ 0.75 且無混淆概念），不再只看 `best_score`。
- [x] **高分有弱點**：`best_score >= 0.75 AND unique_confused` → remediate，先補強再前進。
- [x] **死碼清除**：移除從未被遞增的 `remediate_count >= max_remediation` 條件。
- [x] **首次全錯 retry**：`mastery == "none" AND attempts == 1` → retry，給一次補救機會後再判斷是否重教。
- [x] **子章節 high_severity 升級**：子章節中出現 high severity misconception 且 `source_reteach_count < 2` → reteach。
- [x] **enrichment 保護**：`is_child_stage` 不含 enrichment；orchestrator 的 reteach/remediate 分支若 stage.kind == "enrichment" 強制轉為 advance，不插子章節。

### 待完成

- [x] 前端章節列表與回顧顯示：子章節要可被獨立進入、獨立答題、獨立回顧，同時保留與原章節的關聯。（`StageMap.tsx` 以 `source_stage_id` 分組，子章節縮排顯示於父章節下方，附類型 badge）
- [ ] 補測試：至少覆蓋「主章節 → 重教」、「重教 → 補強」、「重教最多 2 次」、「補強最多 2 次」、「高分有弱點 → 補強」、「首次全錯 → retry」、「enrichment 不觸發子章節」。

---

*文件建立日期：2026-05-04*
