"""Policy: live LLM tests must not run during normal pytest."""
from pathlib import Path



def test_pytest_ini_excludes_live_markers_by_default():
    ini = Path(__file__).resolve().parents[2] / "pytest.ini"
    text = ini.read_text(encoding="utf-8")
    assert "not llm_live" in text
    assert "not curriculum_live" in text


def test_live_curriculum_tool_not_under_tests():
    tools_live = Path(__file__).resolve().parents[1] / "tools" / "live_small_file_curriculum_test.py"
    tests_live = Path(__file__).resolve().parent / "live_small_file_curriculum_test.py"
    assert tools_live.is_file()
    assert not tests_live.exists()
