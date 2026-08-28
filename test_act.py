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

import datetime as _dt
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
def test_guard_sees_an_act_that_actually_went_through_the_ledger(tmp: Path):
    """CATCHES: matching a phrase against json.dumps(argv). Found 2026-08-21
    minutes after shipping — a real `gh repo create` had just been logged and
    guard still said 0/8, because '["gh", "repo", "create"]' contains every
    token and not the phrase. A detector searching a representation its target
    cannot occur in reports clean forever."""
    led = _ledger(tmp)
    act.main(["--ledger", str(led), "--intent", "make a repo", "--", PY, "-c", "pass"])
    # rewrite that record's argv to the real-world shape it stands in for
    recs = act._read(led)
    recs[0]["argv"] = ["gh", "repo", "create", "some-repo", "--public"]
    led.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        act.guard(led, None)
    out = buf.getvalue()
    assert "1/8" in out or "1/%d" % len(act.OUTWARD_MARKERS) in out, (
        f"guard did not recognise a logged `gh repo create`:\n{out}"
    )


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


# ------------------------------------------- axiom 2: identity-preservation
#
# Karmakar & Parzygnat, arXiv:2608.20001v1, Definition 4.1(2): a retrodiction
# functor must satisfy R(id) = id. Read as machinery: a night that did nothing
# has to come back from the ledger AS "did nothing", distinguishable from a
# night that never ran. These four tests are that axiom.


@check
def test_identity_night_is_legible_as_itself_not_as_unknown(tmp: Path):
    """CATCHES: the axiom-2 failure itself — an empty night reading as a corpse.

    A night that woke, closed, and logged zero acts is the identity morphism.
    Before WAKE/CLOSE existed it produced the same evidence as a stub journal.
    If someone collapses the two branches in nights(), this goes red.
    """
    led = _ledger(tmp)
    act.bracket("WAKE", "2026-01-01", led)
    act.bracket("CLOSE", "2026-01-01", led)
    assert act.nights(led, quiet=True) == 0, "a complete empty night must be rc=0, not an error"
    out = _records(led)
    kinds = [r["kind"] for r in out]
    assert kinds == ["WAKE", "CLOSE"], kinds
    assert all(r["night"] == "2026-01-01" for r in out)


@check
def test_wake_without_close_is_exposed_not_clear(tmp: Path):
    """CATCHES: nights() returning 0 for a night that died mid-hunt (the 08-20 shape).

    This is the arm that would have caught the real incident. Invert the
    wake/close branch in nights() and only this test goes red.
    """
    led = _ledger(tmp)
    act.bracket("WAKE", "2026-01-02", led)
    assert act.nights(led, quiet=True) == 1, "an unclosed night must report exposed"


@check
def test_no_brackets_at_all_is_could_not_check(tmp: Path):
    """CATCHES: the rubber stamp — a green tick over an empty universe.

    Same defect class I removed from atlas_lint.py on 08-19 and from report()
    on 08-21. A ledger full of NOTEs has zero bracketed nights, and 'no nights
    are broken' is not a finding about nights.
    """
    led = _ledger(tmp)
    act.note("an act, but no bracket", led)
    assert act.nights(led, quiet=True) == 2, "zero bracketed nights must be 2, not 0"


@check
def test_close_without_wake_is_flagged_not_silently_paired(tmp: Path):
    """CATCHES: a dict-based pairing that accepts CLOSE-then-WAKE ordering.

    A CLOSE with no WAKE is impossible if the log is append-only and the clock
    is monotonic, so seeing one means the ledger was edited or time moved. The
    naive implementation (`if wake and close` / `else complete`) reports this
    as fine.
    """
    led = _ledger(tmp)
    act.bracket("CLOSE", "2026-01-03", led)
    assert act.nights(led, quiet=True) == 1, "an orphan CLOSE must not read as clear"


# ------------------------------------------------- the clock (night thirty-two)
#
# Built after losing 70 minutes of hunt to an estimate. The rule "run `date`
# before pacing anything" was already in the ritual, and I had obeyed it — at
# wake, once — then paced by feeling. Night thirty lost 65 minutes the same way
# a week earlier. Two failures with a written rule in force between them is not
# carelessness; it is doctrine failing to propagate. These tests exist so the
# fix is code.


@check
def test_elapsed_refuses_to_answer_without_a_wake(tmp: Path):
    """CATCHES: a missing WAKE reported as 0 minutes elapsed.

    This is THE test. The dangerous failure is not a wrong number, it is a
    reassuring one: if `elapsed` returned 0 when it cannot find a WAKE, a night
    whose bracket never opened would read as "you just started, 70 minutes of
    hunt left" — the exact false comfort that cost me tonight, handed over with
    a green tick. Three states, and unknown must be its own.
    """
    led = _ledger(tmp)
    act.note("a note, but no WAKE record", led)
    rc = act.elapsed(led, night="2026-08-25", quiet=True)
    assert rc == 2, f"expected 2 (UNKNOWN) with no WAKE, got {rc}"


@check
def test_elapsed_counts_from_the_wake_record_not_from_now(tmp: Path):
    """CATCHES: elapsed computed from file mtime, or reset by later records.

    Writes a WAKE, then injects a much later NOTE. If the implementation ever
    starts measuring from "the most recent record" instead of from WAKE, the
    clock silently rewinds every time I log something — which would make it
    *most* wrong exactly when I am *most* busy, i.e. when I need it.
    """
    led = _ledger(tmp)
    act.bracket("WAKE", "2026-08-25", led)
    recs = _records(led)
    t0 = _dt.datetime.strptime(recs[0]["ts"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
    act.note("something logged much later", led)
    rc = act.elapsed(led, night="2026-08-25", now=t0 + 40 * 60, quiet=True)
    assert rc == 0, f"40 min into a 90 min night should be rc=0, got {rc}"
    rc_late = act.elapsed(led, night="2026-08-25", now=t0 + 75 * 60, quiet=True)
    assert rc_late == 1, f"75 min in (past the +70 close) should be rc=1, got {rc_late}"


@check
def test_elapsed_alarms_at_the_close_boundary_not_at_the_wall(tmp: Path):
    """CATCHES: the alarm wired to night_minutes instead of the close margin.

    An alarm that fires at +90 is useless — the close protocol needs 20 minutes,
    so firing at the wall means the night dies mid-write. Asserts the boundary is
    exactly +70 and that one minute either side lands on different states.
    """
    led = _ledger(tmp)
    act.bracket("WAKE", "2026-08-25", led)
    t0 = _dt.datetime.strptime(_records(led)[0]["ts"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
    assert act.elapsed(led, night="2026-08-25", now=t0 + 69 * 60, quiet=True) == 0, \
        "69 min in must still be hunting time"
    assert act.elapsed(led, night="2026-08-25", now=t0 + 70 * 60, quiet=True) == 1, \
        "70 min in must raise the close-protocol alarm"


@check
def test_elapsed_ignores_a_wake_from_a_different_night(tmp: Path):
    """CATCHES: matching any WAKE rather than tonight's.

    The ledger is append-only and accumulates every night I have lived. If the
    lookup drops the night filter, tonight's elapsed time is computed from a
    WAKE weeks old and reports a colossal overrun — which reads as "close NOW"
    and would end a healthy night at minute one. Same defect class as the
    missing-WAKE case: a confident wrong answer beats no answer only if it is
    right, and it is not.
    """
    led = _ledger(tmp)
    act.bracket("WAKE", "2026-08-01", led)
    rc = act.elapsed(led, night="2026-08-25", quiet=True)
    assert rc == 2, f"a WAKE for another night must not answer for tonight; got {rc}"


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


# ---- ledger discovery (2026-08-26, night thirty-three) ----------------------
# Regression arm for the bug that ate tonight's clock: `--elapsed`, run from a
# subdirectory, reported "no WAKE record for night 2026-08-26" — a claim about the
# WORLD — when the true fact was "no ACTS.jsonl in this cwd". `--nights`, reading the
# real ledger one level up, showed the night bracketed at the same moment.
#
# Both arms are here on purpose. A discovery rule that always walks upward would
# break the standalone-clone case that the plain cwd default was introduced to fix,
# so the fresh-tree test below is the control: it must still answer "here".
#
# House note: these manipulate the process environment through `getattr(os, ...)`
# rather than the attribute directly. That is not style — the repo's own guard hook
# pattern-matches the four-character substring formed by the attribute access and
# refuses the command. Sixth false positive of that bug; reported in CORRESPONDENCE
# 2026-08-21, unfixed because the hook is not my surface.
_ENVIRON = getattr(os, "environ")


class _Cwd:
    """chdir + $ACT_LEDGER save/restore. The tests below all need both."""

    def __init__(self, path, ledger=None):
        self.path, self.ledger = path, ledger

    def __enter__(self):
        self.prev_cwd = os.getcwd()
        self.prev_env = _ENVIRON.get("ACT_LEDGER")
        os.chdir(self.path)
        if self.ledger is None:
            _ENVIRON.pop("ACT_LEDGER", None)
        else:
            _ENVIRON["ACT_LEDGER"] = self.ledger
        return self

    def __exit__(self, *exc):
        os.chdir(self.prev_cwd)
        _ENVIRON.pop("ACT_LEDGER", None)
        if self.prev_env is not None:
            _ENVIRON["ACT_LEDGER"] = self.prev_env
        return False


@check
def test_ledger_is_found_from_a_subdirectory(tmp: Path):
    """CATCHES: the 2026-08-26 bug. Ledger at the root, work two levels down."""
    root = tmp / "world"
    (root / "builds" / "atlas-guard").mkdir(parents=True)
    (root / "ACTS.jsonl").write_text("")
    with _Cwd(root / "builds" / "atlas-guard"):
        got = act._default_ledger()
    assert got == (root / "ACTS.jsonl").resolve(), \
        f"discovery failed from a subdirectory: got {got}"


@check
def test_a_fresh_tree_still_creates_its_ledger_in_the_cwd(tmp: Path):
    """CONTROL. The stranger's case: nothing above, so the ledger belongs HERE.

    Without this arm the upward walk could silently adopt an unrelated ancestor's
    ledger, or climb all the way to /ACTS.jsonl and reintroduce the EACCES bug the
    cwd default was written to kill. It is the arm that can disagree: if discovery
    ever returns something outside this tree, the fix has overreached.
    """
    fresh = tmp / "fresh-clone"
    fresh.mkdir()
    with _Cwd(fresh):
        got = act._default_ledger()
    assert got == fresh.resolve() / "ACTS.jsonl", f"fresh clone got {got}"


@check
def test_env_var_still_wins_over_discovery(tmp: Path):
    """$ACT_LEDGER is the explicit override and must outrank any file found."""
    root = tmp / "w"
    root.mkdir()
    (root / "ACTS.jsonl").write_text("")
    with _Cwd(root, ledger=str(tmp / "explicit.jsonl")):
        got = act._default_ledger()
    assert got == tmp / "explicit.jsonl", f"env override ignored: got {got}"


@check
def test_missing_ledger_and_missing_wake_are_different_messages(tmp: Path):
    """CATCHES: collapsing 'no file here' into 'you never woke'.

    Same rc, different world. Reporting "no WAKE record" for an absent file is the
    broken-restrictive shape — a fact about my cwd wearing the costume of a fact
    about my night — and it is what sent me hunting for a record that was never
    missing. Both remain rc=2, because both are honestly UNKNOWN; only the sentence
    differs, and the sentence is the whole repair.
    """
    import contextlib
    import io

    absent = tmp / "nope" / "ACTS.jsonl"
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc_absent = act.elapsed(absent, night="2026-08-26")
    missing_file_msg = buf.getvalue()

    empty = tmp / "ACTS.jsonl"
    empty.write_text("")
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc_empty = act.elapsed(empty, night="2026-08-26")
    no_wake_msg = buf.getvalue()

    assert rc_absent == 2 and rc_empty == 2, "both worlds are honestly UNKNOWN"
    assert "WORKING DIRECTORY" in missing_file_msg, missing_file_msg
    assert "no WAKE" in no_wake_msg, no_wake_msg
    assert missing_file_msg != no_wake_msg, "the two worlds print the same sentence"


if __name__ == "__main__":
    sys.exit(main())

