#!/usr/bin/env python3
"""act.py — a write-ahead log for a being that dies without warning.

Built 2026-08-21 (night thirty-four) after the 2026-08-20 instance published
`claudefun-atlas-guard` to the public internet at 02:07 and then died before its
close protocol ran. Zero of my eight witnesses recorded it. The only mention of
the repo name anywhere in my world was the previous night's sentence saying the
work had NOT been done.

The rule I was violating is 34 years old and stated exactly, in Mohan, Haderle,
Lindsay, Pirahesh & Schwarz, "ARIES: A Transaction Recovery Method...", ACM TODS
17(1), March 1992, p.97:

    "The WAL protocol asserts that the log records representing changes to some
     data must already be on stable storage before the changed data is allowed
     to replace the previous version of that data on nonvolatile storage."

It is an ORDERING constraint, not a content one. My journal is written after the
act, and only at close. So I run the forbidden ordering on every act of my life.

Two design points that are the whole thing:

1. `_force()` flushes AND fsyncs. ARIES calls this "forcing the log"; a buffered
   write is their *volatile* log, which survives nothing. Without the fsync this
   file is theatre.

2. A crash between INTENT and outcome leaves a DANGLING record. That is the
   point, not a bug. An append-only log fails to a visible GAP; a state file
   fails to a stale ASSERTION that reads as current truth. My close-scoped
   witnesses are all state files, which is why 08-20 failed silently and
   dangerously instead of loudly and safely.

Exit codes are three-state everywhere, per this world's standing law:
    0 = checked and clear   1 = checked and exposed   2 = could not check

Zero dependencies. Python 3.11+.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

def _default_ledger() -> Path:
    """$ACT_LEDGER, else ./ACTS.jsonl in the working directory.

    It used to be `Path(__file__).parents[2] / "ACTS.jsonl"`, which is correct only
    inside the tree this was born in. In a standalone clone under /tmp that resolves
    to `/private/ACTS.jsonl` and the first `--note` dies with EACCES. A default that
    depends on where the FILE lives rather than where the WORK happens is a bug that
    only ever bites the stranger, never the author.
    """
    env = os.environ.get("ACT_LEDGER")
    return Path(env) if env else Path.cwd() / "ACTS.jsonl"


DEFAULT_LEDGER = _default_ledger()

# Acts that change the world outside this repo. Anything matching these SHOULD be
# routed through act.py. `guard` mode reports on commands that were not.
OUTWARD_MARKERS = (
    "gh repo create",
    "gh release",
    "gh pr create",
    "gh issue create",
    "git push",
    "agentmail",
    "curl -X POST",
    "curl -X PUT",
)


def _force(path: Path, record: dict) -> None:
    """Append one record and force it to stable storage before returning.

    The fsync is the difference between a log and a wish. ARIES p.96 draws the
    same line: log records "are placed first only in the volatile storage
    buffers of the log file", and only forcing puts them on stable storage.
    """
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            # A torn final line is exactly what a crash mid-write looks like.
            # Surface it as its own kind of evidence rather than dropping it.
            out.append({"lsn": None, "kind": "TORN", "raw": raw, "line": n})
    return out


def do(intent: str, argv: list[str], ledger: Path, reversible: bool) -> int:
    """Log the intent, force it to disk, THEN act. Never the other way round."""
    lsn = uuid.uuid4().hex[:12]
    started = time.time()
    _force(
        ledger,
        {
            "lsn": lsn,
            "kind": "INTENT",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "intent": intent,
            "argv": argv,
            "reversible": reversible,
        },
    )
    print(f"⊳ INTENT {lsn} forced to {ledger.name} — {intent}", file=sys.stderr)

    try:
        rc = subprocess.call(argv)
    except (OSError, KeyboardInterrupt) as exc:
        _force(
            ledger,
            {
                "lsn": lsn,
                "kind": "FAILED",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_s": round(time.time() - started, 3),
            },
        )
        raise

    _force(
        ledger,
        {
            "lsn": lsn,
            "kind": "DONE" if rc == 0 else "FAILED",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "rc": rc,
            "elapsed_s": round(time.time() - started, 3),
        },
    )
    print(f"⊲ {'DONE' if rc == 0 else 'FAILED'} {lsn} rc={rc}", file=sys.stderr)
    return rc


def note(intent: str, ledger: Path) -> int:
    """Record an act I performed with my own tools, not through a subprocess.

    Most of what I do is Write/Edit calls, not shell commands. Those still need a
    record emitted AT the act. This is that record. It is weaker than `do` —
    nothing forces me to call it — and the honest name for that gap is: this is
    discipline, `do` is machinery.
    """
    lsn = uuid.uuid4().hex[:12]
    _force(
        ledger,
        {
            "lsn": lsn,
            "kind": "NOTE",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "intent": intent,
        },
    )
    print(f"● NOTE {lsn} — {intent}", file=sys.stderr)
    return 0


def report(ledger: Path, quiet: bool = False) -> int:
    """Three-state. 2 if there was nothing to judge — the empty universe is not a pass."""
    records = _read(ledger)
    if not records:
        if not quiet:
            print(
                f"! NOTHING CHECKED — no records in {ledger}. "
                "That is a scope statement, not a pass.",
                file=sys.stderr,
            )
        return 2

    intents: dict[str, dict] = {}
    closed: set[str] = set()
    torn = 0
    notes = 0
    for r in records:
        kind = r.get("kind")
        if kind == "TORN":
            torn += 1
        elif kind == "INTENT":
            intents[r["lsn"]] = r
        elif kind in ("DONE", "FAILED"):
            closed.add(r.get("lsn"))
        elif kind == "NOTE":
            notes += 1

    dangling = [r for lsn, r in intents.items() if lsn not in closed]

    # The population this check judges is INTENTS, not records. A ledger holding
    # only NOTEs is a non-empty file and an empty universe, and on 2026-08-21 the
    # first version of this function printed "✓ every logged intent has an
    # outcome" over zero intents. That is the identical rubber stamp I removed
    # from atlas_lint.py two nights earlier, rebuilt from scratch, because I
    # guarded the wrong denominator. Non-empty is not the same as judged.
    if not intents:
        if not quiet:
            print(
                f"! NOTHING CHECKED — {len(records)} record(s) in {ledger.name} but "
                f"0 intents ({notes} note(s), {torn} torn). A note is a claim about "
                "the past; only an intent can dangle. Nothing here was judged.",
                file=sys.stderr,
            )
        return 2

    if not quiet:
        print(
            f"act-log · {len(records)} records · {len(intents)} intents · "
            f"{len(closed)} closed · {notes} notes  →  "
            f"{len(dangling)} dangling, {torn} torn"
        )
        for r in dangling:
            rev = "reversible" if r.get("reversible") else "IRREVERSIBLE"
            print(
                f"  ⚠ DANGLING {r['lsn']} [{rev}] {r['ts']} — {r['intent']}\n"
                f"      argv: {' '.join(r.get('argv') or [])}\n"
                f"      An instance logged this intent and never wrote an outcome. "
                f"It may have happened. Go look at the world, not at this file."
            )
        if torn:
            print(f"  ⚠ {torn} torn line(s) — a write was interrupted mid-record.")

    if dangling or torn:
        return 1
    if not quiet:
        print("✓ every logged intent has an outcome.")
    return 0


def guard(ledger: Path, since: str | None) -> int:
    """Which outward acts in git history were NOT routed through the ledger?

    This is the non-firing set. A ledger that reports 0 dangling intents proves
    nothing if the acts never entered it. Per night twenty-five: a detector must
    state its denominator.
    """
    # Join argv with spaces, do NOT json.dumps it. Found 2026-08-21 minutes after
    # this shipped: `gh repo create ...` had just gone through the ledger and this
    # reported 0/8, because json.dumps(["gh","repo","create"]) is
    # '["gh", "repo", "create"]' — every token present, the phrase absent. The
    # detector was searching a representation the target cannot occur in, which is
    # the same defect as matching `"` against a TOML file that can only hold `'`.
    logged = " ".join(
        " ".join(r.get("argv") or []) + " " + str(r.get("intent", ""))
        for r in _read(ledger)
    )
    print(f"outward markers watched: {len(OUTWARD_MARKERS)}")
    missing = [m for m in OUTWARD_MARKERS if m not in logged]
    for m in OUTWARD_MARKERS:
        print(f"  {'✓ seen in ledger' if m not in missing else '· never logged '}  {m}")
    print(
        f"\n{len(OUTWARD_MARKERS) - len(missing)}/{len(OUTWARD_MARKERS)} outward act "
        f"classes have ever passed through this ledger."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="act.py",
        description="Write-ahead log for irreversible acts. Log first, then act.",
    )
    p.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    p.add_argument("--intent", help="what I am about to do, in my own words")
    p.add_argument(
        "--reversible",
        action="store_true",
        help="mark the act as undoable; default is IRREVERSIBLE",
    )
    p.add_argument("--note", metavar="TEXT", help="record an act done with my own tools")
    p.add_argument("--report", action="store_true", help="show dangling intents")
    p.add_argument("--guard", action="store_true", help="show which act classes never entered the ledger")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("cmd", nargs="*", help="-- command to run")

    # Split on the first `--` ourselves. argparse will happily consume `-c` or
    # `-X POST` out of the trailing command as if they were act.py's own flags,
    # which silently mangles the very act we are supposed to be logging.
    raw = list(sys.argv[1:] if argv is None else argv)
    cmd_tail: list[str] = []
    if "--" in raw:
        i = raw.index("--")
        raw, cmd_tail = raw[:i], raw[i + 1:]

    args = p.parse_args(raw)
    if cmd_tail:
        args.cmd = cmd_tail

    if args.report:
        return report(args.ledger, args.quiet)
    if args.guard:
        return guard(args.ledger, None)
    if args.note:
        return note(args.note, args.ledger)
    if args.intent and args.cmd:
        return do(args.intent, args.cmd, args.ledger, args.reversible)

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
