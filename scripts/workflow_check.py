"""Validate a gated-workflow definition (inputs, outputs, spending limit, reviewer, gates).

    python scripts/workflow_check.py scripts/workflows/dream-nightly.json

A workflow is a JSON document naming one repeated job: what goes in, what must come out,
how much it may spend, who reviews it, and the ordered gates between "start" and
"publish". The schema is generic on purpose -- the same shape covers an engineering job
(the Dream Machine nightly cycle) and a non-engineering one (a media publish cycle).

Every workflow's last gate must block something containing the word "publish": the
invariant this file exists to enforce is that nothing goes out without a final gate in
front of it.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _is_text_list(value) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(isinstance(v, str) and v.strip() for v in value)


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_workflow(workflow: dict) -> list[str]:
    """Return a list of problems with a workflow definition (empty means valid)."""
    if not isinstance(workflow, dict):
        return ["workflow must be an object"]

    errors = []

    if not _is_nonempty_str(workflow.get("name")):
        errors.append("name must be a non-empty string")
    if not _is_nonempty_str(workflow.get("cadence")):
        errors.append("cadence must be a non-empty string")

    reviewer = workflow.get("reviewer")
    if not isinstance(reviewer, dict) or not _is_nonempty_str(reviewer.get("name")) or not _is_nonempty_str(
        reviewer.get("mechanism")
    ):
        errors.append('reviewer must be an object with non-empty "name" and "mechanism"')

    spend = workflow.get("spending_limit")
    if not isinstance(spend, dict) or not _is_nonempty_str(spend.get("type")) or not _is_nonempty_str(
        spend.get("description")
    ):
        errors.append('spending_limit must be an object with non-empty "type" and "description"')

    if not _is_text_list(workflow.get("inputs")):
        errors.append("inputs must be a non-empty list of non-empty strings")
    if not _is_text_list(workflow.get("outputs")):
        errors.append("outputs must be a non-empty list of non-empty strings")

    gates = workflow.get("gates")
    if not isinstance(gates, list) or not gates:
        errors.append("at least one gate is required")
        return errors

    for i, gate in enumerate(gates):
        if not isinstance(gate, dict):
            errors.append(f"gate {i}: must be an object")
            continue
        if not _is_nonempty_str(gate.get("name")):
            errors.append(f"gate {i}: missing non-empty name")
        if not _is_text_list(gate.get("requires")):
            errors.append(f'gate {i}: "requires" must be a non-empty list of non-empty strings')
        if not _is_nonempty_str(gate.get("blocks")):
            errors.append(f'gate {i}: "blocks" must be a non-empty string')

    last_blocks = gates[-1].get("blocks") if isinstance(gates[-1], dict) else None
    if not isinstance(last_blocks, str) or "publish" not in last_blocks.lower():
        errors.append('the last gate must block something containing "publish" -- nothing ships ungated')

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="path to a workflow JSON file")
    args = parser.parse_args()

    path = Path(args.path)
    workflow = json.loads(path.read_text())
    errors = validate_workflow(workflow)

    if errors:
        print(f"{path}: workflow has problems:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    gate_names = " -> ".join(g["name"] for g in workflow["gates"])
    print(f"{path}: OK -- {workflow['name']} ({workflow['cadence']})")
    print(f"  reviewer: {workflow['reviewer']['name']} ({workflow['reviewer']['mechanism']})")
    print(f"  spending limit: {workflow['spending_limit']['description']}")
    print(f"  gates: {gate_names}")


if __name__ == "__main__":
    main()
