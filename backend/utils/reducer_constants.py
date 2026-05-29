"""Constants still consumed by the small-file curriculum pipeline.

Originally part of the global reducer plan (V2). The reducer agent and
helpers were removed in the V2 unification; only these constants remain.
"""

# Pipeline_v2 _dedupe_candidates cap (chunks per merged candidate).
# Sourced from sess_live_049d39ce stage 2 case where cross-region merge
# produced a 45-chunk mega-stage that overloaded Teacher.
MAX_MERGED_OUTCOME_CHUNKS = 14

# curriculum_health.assess_reducer_health collapse-warning threshold.
OUTCOME_RATIO_WARN = 0.5
