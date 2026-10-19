import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import workflow_check  # noqa: E402

WORKFLOWS_DIR = ROOT / "scripts" / "workflows"


def _load(name):
    return json.loads((WORKFLOWS_DIR / name).read_text())


@pytest.fixture
def dream_workflow():
    return _load("dream-nightly.json")


@pytest.fixture
def media_workflow():
    return _load("media-publish.example.json")


@pytest.mark.parametrize("workflow", [None, [], "workflow", 1, True])
def test_workflow_must_be_an_object(workflow):
    assert workflow_check.validate_workflow(workflow) == ["workflow must be an object"]


def test_dream_nightly_workflow_is_valid(dream_workflow):
    assert workflow_check.validate_workflow(dream_workflow) == []


def test_media_publish_example_is_valid(media_workflow):
    assert workflow_check.validate_workflow(media_workflow) == []


def test_media_publish_example_has_the_five_required_gates(media_workflow):
    names = [g["name"] for g in media_workflow["gates"]]
    assert names == ["script", "visual-plan", "asset-receipts", "audio-check", "final-review"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"name": "  "}, "name must be"),
        ({"cadence": ""}, "cadence must be"),
        ({"reviewer": {"name": "x"}}, "reviewer must be"),
        ({"reviewer": {"mechanism": "x"}}, "reviewer must be"),
        ({"spending_limit": {"type": "bounded"}}, "spending_limit must be"),
        ({"inputs": []}, "inputs must be"),
        ({"inputs": [""]}, "inputs must be"),
        ({"outputs": []}, "outputs must be"),
        ({"gates": []}, "at least one gate"),
    ],
)
def test_workflow_rules(dream_workflow, change, message):
    workflow = {**dream_workflow, **change}
    errors = workflow_check.validate_workflow(workflow)
    assert any(message in e for e in errors), errors


def test_gate_missing_requires_is_rejected(dream_workflow):
    workflow = copy.deepcopy(dream_workflow)
    del workflow["gates"][0]["requires"]
    errors = workflow_check.validate_workflow(workflow)
    assert any('"requires"' in e for e in errors), errors


def test_gate_missing_blocks_is_rejected(dream_workflow):
    workflow = copy.deepcopy(dream_workflow)
    del workflow["gates"][0]["blocks"]
    errors = workflow_check.validate_workflow(workflow)
    assert any('"blocks"' in e for e in errors), errors


def test_last_gate_must_block_something_containing_publish(dream_workflow):
    workflow = copy.deepcopy(dream_workflow)
    workflow["gates"][-1]["blocks"] = "a follow-up ticket"
    errors = workflow_check.validate_workflow(workflow)
    assert any("publish" in e for e in errors), errors
