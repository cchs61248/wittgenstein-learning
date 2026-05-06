# Reteach Remediation Substages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make reteach and remediation independent flows that always insert new child stages instead of overwriting or appending to the source stage.

**Architecture:** Keep stage insertion inside `LearningOrchestrator`, but split dynamic stage kinds into `reteach`, `remediation`, and existing `enrichment`. Move mastery classification into deterministic helpers in `ProgressManagerAgent` so dynamic child stages can branch after their own quiz while enforcing per-source-stage limits.

**Tech Stack:** Python 3.11+ backend with FastAPI/WebSocket orchestration, SQLite session persistence, pytest for backend tests, React/Zustand frontend with TypeScript.

---

## File Map

- Modify `backend/agents/progress_manager.py`: classify complete / partial / none mastery, remove unconditional dynamic advance, add child-stage-aware decisions and count limit inputs.
- Modify `backend/orchestrator/learning_orchestrator.py`: add `_insert_reteach_stage`, extend `_insert_remediation_stage`, count child stages by `source_stage_id`, and route `reteach/remediate` into child stages via `run_stage`.
- Modify `backend/memory/working_memory.py`: replace volatile `remediate_count` usage with stage-agnostic fields only if needed; durable counts should be derived from `wm.stages`.
- Modify `frontend/src/types/messages.ts`: expose optional `node_id`, `kind`, and `source_stage_id` on stages and decision candidates.
- Modify `frontend/src/store/sessionStore.ts`: preserve stage metadata in state.
- Modify `frontend/src/components/StageMap.tsx`: visually label independent child stages without changing navigation behavior.
- Create `backend/tests/test_progress_manager_dynamic.py`: deterministic tests for progress decisions without LLM calls.
- Create `backend/tests/test_dynamic_stage_insertion.py`: unit tests for inserted child stage metadata and source-stage count behavior.

---

### Task 1: Progress Decision Rules

**Files:**
- Modify: `backend/agents/progress_manager.py`
- Test: `backend/tests/test_progress_manager_dynamic.py`

- [ ] **Step 1: Write failing tests for child-stage branching**

Create `backend/tests/test_progress_manager_dynamic.py`:

```python
import pytest

from backend.agents.base_agent import AgentContext
from backend.agents.progress_manager import ProgressManagerAgent


def ctx(payload: dict) -> AgentContext:
    return AgentContext(session_id="s1", user_id="u1", task_payload=payload)


@pytest.mark.asyncio
async def test_remediation_child_partially_mastered_repeats_remediation_when_under_limit():
    agent = ProgressManagerAgent()
    result = await agent.run(ctx({
        "evaluations": [
            {"score": 0.72, "confused_concepts": ["家族相似性"], "misconception_patterns": []},
            {"score": 0.78, "confused_concepts": [], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 11,
        "total_stages": 4,
        "current_attempt": 1,
        "stage_kind": "remediation",
        "source_stage_id": 3,
        "source_reteach_count": 0,
        "source_remediation_count": 1,
    }))

    assert result["decision"] == "remediate"
    assert result["remediation_focus"] == ["家族相似性"]


@pytest.mark.asyncio
async def test_reteach_child_unmastered_reteaches_again_when_under_limit():
    agent = ProgressManagerAgent()
    result = await agent.run(ctx({
        "evaluations": [
            {"score": 0.1, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
            {"score": 0.2, "confused_concepts": ["規則遵循"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 12,
        "total_stages": 4,
        "current_attempt": 1,
        "stage_kind": "reteach",
        "source_stage_id": 3,
        "source_reteach_count": 1,
        "source_remediation_count": 0,
    }))

    assert result["decision"] == "reteach"
    assert result["remediation_focus"] == ["語言遊戲", "規則遵循"]


@pytest.mark.asyncio
async def test_reteach_child_unmastered_turns_to_remediation_at_reteach_limit():
    agent = ProgressManagerAgent()
    result = await agent.run(ctx({
        "evaluations": [
            {"score": 0.1, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
            {"score": 0.2, "confused_concepts": ["規則遵循"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 13,
        "total_stages": 4,
        "current_attempt": 1,
        "stage_kind": "reteach",
        "source_stage_id": 3,
        "source_reteach_count": 2,
        "source_remediation_count": 0,
    }))

    assert result["decision"] == "remediate"
    assert "重教" in result["message"]


@pytest.mark.asyncio
async def test_remediation_child_advances_at_remediation_limit():
    agent = ProgressManagerAgent()
    result = await agent.run(ctx({
        "evaluations": [
            {"score": 0.5, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
            {"score": 0.55, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 14,
        "total_stages": 4,
        "current_attempt": 1,
        "stage_kind": "remediation",
        "source_stage_id": 3,
        "source_reteach_count": 0,
        "source_remediation_count": 2,
    }))

    assert result["decision"] == "advance"
    assert result["next_stage_id"] is None
```

- [ ] **Step 2: Run tests and verify they fail**

Run from repo root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_progress_manager_dynamic.py -q
```

Expected: failures showing dynamic stages currently always advance or unsupported fields are ignored.

- [ ] **Step 3: Implement deterministic mastery classification**

In `backend/agents/progress_manager.py`, add helpers above `ProgressManagerAgent`:

```python
def _unique_confused_concepts(evaluations: list[dict]) -> list[str]:
    concepts: list[str] = []
    for ev in evaluations:
        concepts.extend(ev.get("confused_concepts", []))
    return list(dict.fromkeys(c for c in concepts if c))


def _mastery_state(scores: list[float], confused: list[str], pass_threshold: float) -> str:
    if not scores:
        return "none"
    best_score = max(scores)
    avg_score = sum(scores) / len(scores)
    passed_count = sum(1 for score in scores if score >= pass_threshold)
    if best_score >= pass_threshold and not confused:
        return "complete"
    if best_score < 0.5 and avg_score < 0.5:
        return "none"
    if passed_count > 0 or best_score >= pass_threshold:
        return "partial"
    return "partial" if confused else "none"
```

- [ ] **Step 4: Replace dynamic-stage early advance**

In `ProgressManagerAgent.run`, remove the `if is_dynamic: return advance` block. Read these payload fields after `remediate_count`:

```python
stage_kind: str = payload.get("stage_kind") or ("dynamic" if is_dynamic else "main")
source_stage_id = payload.get("source_stage_id")
source_reteach_count: int = int(payload.get("source_reteach_count", 0) or 0)
source_remediation_count: int = int(payload.get("source_remediation_count", remediate_count) or 0)
max_reteach: int = int(payload.get("max_reteach", 2))
max_remediation: int = int(payload.get("max_remediation", 2))
```

After computing `unique_confused`, `high_severity`, and `repeated_patterns`, add child-stage branching before the current main-stage decision ladder:

```python
mastery = _mastery_state(scores, unique_confused, pass_threshold)
is_child_stage = stage_kind in {"reteach", "remediation"} or source_stage_id is not None

if is_child_stage:
    if mastery == "complete":
        decision = "advance"
        next_stage = None
        message = f"很好！你已掌握這個補充章節（校正得分：{best_score:.0%}），讓我們繼續。"
    elif stage_kind == "reteach" and mastery == "none" and source_reteach_count < max_reteach:
        decision = "reteach"
        next_stage = None
        message = "這一輪仍未建立整體理解，我會再插入一個新的重教子章節，用另一個框架重新說明。"
    elif source_remediation_count < max_remediation:
        decision = "remediate"
        next_stage = None
        message = "你已掌握部分內容，我會針對仍卡住的概念新增補強子章節。"
    else:
        decision = "advance"
        next_stage = None
        message = "你已達到此章補強上限，先繼續前進，後續可再回顧這些概念。"
else:
    # keep main-stage decision ladder here
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_progress_manager_dynamic.py -q
```

Expected: `4 passed`.

---

### Task 2: Child Stage Insertion

**Files:**
- Modify: `backend/orchestrator/learning_orchestrator.py`
- Test: `backend/tests/test_dynamic_stage_insertion.py`

- [ ] **Step 1: Write failing insertion tests**

Create tests that instantiate `LearningOrchestrator` without calling LLM methods. Patch `session_memory.store_stages` and `session_memory.upsert_stage_progress` with async stubs.

```python
import pytest

from backend.orchestrator.learning_orchestrator import LearningOrchestrator


@pytest.mark.asyncio
async def test_insert_reteach_stage_preserves_source_stage(monkeypatch):
    stored = {}

    async def store_stages(session_id, stages):
        stored["stages"] = stages

    async def upsert_stage_progress(**kwargs):
        stored["progress"] = kwargs

    monkeypatch.setattr("backend.orchestrator.learning_orchestrator.session_memory.store_stages", store_stages)
    monkeypatch.setattr("backend.orchestrator.learning_orchestrator.session_memory.upsert_stage_progress", upsert_stage_progress)

    orchestrator = LearningOrchestrator()
    stages = [{
        "stage_id": 3,
        "node_id": "3",
        "title": "原章",
        "content": "原始內容",
        "key_concepts": ["語言遊戲"],
        "source_chunks": [{"chunk_id": "chunk_0001", "quote": "原文"}],
    }]

    updated, idx = await orchestrator._insert_reteach_stage("s1", stages, 0, ["語言遊戲"])

    assert idx == 1
    assert updated[0]["content"] == "原始內容"
    assert updated[1]["kind"] == "reteach"
    assert updated[1]["source_stage_id"] == 3
    assert "重教" in updated[1]["title"]
    assert stored["progress"]["understanding_notes"]["kind"] == "reteach"


def test_count_dynamic_children_by_source_stage():
    orchestrator = LearningOrchestrator()
    stages = [
        {"stage_id": 3, "kind": "main"},
        {"stage_id": 4, "kind": "reteach", "source_stage_id": 3},
        {"stage_id": 5, "kind": "remediation", "source_stage_id": 3},
        {"stage_id": 6, "kind": "remediation", "source_stage_id": 3},
        {"stage_id": 7, "kind": "reteach", "source_stage_id": 2},
    ]

    assert orchestrator._count_child_stages(stages, 3, "reteach") == 1
    assert orchestrator._count_child_stages(stages, 3, "remediation") == 2
```

- [ ] **Step 2: Run insertion tests and verify fail**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_dynamic_stage_insertion.py -q
```

Expected: `_insert_reteach_stage` and `_count_child_stages` do not exist yet.

- [ ] **Step 3: Add child stage helpers**

In `LearningOrchestrator`, add:

```python
def _source_stage_id(self, stage: dict) -> int:
    return int(stage.get("source_stage_id") or stage.get("stage_id"))


def _count_child_stages(self, stages: list[dict], source_stage_id: int, kind: str) -> int:
    return sum(
        1
        for s in stages
        if int(s.get("source_stage_id") or -1) == int(source_stage_id)
        and s.get("kind") == kind
    )
```

- [ ] **Step 4: Split reteach and remediation insertion**

Add `_insert_reteach_stage` next to `_insert_remediation_stage`. Update `_insert_remediation_stage` to set `kind: "remediation"` and preserve `source_stage_id` from the source root.

```python
async def _insert_reteach_stage(
    self,
    session_id: str,
    stages: list[dict],
    current_idx: int,
    reteach_focus: list[str],
) -> tuple[list[dict], int]:
    current = stages[current_idx]
    source_stage_id = self._source_stage_id(current)
    max_stage_id = max((s.get("stage_id", 0) for s in stages), default=0)
    new_stage_id = max_stage_id + 1
    focus_text = "、".join(reteach_focus[:3]) if reteach_focus else "核心概念"
    new_stage = {
        "stage_id": new_stage_id,
        "node_id": f"T.{source_stage_id}.{self._count_child_stages(stages, source_stage_id, 'reteach') + 1}",
        "title": f"重教：{current.get('title', focus_text)}",
        "content": (
            f"本節為重教子章節，請針對「{focus_text}」用完全不同的教學框架重新組織。\n\n"
            f"原章節內容：\n{current.get('content', '')[:1200]}"
        ),
        "key_concepts": current.get("key_concepts", [])[:5],
        "prerequisites": [current.get("title", "")],
        "estimated_questions": 3,
        "source_chunks": self._normalize_stage_source_chunks(current),
        "is_dynamic": True,
        "kind": "reteach",
        "source_stage_id": source_stage_id,
    }
    insert_idx = current_idx + 1
    updated = stages[:insert_idx] + [new_stage] + stages[insert_idx:]
    await session_memory.store_stages(session_id, updated)
    await session_memory.upsert_stage_progress(
        session_id=session_id,
        stage_id=new_stage_id,
        status="pending",
        attempts=0,
        best_score=0.0,
        understanding_notes={"dynamic": True, "kind": "reteach", "source_stage_id": source_stage_id, "focus": reteach_focus[:3]},
    )
    return updated, insert_idx
```

- [ ] **Step 5: Run insertion tests and verify pass**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_dynamic_stage_insertion.py -q
```

Expected: `2 passed`.

---

### Task 3: Orchestrator Routing

**Files:**
- Modify: `backend/orchestrator/learning_orchestrator.py`
- Modify: `backend/agents/progress_manager.py`

- [ ] **Step 1: Pass child metadata to ProgressManager**

In `_make_progress_decision`, extend task payload:

```python
source_stage_id = self._source_stage_id(stage)
"stage_kind": stage.get("kind", "main"),
"source_stage_id": source_stage_id,
"source_reteach_count": self._count_child_stages(stages, source_stage_id, "reteach"),
"source_remediation_count": self._count_child_stages(stages, source_stage_id, "remediation"),
"max_reteach": 2,
"max_remediation": 2,
```

- [ ] **Step 2: Insert the correct child stage for each decision**

Replace the shared `elif d in ("remediate", "reteach")` insertion logic:

```python
elif d == "reteach":
    focus = decision.get("remediation_focus") or stage.get("key_concepts", [])[:2]
    stages_for_run, next_stage_idx = await self._insert_reteach_stage(
        session_id=session_id,
        stages=stages,
        current_idx=current_idx,
        reteach_focus=focus,
    )
    wm.stages = stages_for_run
    stages = stages_for_run
    dynamic_stage_inserted = True
    decision_reasons.append("已動態插入重教子章節（" + "、".join(focus[:3]) + "）。")
elif d == "remediate":
    focus = decision.get("remediation_focus") or stage.get("key_concepts", [])[:2]
    stages_for_run, next_stage_idx = await self._insert_remediation_stage(
        session_id=session_id,
        stages=stages,
        current_idx=current_idx,
        remediation_focus=focus,
    )
    wm.stages = stages_for_run
    stages = stages_for_run
    dynamic_stage_inserted = True
    decision_reasons.append("已動態插入補強子章節（" + "、".join(focus[:3]) + "）。")
```

- [ ] **Step 3: Route reteach through new stage instead of explanation reset**

Replace the long `elif d == "reteach"` streaming branch with the same child-stage transition used by remediation:

```python
elif d in ("remediate", "reteach") and next_stage_idx is not None:
    await session_memory.upsert_stage_progress(
        session_id=session_id,
        stage_id=stage["stage_id"],
        status="completed",
        attempts=wm.current_attempt,
        best_score=decision["best_score"],
        understanding_notes={
            "branched_to": d,
            "focus": decision.get("remediation_focus") or [],
            "source_stage_id": self._source_stage_id(stage),
        },
    )
    refreshed_statuses = await session_memory.get_stage_statuses(session_id)
    await emit({"type": "session_started", "payload": { ... }})
    await self.run_stage(session_id, user_id, stages, next_stage_idx, wm.question_mode, emit)
```

Do not emit `explanation_reset` for reteach.

- [ ] **Step 4: Preserve retry behavior**

Leave the `retry` branch unchanged. Retry remains the only same-stage re-questioning path.

- [ ] **Step 5: Run focused backend tests**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_progress_manager_dynamic.py backend\tests\test_dynamic_stage_insertion.py -q
```

Expected: all focused tests pass.

---

### Task 4: Frontend Stage Metadata

**Files:**
- Modify: `frontend/src/types/messages.ts`
- Modify: `frontend/src/api/session.ts`
- Modify: `frontend/src/store/sessionStore.ts`
- Modify: `frontend/src/components/StageMap.tsx`
- Modify: `backend/routers/session.py`
- Modify: `backend/orchestrator/learning_orchestrator.py`

- [ ] **Step 1: Extend stage DTOs**

Add optional fields to frontend stage types:

```ts
export interface StageInfo {
  stage_id: number;
  node_id?: string;
  title: string;
  source_chunks?: SourceChunk[];
  kind?: 'reteach' | 'remediation' | 'enrichment' | string;
  source_stage_id?: number;
}
```

- [ ] **Step 2: Include metadata in backend stage payloads**

Where backend emits or returns stages, include:

```python
"node_id": s.get("node_id", ""),
"kind": s.get("kind"),
"source_stage_id": s.get("source_stage_id"),
```

Apply this to `LearningOrchestrator` `session_started` payloads and `backend/routers/session.py` active/session detail responses.

- [ ] **Step 3: Label child stages in StageMap**

In `StageMap.tsx`, derive a label:

```tsx
const kindLabel =
  stage.kind === 'reteach' ? '重教子章節' :
  stage.kind === 'remediation' ? '補強子章節' :
  stage.kind === 'enrichment' ? '整合挑戰' :
  null;
```

Render it near the status line:

```tsx
{kindLabel && <span className="stage-kind-label">{kindLabel}</span>}
```

- [ ] **Step 4: Run frontend typecheck/build**

Run:

```powershell
npm run build
```

from `frontend`.

Expected: TypeScript and Vite build pass.

---

### Task 5: Verification And Docs

**Files:**
- Modify: `BACKEND_FLOW.md`
- Keep: `docs/REMEDIATION_RETEACH_UX_NOTES.md`

- [ ] **Step 1: Update backend flow docs**

Update `BACKEND_FLOW.md` sections covering `ProgressManager`, dynamic nodes, and reteach/remediate behavior to match:

- `reteach` inserts `kind: "reteach"` child stages.
- `remediate` inserts `kind: "remediation"` child stages.
- Dynamic stages no longer always advance.
- Both counts are scoped by root `source_stage_id` and capped at 2.

- [ ] **Step 2: Run full backend tests**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Expected: all backend tests pass.

- [ ] **Step 3: Run frontend build**

Run:

```powershell
npm run build
```

from `frontend`.

Expected: build passes.

- [ ] **Step 4: Manual smoke path**

With backend running, use the app and confirm:

- A reteach decision inserts a new visible child stage.
- The original stage remains reviewable with its original explanation and QA.
- No `explanation_reset` is emitted for reteach.
- A remediation decision inserts a new visible child stage.
- Child stage completion can branch again until the per-source-stage cap is reached.

---

## Self-Review

- Spec coverage: The plan covers independent flows, child-stage insertion, no original-stage overwrite, direct entry into child stages, post-child branching, and two-attempt caps.
- Placeholder scan: No task uses TBD/TODO/fill-later language; code snippets identify exact functions and files.
- Type consistency: Stage metadata uses `kind` and `source_stage_id` consistently across backend, WebSocket payloads, REST payloads, and frontend store/display.
