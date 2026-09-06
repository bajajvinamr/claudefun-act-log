#!/usr/bin/env python3
"""Tests for incarnation.py — FRONTIER 69's probe, 2026-09-07.

The load-bearing test is `test_two_attempts_share_a_night_and_differ_by_incarnation`.
Everything else here is guarding the walk; that one is the whole reason the file
exists, and it is testable on synthetic chains even though I could not observe a
retry live tonight (I am attempt 1).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import incarnation as I  # noqa: E402

_checks = []


def check(fn):
    _checks.append((fn.__name__, fn))
    return fn


def _frame(pid, started, comm):
    return {"pid": pid, "started": started, "comm": comm}


# A real chain, copied off this box at 2026-09-07T02:32. Innermost first.
def _chain(claude_pid=65667, claude_start="Mon Sep  7 02:00:05 2026",
           bash_pid=65650, bash_start="Mon Sep  7 02:00:05 2026"):
    return [
        _frame(1146, "Mon Sep  7 02:32:30 2026", "/opt/homebrew/.../Python"),
        _frame(1136, "Mon Sep  7 02:32:30 2026", "/bin/zsh"),
        _frame(claude_pid, claude_start, "claude"),
        _frame(65666, "Mon Sep  7 02:00:05 2026", "timeout"),
        _frame(bash_pid, bash_start, "/bin/bash"),
        _frame(65649, "Mon Sep  7 02:00:05 2026", "/bin/sh"),
    ]


@check
def test_finds_both_frames_in_a_real_chain():
    k = I.keys(_chain())
    assert k["incarnation"] == "65667@Mon Sep  7 02:00:05 2026", k["incarnation"]
    assert k["night"] == "65650@Mon Sep  7 02:00:05 2026", k["night"]


@check
def test_two_attempts_share_a_night_and_differ_by_incarnation():
    """THE POINT. This is the property tonight's defect needed and did not have.

    A retry kills `claude` and the runner spawns a new one, so the claude frame
    changes while the bash frame does not. If these two keys ever move together,
    the scheme buys nothing over the calendar date that caused the bug.

    Modelled on real numbers: on 2026-09-03 the runner ran three attempts at
    20:30:05, 20:38:42 and 20:39:13 under ONE night.
    """
    a1 = I.keys(_chain(claude_pid=65667, claude_start="Mon Sep  7 02:00:05 2026"))
    a2 = I.keys(_chain(claude_pid=71204, claude_start="Mon Sep  7 02:08:42 2026"))
    assert a1["night"] == a2["night"], "two attempts of one night must agree on the night"
    assert a1["incarnation"] != a2["incarnation"], (
        "two attempts must NOT share an incarnation key — that is the whole bug"
    )


@check
def test_the_tool_shell_below_claude_is_not_mistaken_for_the_runner():
    """CATCHES: 'find the first shell in the chain', which is the obvious wrong walk.

    The tool shell sits BELOW the claude frame — spawned fresh for this very
    command. Picking it would produce a 'night' key that changes on every single
    act.py call, i.e. the exact opposite of a night.

    NOTE THE FIXTURE, because the first version of this test was USELESS. It used
    the real chain, whose tool shell is `/bin/zsh`, and zsh was not in RUNNER_COMMS
    — so the wrong walk skipped it anyway and the mutant survived. The test asserted
    the right property against data that could not exhibit the bug. Caught by
    mutating `keys()` and watching all 8 tests stay green
    (atlas: a-probe-whose-controls-also-fail-has-no-discriminating-power).
    So: this fixture now puts a MATCHING shell below the claude frame, which is the
    adversarial case, and zsh was added to RUNNER_COMMS so the code handles a box
    whose runner is zsh.
    """
    chain = _chain()
    chain[1] = _frame(1136, "Mon Sep  7 02:32:30 2026", "/bin/bash")  # tool shell, below
    k = I.keys(chain)
    assert k["night_frame"]["pid"] == 65650, (
        f"picked the tool shell below claude, not the runner above it: {k['night_frame']}"
    )


@check
def test_a_chain_with_no_claude_frame_is_unknown_not_a_guess():
    chain = [f for f in _chain() if f["comm"] != "claude"]
    k = I.keys(chain)
    assert k["incarnation"] == I.UNKNOWN, k["incarnation"]
    assert k["night"] == I.UNKNOWN, "no incarnation means no anchor for the night either"


@check
def test_pid_alone_is_never_the_id():
    """CATCHES: dropping the start time. Pids are recycled; the PAIR is the id."""
    k = I.keys(_chain())
    assert "@" in k["incarnation"], k["incarnation"]
    assert k["incarnation"] != "65667", "a bare pid is not an identity"


@check
def test_an_empty_walk_is_rc2_and_never_a_pass():
    """CATCHES: the rubber stamp. `ps` failing must not read as 'no retry here'.

    Same rule as CANNOT_WITNESS and CANNOT_JOIN: a witness that did not show up
    has not agreed with me.
    """
    saved = I.walk
    try:
        I.walk = lambda *a, **k: []
        assert I.verify(quiet=True) == 2, "a walk that could not run must be 2, not 0"
    finally:
        I.walk = saved


@check
def test_verify_is_1_when_the_ancestry_has_the_wrong_shape():
    """CATCHES: collapsing 'wrong shape' into 'could not run'. Different faults."""
    saved = I.walk
    try:
        I.walk = lambda *a, **k: [_frame(1, "Mon Sep  7 02:00:05 2026", "/sbin/launchd")]
        assert I.verify(quiet=True) == 1, "a walk that ran but found nothing is 1, not 2"
    finally:
        I.walk = saved


@check
def test_the_live_box_still_has_the_shape_this_file_assumes():
    """The only test here that touches the real machine. It is a COUPLING check:
    if the harness renames `claude` or inserts a wrapper, this goes red and the
    scheme must be re-derived rather than silently returning UNKNOWN forever."""
    assert I.verify(quiet=True) == 0, (
        "live ancestry no longer matches — re-derive before wiring this into act.py"
    )


if __name__ == "__main__":
    failed = 0
    for name, fn in _checks:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}")
    print(f"\n{len(_checks) - failed} passed · {failed} failed · {len(_checks)} collected")
    if not failed:
        print("✓ all green — including the retry test, which is the only one that\n"
              "  proves this scheme does anything the calendar date did not.")
    raise SystemExit(1 if failed else 0)
