"""Phase 4 / T4b: PedagogicalPlannerAgent + prompt contract.

The agent builds a compact stage-level planning payload, asks an LLM for a JSON
stage-move plan, and parses it against the T4a PedagogicalPlan schema. It never
applies moves, wires into the pipeline, or changes curriculum output.

LLM is faked; no real network calls.
"""
import asyncio
import copy
import json
import unittest

from backend.agents.pedagogical_planner import (
    PedagogicalPlannerAgent,
    PedagogicalPlannerAgentResult,
    _build_planner_payload,
    _extract_json_object,
)
from backend.llm.base_provider import LLMResponse
from backend.utils.prompt_templates import SYSTEM_PROMPTS
from backend.utils.pedagogical_planner import (
    PedagogicalPlan,
    build_stage_cards,
    build_prerequisite_graph,
    build_ordering_plan,
)


def _stage(stage_id=None, title="T", summary="S", key_concepts=("k",),
           source_ids=(), source_stage_ids=()):
    d = {"title": title, "summary": summary, "key_concepts": list(key_concepts),
         "source_chunk_ids": ["chunk_0000"]}
    if stage_id is not None:
        d["stage_id"] = stage_id
    if source_ids:
        d["source_ids"] = list(source_ids)
    if source_stage_ids:
        d["source_stage_ids"] = list(source_stage_ids)
    return d


def _inputs(stages):
    cards, _ = build_stage_cards(stages)
    graph, _ = build_prerequisite_graph(cards)
    ordering = build_ordering_plan(cards, graph)
    return cards, graph, ordering


class _FakeLLM:
    def __init__(self, content=None, raise_exc=None):
        self._content = content
        self._raise = raise_exc
        self.calls = []

    async def chat(self, messages, system_prompt=None):
        self.calls.append({"messages": messages, "system_prompt": system_prompt})
        if self._raise is not None:
            raise self._raise
        return LLMResponse(
            content=self._content, input_tokens=1, output_tokens=1,
            model="fake", finish_reason="stop",
        )


def _agent(content=None, raise_exc=None):
    llm = _FakeLLM(content=content, raise_exc=raise_exc)
    return PedagogicalPlannerAgent(llm, None), llm


def _propose(agent, stages):
    cards, graph, ordering = _inputs(stages)
    return asyncio.run(
        agent.propose_plan(stages=stages, cards=cards, graph=graph, ordering_plan=ordering)
    )


class TestPayloadBuilder(unittest.TestCase):
    def test_builds_compact_stage_payload_with_normalized_ids(self):
        stages = [_stage(title="A", key_concepts=("k1",))]  # no explicit stage_id
        cards, graph, ordering = _inputs(stages)
        payload = _build_planner_payload(stages, cards, graph, ordering)
        self.assertEqual(payload["stages"][0]["stage_id"], "stage_0")
        self.assertEqual(payload["stages"][0]["title"], "A")
        self.assertEqual(payload["stages"][0]["key_concepts"], ["k1"])

    def test_includes_card_role_and_difficulty(self):
        stages = [_stage("s1", title="提示詞工程概論")]
        cards, graph, ordering = _inputs(stages)
        payload = _build_planner_payload(stages, cards, graph, ordering)
        card = payload["stage_cards"][0]
        self.assertEqual(card["stage_id"], "s1")
        self.assertIn("role", card)
        self.assertIn("difficulty", card)

    def test_includes_prerequisite_edges(self):
        stages = [_stage("a", title="檢索 Retrieval"), _stage("b", title="檢索增強生成 RAG")]
        cards, graph, ordering = _inputs(stages)
        payload = _build_planner_payload(stages, cards, graph, ordering)
        pairs = [(e["before_stage_id"], e["after_stage_id"]) for e in payload["prerequisite_edges"]]
        self.assertIn(("a", "b"), pairs)

    def test_includes_ordering_plan(self):
        stages = [_stage("a"), _stage("b")]
        cards, graph, ordering = _inputs(stages)
        payload = _build_planner_payload(stages, cards, graph, ordering)
        op = payload["ordering_plan"]
        self.assertIn("current_stage_ids", op)
        self.assertIn("recommended_stage_ids", op)
        self.assertIn("order_changed", op)
        self.assertIn("diagnostics", op)

    def test_payload_excludes_chunk_fields(self):
        stages = [_stage("a")]
        cards, graph, ordering = _inputs(stages)
        payload = _build_planner_payload(stages, cards, graph, ordering)
        self.assertNotIn("source_chunk_ids", payload["stages"][0])
        self.assertNotIn("chunk", json.dumps(payload, ensure_ascii=False).lower())

    def test_does_not_mutate_inputs(self):
        stages = [_stage("a"), _stage("b")]
        before = copy.deepcopy(stages)
        cards, graph, ordering = _inputs(stages)
        _build_planner_payload(stages, cards, graph, ordering)
        self.assertEqual(stages, before)


class TestJsonExtraction(unittest.TestCase):
    def test_extracts_plain_json_object(self):
        self.assertEqual(_extract_json_object('{"moves": [], "rationale": "x"}'),
                         {"moves": [], "rationale": "x"})

    def test_extracts_fenced_json_object(self):
        text = '```json\n{"moves": [], "rationale": "x"}\n```'
        self.assertEqual(_extract_json_object(text), {"moves": [], "rationale": "x"})

    def test_rejects_non_json_text(self):
        self.assertIsNone(_extract_json_object("not json at all"))

    def test_rejects_json_list(self):
        self.assertIsNone(_extract_json_object('[1, 2, 3]'))

    def test_tolerates_prose_wrapped_object(self):
        text = 'Here is the plan: {"moves": [], "rationale": "x"} done.'
        self.assertEqual(_extract_json_object(text), {"moves": [], "rationale": "x"})


class TestAgentSuccess(unittest.TestCase):
    def test_valid_plan_json_returns_plan(self):
        content = '{"moves": [{"stage_id": "b", "after_stage_id": "a", "reason": "r"}], "rationale": "R"}'
        agent, _ = _agent(content=content)
        res = _propose(agent, [_stage("a"), _stage("b")])
        self.assertIsInstance(res, PedagogicalPlannerAgentResult)
        self.assertIsInstance(res.plan, PedagogicalPlan)
        self.assertEqual(res.diagnostics, ())

    def test_parsed_moves_match_schema(self):
        content = '{"moves": [{"stage_id": "b", "after_stage_id": null, "reason": "r"}], "rationale": "R"}'
        agent, _ = _agent(content=content)
        res = _propose(agent, [_stage("a"), _stage("b")])
        self.assertEqual(res.plan.moves[0].stage_id, "b")
        self.assertIsNone(res.plan.moves[0].after_stage_id)
        self.assertEqual(res.plan.rationale, "R")

    def test_raw_response_preserved(self):
        content = '{"moves": [], "rationale": "x"}'
        agent, _ = _agent(content=content)
        res = _propose(agent, [_stage("a")])
        self.assertEqual(res.raw_response, content)

    def test_empty_moves_accepted(self):
        agent, _ = _agent(content='{"moves": [], "rationale": "no change"}')
        res = _propose(agent, [_stage("a")])
        self.assertIsNotNone(res.plan)
        self.assertEqual(res.plan.moves, ())

    def test_fenced_valid_json_accepted(self):
        agent, _ = _agent(content='```json\n{"moves": [], "rationale": "x"}\n```')
        res = _propose(agent, [_stage("a")])
        self.assertIsNotNone(res.plan)


class TestAgentFailures(unittest.TestCase):
    def test_malformed_json_returns_invalid_json_diagnostic(self):
        agent, _ = _agent(content="totally not json")
        res = _propose(agent, [_stage("a")])
        self.assertIsNone(res.plan)
        self.assertEqual(res.diagnostics[0]["type"], "pedagogical_planner_invalid_json")

    def test_schema_invalid_returns_parse_diagnostic(self):
        agent, _ = _agent(content='{"rationale": "no moves key"}')
        res = _propose(agent, [_stage("a")])
        self.assertIsNone(res.plan)
        self.assertEqual(res.diagnostics[0]["type"], "invalid_plan_payload")

    def test_llm_error_returns_error_diagnostic(self):
        agent, _ = _agent(raise_exc=TimeoutError("slow"))
        res = _propose(agent, [_stage("a")])
        self.assertIsNone(res.plan)
        self.assertEqual(res.diagnostics[0]["type"], "pedagogical_planner_llm_error")
        self.assertEqual(res.diagnostics[0]["reason"], "TimeoutError")
        self.assertIsNone(res.raw_response)


class TestPromptContract(unittest.TestCase):
    def test_prompt_registered(self):
        self.assertIn("pedagogical_planner", SYSTEM_PROMPTS)

    def test_prompt_passed_unmodified_as_system_prompt(self):
        agent, llm = _agent(content='{"moves": [], "rationale": "x"}')
        _propose(agent, [_stage("a")])
        self.assertEqual(llm.calls[0]["system_prompt"], SYSTEM_PROMPTS["pedagogical_planner"])

    def test_prompt_contains_hard_rules(self):
        p = SYSTEM_PROMPTS["pedagogical_planner"]
        for needle in ("JSON", "Do NOT rewrite", "Do NOT create", "at most once",
                       "after_stage_id", "null", "chunk"):
            self.assertIn(needle, p)

    def test_prompt_shows_json_null_not_string_null_contract(self):
        p = SYSTEM_PROMPTS["pedagogical_planner"]
        self.assertIn('"after_stage_id": null', p)
        self.assertNotIn('"existing_stage_id_or_null"', p)

    def test_user_message_includes_serialized_payload(self):
        agent, llm = _agent(content='{"moves": [], "rationale": "x"}')
        _propose(agent, [_stage("a", title="UNIQUE_TITLE_XYZ")])
        user_text = llm.calls[0]["messages"][0].content
        self.assertIn("planner_input=", user_text)
        self.assertIn("UNIQUE_TITLE_XYZ", user_text)

    def test_user_message_excludes_chunk_ids(self):
        agent, llm = _agent(content='{"moves": [], "rationale": "x"}')
        _propose(agent, [_stage("a")])
        user_text = llm.calls[0]["messages"][0].content
        self.assertNotIn("chunk_0000", user_text)


if __name__ == "__main__":
    unittest.main()
