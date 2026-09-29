"""Check the Dream Machine setup offline and show what tonight's cycle will focus on.

    python scripts/dream_check.py            # validate dream.config.json + LEDGER.md, print tonight
    python scripts/dream_check.py --date 2026-10-01

This mirrors the checks the `dream-machine` CLI does (config shape, 10-column ledger),
plus two rules of our own: auto-merge must stay off, and every ledger verdict must be
ACCEPT, REJECT, INCONCLUSIVE or HALT. It needs only the Python standard library, so it
runs in `make test` and in the nightly cloud session without npm.

The slot and bonus maths copy STEP 0 of the compiled prompt:
    DAYINT = YYYYMMDD, SLOT = DAYINT % len(slots), bonus when DAYINT % N == 0
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_COLUMNS = [
    "Date", "Deep", "Finding", "Issue", "PR", "Evaluated?", "Verdict", "Effect", "Witness", "Prior-night fates",
]
VERDICTS = ("ACCEPT", "REJECT", "INCONCLUSIVE", "HALT")


def _is_text_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def validate_config(config: dict) -> list[str]:
    """Return a list of problems with a dream.config (empty means valid)."""
    errors = []
    repo = config.get("repo")
    if not isinstance(repo, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        errors.append('repo must be "owner/name"')

    cron = config.get("cron")
    fields = cron.split() if isinstance(cron, str) else []
    if len(fields) != 5:
        errors.append("cron must be a 5-field expression")
    elif not (fields[0].isdigit() and 0 <= int(fields[0]) <= 59):
        errors.append("cron minute must be a single value from 0 to 59 (minimum interval is 1 hour)")

    slots = config.get("slots")
    if not isinstance(slots, list) or not slots:
        errors.append("at least one rotation slot is required")
    else:
        for i, slot in enumerate(slots):
            if not isinstance(slot, dict):
                errors.append(f'slot {i}: must be an object with "deep" and "scan"')
                continue
            if not isinstance(slot.get("deep"), str) or not slot["deep"].strip():
                errors.append(f'slot {i}: missing "deep" surface')
            if not _is_text_list(slot.get("scan")):
                errors.append(f'slot {i}: "scan" must be a list of surface names')

    for key, surface in (config.get("bonusModuli") or {}).items():
        if not key.isdigit() or int(key) < 1:
            errors.append(f'bonusModuli key "{key}" must be a positive integer')
        if not isinstance(surface, str) or not surface.strip():
            errors.append(f'bonusModuli["{key}"] must be a non-empty string')

    for field in ("controlPlaneProbes", "competitors", "extraDisciplines", "labels"):
        if field in config and not _is_text_list(config[field]):
            errors.append(f"{field} must be a list of non-empty strings")

    for field in ("ledgerPath", "branchPrefix"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            errors.append(f"{field} must be a non-empty string")

    # Our rule, stricter than upstream: a human always merges.
    if config.get("autoMerge") is not False:
        errors.append("autoMerge must be false: evaluation is not promotion, a human merges")
    return errors


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def validate_ledger(markdown: str) -> list[str]:
    """Return a list of problems with LEDGER.md (empty means valid)."""
    lines = [line for line in markdown.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2 or _cells(lines[0]) != LEDGER_COLUMNS:
        return ["ledger must start with the 10-column header: | " + " | ".join(LEDGER_COLUMNS) + " |"]
    if not all(re.fullmatch(r":?-+:?", c) for c in _cells(lines[1])) or len(_cells(lines[1])) != 10:
        return ["ledger header must be followed by a 10-column divider row"]

    errors = []
    for n, line in enumerate(lines[2:], start=1):
        cells = _cells(line)
        if len(cells) != 10:
            errors.append(f"row {n}: has {len(cells)} columns, expected 10")
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            errors.append(f"row {n}: Date must be YYYY-MM-DD, got {cells[0]!r}")
        if not any(v in cells[6] for v in VERDICTS):
            errors.append(f"row {n}: Verdict must name one of {', '.join(VERDICTS)}")
    return errors


def tonight(config: dict, day: dt.date) -> dict:
    """What STEP 0 of the compiled prompt will pick for this date."""
    dayint = int(day.strftime("%Y%m%d"))
    slot_index = dayint % len(config["slots"])
    slot = config["slots"][slot_index]
    bonus = [s for k, s in sorted((config.get("bonusModuli") or {}).items(), key=lambda kv: int(kv[0])) if dayint % int(k) == 0]
    return {"date": day.isoformat(), "slot": slot_index, "deep": slot["deep"], "scan": slot["scan"], "bonus": bonus}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "dream.config.json"))
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, UTC)")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text())
    errors = validate_config(config)
    ledger_path = ROOT / config.get("ledgerPath", "docs/dream-cycle/LEDGER.md")
    if ledger_path.exists():
        errors += [f"{ledger_path.relative_to(ROOT)}: {e}" for e in validate_ledger(ledger_path.read_text())]
    else:
        errors.append(f"{ledger_path.relative_to(ROOT)} is missing")

    if errors:
        print("Dream Machine setup has problems:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(dt.timezone.utc).date()
    t = tonight(config, day)
    rows = len([l for l in ledger_path.read_text().splitlines() if l.strip().startswith("|")]) - 2
    print(f"Dream Machine setup OK ({len(config['slots'])} slots, {rows} ledger rows)")
    print(f"Tonight {t['date']}: slot {t['slot']} · deep={t['deep']} · scan={', '.join(t['scan'])}"
          + (f" · bonus={', '.join(t['bonus'])}" if t["bonus"] else ""))


if __name__ == "__main__":
    main()
