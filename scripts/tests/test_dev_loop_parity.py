"""`make test` and Tilt's unit-tests button must run the same suites.

Tilt re-runs unit-tests on every save; if it skips a suite that `make test` (and the
nightly scoreboard) runs, a local edit can look green and still fail there.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# "<uv --with... arg> pytest -q <suite>" in both the Makefile and the Tiltfile.
SUITE = re.compile(r"(--with(?:-requirements)? \S+) pytest -q ([\w./-]+)")


def make_test_suites():
    recipe = re.search(r"^test:.*\n((?:\t.*\n)+)", (ROOT / "Makefile").read_text(), re.M)
    assert recipe, "no test: target in Makefile"
    return {suite: deps for deps, suite in SUITE.findall(recipe.group(1))}


def tilt_unit_tests():
    block = re.search(r"local_resource\(\s*'unit-tests',(.*?)\n\)", (ROOT / "Tiltfile").read_text(), re.S)
    assert block, "no unit-tests local_resource in Tiltfile"
    cmd = re.search(r"cmd=(.*?)\n\s*deps=", block.group(1), re.S).group(1)
    watched = re.findall(r"'([^']+)'", re.search(r"deps=\[(.*?)\]", block.group(1), re.S).group(1))
    return {suite: deps for deps, suite in SUITE.findall(cmd)}, watched


def test_make_test_runs_at_least_one_suite():
    assert len(make_test_suites()) >= 1


def test_tilt_runs_every_make_test_suite_the_same_way():
    tilt_suites, _ = tilt_unit_tests()
    assert tilt_suites == make_test_suites()


def test_tilt_reruns_when_a_suite_changes():
    _, watched = tilt_unit_tests()
    missing = [s for s in make_test_suites() if not any(s == w or s.startswith(w + "/") for w in watched)]
    assert missing == [], f"Tilt unit-tests does not watch {missing}"
