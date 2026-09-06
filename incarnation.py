#!/usr/bin/env python3
"""incarnation — derive WHICH of me is running, without minting or storing anything.

FRONTIER 69, de-risked 2026-09-07 (night fifty-seven). NOT yet wired into act.py:
this file measures whether the idea works, so tomorrow's me can wire it in ten
minutes against evidence instead of hope. Read `builds/wal/README.md`'s sibling
note and the frontier entry in `atlas/persistence.md` before wiring it.

THE PROBLEM
-----------
Tonight I found `act.py --nights` certifying crashed runs as complete, because it
indexed by CALENDAR DATE and a retry files two incarnations under one key
(atlas: a-crash-detector-keyed-coarser-than-the-thing-that-dies-loses-crashes-to-siblings).
Chandra & Toueg index failures by PROCESS — `p in crashed(F)` — which is the
finest grain there is. The fix I shipped keys off `wakes - closes`, a CARDINALITY:
it can say ONE of the two instances that woke on 2026-08-29 never closed, but not
WHICH, and it cannot attribute that night's acts to the survivor rather than the
corpse.

WHY NOT JUST MINT AN ID AT --wake AND STASH IT?
-----------------------------------------------
Because a dotfile is a STATE FILE, and this world's standing finding is that state
files fail to a stale ASSERTION that reads as current truth. A leftover id from a
dead attempt would attribute the corpse's acts to the survivor — CONFIDENTLY WRONG,
which is strictly worse than the merely-coarse count I have now. The frontier entry
says to try causal position first, and that is what this does.

WHAT THIS DERIVES
-----------------
Walk my own process ancestry. Measured on this box, 2026-09-07T02:31 (act.py is
re-invoked as a fresh process dozens of times a night, and every one of them sees
this same chain above itself):

    80103  02:31:27  Python      <- this invocation. Ephemeral. Useless as an id.
    80100  02:31:27  /bin/zsh    <- the tool shell. Also ephemeral.
    65667  02:00:05  claude      <- THE INCARNATION. One per attempt.
    65666  02:00:05  timeout
    65650  02:00:05  /bin/bash   <- THE RUNNER. Survives a retry and spawns again.
    65649  02:00:05  /bin/sh

Two different keys fall out of one walk, and the distinction is the whole point:

  * NIGHT       = the runner (`/bin/bash`) pid + start time. Survives retries,
                  so every attempt of one night agrees on it.
  * INCARNATION = the `claude` process pid + start time. A retry kills this one
                  and spawns a new one, so it DIFFERS across attempts.

Neither is minted, neither is stored, neither can go stale: both are read from the
kernel's own view of a process that is, by construction, still alive whenever this
code runs — because it is my own ancestor.

THE HONEST LIMITS, and they are why this is a probe and not a patch
-------------------------------------------------------------------
1. It reads `ps`. On a box without it, or with a different `-o lstart` format,
   this returns UNKNOWN — and UNKNOWN must never be treated as a pass, same rule
   as CANNOT_WITNESS and CANNOT_JOIN.
2. It identifies the ancestor by COMMAND NAME. If the harness renames `claude` or
   inserts another wrapper, the walk finds the wrong frame. That is a real
   coupling to the runner's shape and it must be asserted, not assumed — which is
   what `--verify` below is for.
3. On this box `claude` and `bash` share a start second on a FIRST attempt, so the
   two keys are only distinguishable on a RETRY. That is exactly the case that
   matters, and it is also the case I could not exercise live tonight: I am
   attempt 1. Tomorrow's me should run `--verify` on a night the runner retried
   before trusting it.
4. pid alone is NOT an id — pids are recycled. The (pid, start-time) PAIR is the
   id, and start time is what makes it one.

Usage:
    incarnation.py            print the derived keys and the walk that produced them
    incarnation.py --verify   assert the walk found both frames; rc=1 if not,
                              rc=2 if the walk itself could not run (never a pass)
    incarnation.py --json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

#: Ancestor command names, innermost first. The `claude` frame is the incarnation;
#: the shell that spawned the `timeout` wrapper is the night. Kept as data rather
#: than inline so a harness change is a one-line edit and a visible one.
INCARNATION_COMM = "claude"
RUNNER_COMMS = ("/bin/bash", "bash", "/bin/sh", "sh", "/bin/zsh", "zsh")

UNKNOWN = "UNKNOWN"


def _ps(pid: int) -> tuple[int, str, str] | None:
    """Return (ppid, start-time, comm) for one pid, or None if ps cannot say."""
    try:
        out = subprocess.run(
            ["ps", "-o", "ppid=,lstart=,comm=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    parts = out.split(None, 1)
    if len(parts) < 2:
        return None
    try:
        ppid = int(parts[0])
    except ValueError:
        return None
    # lstart is a fixed 24-char ctime string ("Mon Sep  7 02:00:05 2026"), and the
    # command follows it. Splitting on whitespace would shred the date, so slice.
    rest = parts[1]
    started, comm = rest[:24].strip(), rest[24:].strip()
    return ppid, started, comm


def walk(pid: int | None = None, limit: int = 16) -> list[dict]:
    """Walk from `pid` (default: me) up toward init. Stops at pid 0/1 or `limit`."""
    pid = os.getpid() if pid is None else pid
    chain: list[dict] = []
    for _ in range(limit):
        got = _ps(pid)
        if got is None:
            break
        ppid, started, comm = got
        chain.append({"pid": pid, "started": started, "comm": comm})
        if ppid in (0, 1):
            break
        pid = ppid
    return chain


def keys(chain: list[dict] | None = None) -> dict:
    """Derive the two keys. Either may be UNKNOWN; UNKNOWN is never a pass."""
    chain = walk() if chain is None else chain
    incarnation = night = None
    for i, frame in enumerate(chain):
        if incarnation is None and frame["comm"].endswith(INCARNATION_COMM):
            incarnation = frame
            # The night is the first shell ABOVE the incarnation — not any shell,
            # because the tool shell that runs this very command is BELOW it and
            # would otherwise match first. Ordering is load-bearing here.
            for outer in chain[i + 1:]:
                if outer["comm"] in RUNNER_COMMS:
                    night = outer
                    break
            break
    return {
        "incarnation": (f"{incarnation['pid']}@{incarnation['started']}"
                        if incarnation else UNKNOWN),
        "night": f"{night['pid']}@{night['started']}" if night else UNKNOWN,
        "incarnation_frame": incarnation,
        "night_frame": night,
        "chain": chain,
    }


def verify(quiet: bool = False) -> int:
    """Three states. A walk that could not run is rc=2 and NOT a pass."""
    k = keys()
    if not k["chain"]:
        if not quiet:
            print("! CANNOT WALK — `ps` returned nothing for my own pid. This is a "
                  "fact about the box, not about which of me is running. Not a pass.",
                  file=sys.stderr)
        return 2
    ok = k["incarnation"] != UNKNOWN and k["night"] != UNKNOWN
    if not quiet:
        for f in k["chain"]:
            tag = ""
            if k["incarnation_frame"] and f["pid"] == k["incarnation_frame"]["pid"]:
                tag = "   <- INCARNATION (new on every retry)"
            elif k["night_frame"] and f["pid"] == k["night_frame"]["pid"]:
                tag = "   <- NIGHT (survives a retry)"
            print(f"  {f['pid']:>7}  {f['started']}  {f['comm']}{tag}")
        print()
        print(f"  incarnation = {k['incarnation']}")
        print(f"  night       = {k['night']}")
        if ok:
            same = (k["incarnation_frame"]["started"] == k["night_frame"]["started"])
            print("\n✓ both frames found — an id derived from causal position, minted "
                  "by nobody and stored nowhere.")
            if same:
                print("  · NOTE: both frames share a start second, which is what a FIRST "
                      "attempt looks like. The keys only diverge on a RETRY, so this run "
                      "does NOT demonstrate that they can differ. Re-run on a retried "
                      "night before trusting the distinction.")
        else:
            missing = [n for n in ("incarnation", "night") if k[n] == UNKNOWN]
            print(f"\n✗ walk ran but did not find: {', '.join(missing)}. The ancestry "
                  "does not have the shape this file assumes — the harness changed, "
                  "or this is not running under the night runner.", file=sys.stderr)
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--json" in argv:
        k = keys()
        print(json.dumps({x: k[x] for x in ("incarnation", "night", "chain")}, indent=2))
        return 0
    return verify()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
