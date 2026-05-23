"""GlobalCurriculumReducer thresholds (plan v2 contract)."""

# Step A — rule merge
RULE_MERGE_TG_SIM = 0.9
RULE_MERGE_KC_THRESHOLD = 0.85
UNSURE_TG_SIM = 0.75
UNSURE_TITLE_SIM = 0.8
UNSURE_KC_SCORE = 0.7
RULE_MERGED_CONFIDENCE = 0.95

# Step B / C — LLM acceptance & split fallback
MERGE_CONFIDENCE_MIN = 0.8
MAX_UNSURE_PAIRS_LLM = 20
# Reducer LLM 過度 merge 防護：合併後總 chunk 數超過此 cap 即拒絕該合併、保留 split
# (sess_live_b0fb06cd stage 7 = 45 chunks 觸發此規則)
MAX_MERGED_OUTCOME_CHUNKS = 14

# Plan B fuzzy attach (reuse Phase 3 interim dedup threshold)
FUZZY_ATTACH_THRESHOLD = 0.85

# Go/No-Go baselines (spike tests)
GO_NOGO_SAME_SOURCE_MERGE_MIN = 0.90
GO_NOGO_MULTI_SOURCE_MERGE_MIN = 0.75
GO_NOGO_SAME_SOURCE_UNSURE_MAX = 0.20
GO_NOGO_MULTI_SOURCE_UNSURE_MAX = 0.30
GO_NOGO_SPLIT_ACCURACY_MIN = 0.80
GO_NOGO_LIVE_MIN_PAIRS = 5
GO_NOGO_LIVE_MIN_NEGATIVE = 3

# Health monitoring — outcome collapse threshold
OUTCOME_RATIO_WARN = 0.5
