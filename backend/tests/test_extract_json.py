"""Lock the _slice_json outer-bracket selection so reducer LLM array
回應不再被剝掉外層 [ ]（sess_live_ea7e75e5 觸發 llm_reducer_no_accepted_outcomes 的根因）。"""
import json

import pytest

from backend.utils import extract_json
from backend.utils.curriculum_reducer import parse_reducer_llm_output


@pytest.mark.parametrize(
    "raw, expected_kind, expected_len",
    [
        ("```json\n[{\"a\":1},{\"b\":2}]\n```", list, 2),
        ("```json\n{\"x\":1}\n```", dict, None),
        ("[{\"a\":1},{\"b\":2}]", list, 2),
        ("{\"x\":1}", dict, None),
        ("{\"items\":[1,2,3],\"x\":1}", dict, None),
        ("Result: [{\"a\":1}]", list, 1),
        ("Here is JSON: {\"x\":1}", dict, None),
        ("```json\n[]\n```", list, 0),
    ],
)
def test_extract_json_outer_bracket_selection(raw, expected_kind, expected_len):
    out = extract_json(raw)
    parsed = json.loads(out)
    assert isinstance(parsed, expected_kind)
    if expected_len is not None:
        assert len(parsed) == expected_len


def test_parse_reducer_llm_output_fenced_array():
    raw = (
        "```json\n"
        "[\n"
        "  {\"outcome_id\": \"lo_001\", \"title\": \"A\", \"merge_decision\": \"split\"},\n"
        "  {\"outcome_id\": \"lo_002\", \"title\": \"B\", \"merge_decision\": \"merged\"}\n"
        "]\n"
        "```"
    )
    out = parse_reducer_llm_output(raw)
    assert len(out) == 2
    assert out[0]["outcome_id"] == "lo_001"
    assert out[1]["outcome_id"] == "lo_002"
