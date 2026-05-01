from fastapi import APIRouter, HTTPException, Query
from ..auth.utils import decode_token_active
from ..memory import longterm_memory

router = APIRouter(prefix="/learner", tags=["learner"])


@router.get("/stats")
async def get_learner_stats(token: str = Query(...)):
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")

    user_id: str = payload["sub"]
    concepts = await longterm_memory.get_all_concept_mastery(user_id)

    misconceptions = []
    for c in concepts:
        for p in c["confusion_patterns"]:
            if isinstance(p, dict):
                misconceptions.append({
                    "concept_name": c["concept_name"],
                    "pattern": p.get("pattern", ""),
                    "severity": p.get("severity", "medium"),
                    "repair_strategy": p.get("repair_strategy", ""),
                })
            elif isinstance(p, str) and p:
                misconceptions.append({
                    "concept_name": c["concept_name"],
                    "pattern": p,
                    "severity": "medium",
                    "repair_strategy": "",
                })

    return {
        "concepts": [
            {
                "concept_name": c["concept_name"],
                "mastery_score": c["mastery_score"],
                "total_exposures": c["total_exposures"],
                "last_tested": c["last_tested"],
            }
            for c in concepts
        ],
        "misconceptions": misconceptions,
        "weak_count": sum(1 for c in concepts if c["mastery_score"] < 0.6),
    }
