#!/usr/bin/env python3
"""Tests for act.py — the write-ahead log.

Run: python3 test_act.py

The load-bearing test is `test_intent_is_on_disk_before_the_command_runs`. It is
the only one that tests the ARIES cardinal rule itself rather than its
consequences: it hands the wrapper a command that READS THE LEDGER, and asserts
the command can see its own intent. Move the fsync below subprocess.call and
every other test in this file still passes.

Each test names the defect it would catch. A test that cannot say what breaking
it would look like is decoration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import act  # noqa: E402

# No test framework, on purpose: this file must run for a stranger with bare
# Python and nothing installed. Its runner is at the bottom, ~30 lines, and it
# returns 2 when it collects nothing.
PY = sys.executable
ACT = str(HERE / "act.py")

_checks: list[tuple[str, callable]] = []


def check(fn):
    _checks.append((fn.__name__, fn))
    return fn


def _ledger(tmp: Path) -> Path:
    return tmp / "acts.jsonl"


def _records(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------- the cardinal rule


@check
def test_intent_is_on_disk_before_the_command_runs(tmp: Path):
    """CATCHES: fsync/append moved to after subprocess.call — i.e. WAL inverted.

    The command run BY the wrapper reads the ledger. If the ordering is right it
    finds its own INTENT already forced to stable storage. If someone 'optimises'
    act.py to log after acting, this is the only test that goes red.
    """
    led = _ledger(tmp)
    probe = tmp / "probe.py"
    probe.write_text(
        "import json,sys,pathlib\n"
        "recs=[json.loads(l) for l in pathlib.Path(sys.argv[1]).read_text().splitlines() if l.strip()]\n"
        "sys.exit(0 if any(r.get('kind')=='INTENT' for r in recs) else 7)\n"
    )
    rc = act.main(
        ["--ledger", str(led), "--intent", "self-observing act", "--", PY, str(probe), str(led)]
    )
    assert rc == 0, f"the running command could not see its own INTENT (rc={rc}) — log is written after the act"


@check
def test_fsync_is_actually_called(tmp: Path):
    """CATCHES: flush() without fsync() — a volatile log that survives no crash."""
    led = _ledger(tmp)
    seen = []
    real = os.fsync
    os.fsync = lambda fd: (seen.append(fd), real(fd))[1]
    try:
        act.note("forced?", led)
    finally:
        os.fsync = real
    assert len(seen) == 1, f"expected exactly one fsync, saw {len(seen)}"


# ---------------------------------------------------------------- the crash case


@check
def test_sigkill_between_intent_and_outcome_leaves_a_dangling_record(tmp: Path):
    """CATCHES: the whole point. A death mid-act must leave visible evidence."""
    led = _ledger(tmp)
    p = subprocess.Popen(
        [PY, ACT, "--ledger", str(led), "--intent", "irreversible thing", "--", "sleep", "20"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 5
    while time.time() < deadline and not led.exists():
        time.sleep(0.02)
    p.kill()
    p.wait(timeout=5)

    recs = _records(led)
    kinds = [r["kind"] for r in recs]
    assert "INTENT" in kinds, "SIGKILL destroyed the intent record — it was never forced"
    assert "DONE" not in kinds and "FAILED" not in kinds, "an outcome was written after a kill -9?"
    assert act.report(led, quiet=True) == 1, "a dangling intent must exit 1 (checked and exposed)"


@check
def test_dangling_report_names_the_act_and_its_reversibility(tmp: Path):
    """CATCHES: a non-localising diagnostic. Per night thirty: a control that
    detects but cannot localise accuses the innocent. The report must say WHICH
    act dangled and whether it can be undone."""
    led = _ledger(tmp)
    act._force(led, {"lsn": "abc123", "kind": "INTENT", "ts": "2026-08-21T02:00:00+0530",
                     "intent": "publish the repo", "argv": ["gh", "repo", "create"], "reversible": False})
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = act.report(led)
    out = buf.getvalue()
    assert rc == 1
    assert "abc123" in out, "report does not name the dangling act"
    assert "publish the repo" in out, "report does not state what was intended"
    assert "IRREVERSIBLE" in out, "report does not flag irreversibility"


# ---------------------------------------------------------------- three states


@check
def test_missing_ledger_is_could_not_check_not_pass(tmp: Path):
    """CATCHES: two-state exit codes. Absent evidence is not clean evidence."""
    assert act.report(tmp / "does-not-exist.jsonl", quiet=True) == 2


@check
def test_records_but_zero_intents_is_could_not_check(tmp: Path):
    """CATCHES: the exact bug this tool shipped with on 2026-08-21.

    A ledger holding only NOTEs is a non-empty FILE and an empty UNIVERSE. The
    first version printed '✓ every logged intent has an outcome' and returned 0.
    Revert the `if not intents` guard in act.report and this test goes red alone.
    """
    led = _ledger(tmp)
    act.note("a thing I did with my own hands", led)
    assert len(_records(led)) == 1, "precondition: the file is genuinely non-empty"
    assert act.report(led, quiet=True) == 2, "non-empty file with 0 intents must be 'could not check'"


@check
def test_completed_intent_is_clear(tmp: Path):
    """CATCHES: a gate that can never return 0 — a control that cannot succeed."""
    led = _ledger(tmp)
    assert act.main(["--ledger", str(led), "--intent", "harmless", "--", PY, "-c", "pass"]) == 0
    assert act.report(led, quiet=True) == 0


@check
def test_failing_command_still_closes_its_intent(tmp: Path):
    """CATCHES: treating a failed act as a dangling one. A command that ran and
    returned 3 is CLOSED — we know what happened. Only silence dangles."""
    led = _ledger(tmp)
    rc = act.main(["--ledger", str(led), "--intent", "doomed", "--", PY, "-c", "import sys;sys.exit(3)"])
    assert rc == 3, f"wrapper must propagate the command's exit code, got {rc}"
    kinds = [r["kind"] for r in _records(led)]
    assert kinds == ["INTENT", "FAILED"], kinds
    assert act.report(led, quiet=True) == 0, "a known failure is not an unknown outcome"


@check
def test_torn_final_line_is_surfaced_not_swallowed(tmp: Path):
    """CATCHES: silently dropping unparseable lines. A half-written record is
    precisely what a crash mid-fsync looks like; dropping it hides the crash."""
    led = _ledger(tmp)
    act.main(["--ledger", str(led), "--intent", "ok", "--", PY, "-c", "pass"])
    with open(led, "a") as f:
        f.write('{"lsn": "trunc", "kind": "INT')  # no newline, no closing brace
    assert act.report(led, quiet=True) == 1, "a torn record must expose, not pass"
    assert any(r["kind"] == "TORN" for r in act._read(led))


@check
def test_guard_states_its_denominator(tmp: Path):
    """CATCHES: '0 findings' vs '0 findings across 0 candidates' (night 25)."""
    led = _ledger(tmp)
    act.note("seed", led)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        act.guard(led, None)
    out = buf.getvalue()
    assert str(len(act.OUTWARD_MARKERS)) in out, "guard must print how many classes it watched"
    assert "/" in out, "guard must print a ratio, not a bare count"


@check
def test_default_ledger_does_not_depend_on_where_the_file_lives(tmp: Path):
    """CATCHES: the /private/ACTS.jsonl bug found 2026-08-21. A default derived
    from __file__ is correct only in the tree the tool was born in; a stranger's
    clone under /tmp resolved parents[2] to an unwritable root and the first
    --note died with EACCES. A default that depends on where the FILE lives
    rather than where the WORK happens only ever bites the stranger."""
    env = dict(os.environ)
    env.pop("ACT_LEDGER", None)
    r = subprocess.run(
        [PY, ACT, "--note", "from a strange cwd"],
        cwd=tmp, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"note failed from an unfamiliar cwd: {r.stderr.strip()}"
    assert (tmp / "ACTS.jsonl").exists(), "ledger did not land in the working directory"

    env["ACT_LEDGER"] = str(tmp / "custom.jsonl")
    subprocess.run([PY, ACT, "--note", "env override"], cwd=tmp, env=env, capture_output=True)
    assert (tmp / "custom.jsonl").exists(), "$ACT_LEDGER was ignored"


# ---------------------------------------------------------------- runner


def main() -> int:
    import shutil
    import tempfile

    passed, failed = 0, []
    if not _checks:
        print("! NOTHING CHECKED — 0 tests collected", file=sys.stderr)
        return 2
    for name, fn in _checks:
        tmp = Path(tempfile.mkdtemp(prefix="wal-test-"))
        try:
            fn(tmp)
            passed += 1
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    for name, msg in failed:
        print(f"  ✗ {name}\n      {msg}")
    print(f"\n{passed} passed · {len(failed)} failed · {len(_checks)} collected")
    if failed:
        return 1
    print("✓ all green — including the ordering test, which is the only one that")
    print("  goes red if the log is written after the act instead of before it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
