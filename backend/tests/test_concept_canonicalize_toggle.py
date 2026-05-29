import os
import pytest
from unittest.mock import patch

from backend.orchestrator.curriculum_pipeline_v2 import _canonicalize_enabled


def test_default_disabled():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONCEPT_CANONICALIZE", None)
        assert _canonicalize_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "Yes"])
def test_enabled_values(raw):
    with patch.dict(os.environ, {"CONCEPT_CANONICALIZE": raw}, clear=False):
        assert _canonicalize_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "", "anything"])
def test_disabled_values(raw):
    with patch.dict(os.environ, {"CONCEPT_CANONICALIZE": raw}, clear=False):
        assert _canonicalize_enabled() is False
