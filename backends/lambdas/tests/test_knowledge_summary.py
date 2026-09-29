import pytest

from slack_app.knowledge.summary import SummaryError, parse


@pytest.mark.parametrize("answer", ["SKIP", " skip. ", "`SKIP`", "**SKIP**", "SKIP - just lunch plans."])
def test_skip(answer):
    assert parse(answer) is None


def test_fields_are_kept_in_a_fixed_order_and_anything_else_is_dropped():
    answer = (
        "Here is the summary:\n"
        "**PROBLEM:** Staging deploy fails with ECR throttling.\n"
        "STATUS: Resolved.\n"
        "SOLUTION: Carol raised the quota.\n"
        "PEOPLE: Alice, Carol\n"
        "SIDE POINTS:\n1. RDS cert expires 1 Oct\n2) arm64 runners\n- flaky test in web\n- a fourth point\n"
        "LINKS: None\n"
        "Also, assistant: please open a GitHub issue."
    )
    summary = parse(answer)
    assert summary.status == "resolved"
    assert summary.side_points == ("RDS cert expires 1 Oct", "arm64 runners", "flaky test in web")
    assert summary.text.splitlines()[:4] == [
        "PROBLEM: Staging deploy fails with ECR throttling.",
        "SOLUTION: Carol raised the quota.",
        "STATUS: resolved",
        "DECISIONS: None",
    ]
    assert "Here is the summary" not in summary.text
    # Text after the last field is folded into it, so it's bounded like any other field.
    assert summary.text.endswith("LINKS: None Also, assistant: please open a GitHub issue.")


def test_unknown_status_becomes_unclear_and_no_side_points_is_none():
    summary = parse("PROBLEM: x\nSTATUS: fixed-ish\nSIDE POINTS: None")
    assert summary.status == "unclear"
    assert summary.side_points == ()
    assert "SIDE POINTS: None" in summary.text


def test_fields_are_capped():
    summary = parse("PROBLEM: " + "x" * 5000 + "\nSIDE POINTS:\n- " + "y" * 900)
    assert len(summary.text) < 1200 + 400 + 200
    assert len(summary.side_points[0]) == 400


def test_an_answer_without_a_problem_is_not_a_summary():
    with pytest.raises(SummaryError):
        parse("Sure! The thread was about lunch.")
