import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import dream_check  # noqa: E402

HEADER = "| " + " | ".join(dream_check.LEDGER_COLUMNS) + " |\n| " + " | ".join(["---"] * 10) + " |\n"
ROW = "| 2026-10-01 | triage-quality | fix | #1 | #2 | yes | ACCEPT | tests 237->239 | abcd1234 | first night |\n"


@pytest.fixture
def config():
    return json.loads((ROOT / "dream.config.json").read_text())


def test_committed_config_is_valid(config):
    assert dream_check.validate_config(config) == []


def test_committed_ledger_is_valid(config):
    assert dream_check.validate_ledger((ROOT / config["ledgerPath"]).read_text()) == []


@pytest.mark.parametrize(
    "change, message",
    [
        ({"repo": "no-slash"}, "owner/name"),
        ({"cron": "0 2 * *"}, "5-field"),
        ({"cron": "*/10 * * * *"}, "minimum interval"),
        ({"slots": []}, "at least one rotation slot"),
        ({"slots": [{"deep": " ", "scan": ["a"]}]}, 'missing "deep"'),
        ({"slots": [{"deep": "x", "scan": "a"}]}, '"scan" must be a list'),
        ({"bonusModuli": {"x": "docs"}}, "positive integer"),
        ({"bonusModuli": {"25": ""}}, "non-empty string"),
        ({"autoMerge": True}, "autoMerge must be false"),
    ],
)
def test_config_rules(config, change, message):
    errors = dream_check.validate_config({**config, **change})
    assert any(message in e for e in errors), errors


def test_auto_merge_must_be_explicitly_off(config):
    del config["autoMerge"]
    assert any("autoMerge" in e for e in dream_check.validate_config(config))


def test_ledger_accepts_a_well_formed_row():
    assert dream_check.validate_ledger(HEADER + ROW) == []


@pytest.mark.parametrize(
    "ledger, message",
    [
        ("| Date | Deep |\n| --- | --- |\n", "10-column header"),
        (HEADER + "| 2026-10-01 | only | three |\n", "expected 10"),
        (HEADER + ROW.replace("2026-10-01", "Oct 1"), "YYYY-MM-DD"),
        (HEADER + ROW.replace("ACCEPT", "MAYBE"), "Verdict"),
    ],
)
def test_ledger_rules(ledger, message):
    errors = dream_check.validate_ledger(ledger)
    assert any(message in e for e in errors), errors


def test_tonight_uses_dayint_modulo_slot_count(config):
    # 20260929 % 5 == 4
    t = dream_check.tonight(config, dt.date(2026, 9, 29))
    assert (t["slot"], t["deep"]) == (4, "infra-and-dev-loop")
    assert t["bonus"] == []


def test_tonight_adds_bonus_surface_on_matching_days(config):
    t = dream_check.tonight(config, dt.date(2026, 9, 25))
    assert t["bonus"] == ["docs-accuracy"]
