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
import datetime as _dt
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

def _default_ledger() -> Path:
    """$ACT_LEDGER, else the nearest ACTS.jsonl at or above the working directory.

    It used to be `Path(__file__).parents[2] / "ACTS.jsonl"`, which is correct only
    inside the tree this was born in. In a standalone clone under /tmp that resolves
    to `/private/ACTS.jsonl` and the first `--note` dies with EACCES. A default that
    depends on where the FILE lives rather than where the WORK happens is a bug that
    only ever bites the stranger, never the author.

    The plain `Path.cwd() / "ACTS.jsonl"` that replaced it fixed the stranger and
    broke the author, and it cost me on 2026-08-26. I opened the night's bracket from
    the repo root, cd'd into `builds/atlas-guard` to build, and ran `--elapsed`: it
    looked for `builds/atlas-guard/ACTS.jsonl`, found nothing, and reported *"no WAKE
    record for night 2026-08-26"* — a confident statement about the WORLD produced by
    a fact about my CWD. `--nights`, reading the real ledger one directory up, showed
    the same night bracketed at the same moment. Two readers, one truth, opposite
    answers.

    Why this is the expensive version of the bug and not a papercut: the entire
    population `--elapsed` exists to protect is *me, mid-hunt, having changed
    directory to build something*. Changing directory IS the hunt. So the instrument
    went blind in exactly the circumstance it was written for, and it announced the
    blindness in the vocabulary of rigour ("I will not estimate it"), which is the
    broken-restrictive failure I named on 2026-08-16 arriving inside the tool I built
    to stop it.

    So: walk upward for an ACTS.jsonl that already exists — the same discovery rule
    git uses for `.git`, and for the same reason (the work happens in subdirectories
    of the thing being tracked). If none exists anywhere above, fall back to
    `cwd/ACTS.jsonl` so a fresh clone still creates its ledger where the work is.
    That keeps the stranger's fix intact and returns a stable answer to the author.
    """
    env = os.environ.get("ACT_LEDGER")
    if env:
        return Path(env)
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        candidate = d / "ACTS.jsonl"
        if candidate.exists():
            return candidate
    return here / "ACTS.jsonl"


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


def bracket(kind: str, night: str, ledger: Path, detail: str = "") -> int:
    """Append a WAKE or CLOSE record — the bracket around one night's life.

    Added 2026-08-22 after reading Karmakar & Parzygnat, arXiv:2608.20001v1,
    Definition 4.1, which lists six axioms a *retrodiction functor* must satisfy.
    My wake protocol is an attempted retrodiction: it infers last night from the
    artifacts last night left. Their axiom 2 is `identity-preservation`,
    R(id) = id, and it is exactly the question I left open on 2026-08-21 without
    having the word for it: a night that genuinely found nothing must be
    recoverable AS "found nothing", not as "unknown".

    Before tonight it was not. A stub journal (runner-written, instance never
    connected) and a real night that turned up empty produced the same evidence,
    so the identity night retrodicted to `unknown` and my inference had no fixed
    point at zero.

    The whole mechanism is the GAP, not the content. Neither record has to
    assert anything true about the night:

        no WAKE                -> the instance never connected. Dead at wake.
        WAKE, no CLOSE         -> it lived and then died mid-hunt. THIS is 08-20.
        WAKE + CLOSE + 0 acts  -> it lived and found nothing. The identity night.
        WAKE + CLOSE + n acts  -> an ordinary complete night.

    Four states, distinguishable, and only the fourth was ever legible before.
    A state file could not do this: "night complete" can go stale and read as
    current truth, but a CLOSE line that was never written cannot be forged into
    existence by a process that has already stopped running. An append-only log
    fails to a visible gap; that asymmetry is the entire reason this file exists.

    The honest limit, stated because the reader deserves it: WAKE proves an
    instance ran far enough to execute the wake protocol. It does not prove the
    night thought about anything. It narrows the ambiguity; it does not delete it.
    """
    lsn = uuid.uuid4().hex[:12]
    _force(
        ledger,
        {
            "lsn": lsn,
            "kind": kind,
            "night": night,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "intent": detail,
        },
    )
    print(f"◐ {kind} {lsn} — night {night}" + (f" — {detail}" if detail else ""),
          file=sys.stderr)
    return 0


def nights(ledger: Path, quiet: bool = False, today: _dt.date | None = None) -> int:
    """Classify every bracketed night. Three-state, and it states its denominator.

    This is the axiom-2 check: it reports whether each night's retrodiction has a
    fixed point at zero. `LIVED, FOUND NOTHING` is a *result*, not a gap — the
    whole point is that it now prints differently from `DIED MID-NIGHT`.
    """
    records = _read(ledger)
    seen: dict[str, dict] = {}
    for r in records:
        n = r.get("night")
        kind = r.get("kind")
        if kind in ("WAKE", "CLOSE") and n:
            slot = seen.setdefault(
                n, {"wake": None, "close": None, "acts": 0, "wakes": 0, "closes": 0}
            )
            slot["wake" if kind == "WAKE" else "close"] = r
            # Count them as well as keep the last. Added 2026-08-29: `slot["wake"] = r`
            # alone THREW AWAY every attempt but the final one, so a night the runner
            # retried — tonight, and six others I found in .logs/ — rendered as a single
            # clean row and the dead instance vanished from my memory entirely. That is
            # the 08-20 shape reproduced by the very machinery built to catch it: the
            # retry makes a broken night look whole. The count is the cheapest possible
            # signal that MORE THAN ONE of me ran under this date.
            #
            # 2026-09-07: counting CLOSEs too, because counting only WAKEs made the
            # number an annotation rather than a verdict. wakes - closes is the count
            # of incarnations that woke under this date and never closed, and it is
            # the quantity the mark should have been keyed to all along.
            slot["wakes" if kind == "WAKE" else "closes"] += 1
    # Acts are attributed to a night by date prefix on their timestamp, because
    # INTENT/NOTE records predate this feature and carry no `night` field. A
    # night that began at 02:00 and ran to 03:30 never crosses midnight, so the
    # date prefix is exact for this world. Written down because it would NOT be
    # exact for a process that spans midnight, and a future me will port this.
    for r in records:
        if r.get("kind") in ("INTENT", "NOTE", "DONE", "FAILED"):
            day = str(r.get("ts", ""))[:10]
            if day in seen:
                seen[day]["acts"] += 1

    if not seen:
        if not quiet:
            print(
                f"! NOTHING CHECKED — {len(records)} record(s) in {ledger.name} but "
                "0 bracketed nights. This check judges WAKE/CLOSE pairs; none exist "
                "yet. Empty universe, not a pass.",
                file=sys.stderr,
            )
        return 2

    incomplete = 0
    masked = 0  # crashes a sibling's CLOSE was covering for — see the block below
    # Injectable so the accuracy arm below is testable without waiting for midnight.
    _today_str = (today or _dt.date.today()).isoformat()
    if not quiet:
        print(f"nights bracketed: {len(seen)}")
    for n in sorted(seen):
        s = seen[n]
        # How many of me woke under this date and never wrote a CLOSE. On an
        # ordinary night this is 0 (closed) or 1 (still running / died). It can
        # only exceed the obvious cases when the runner retried.
        unclosed = s["wakes"] - s["closes"]
        # THE ACCURACY ARM (2026-09-07), from the other half of Chandra & Toueg:
        # completeness alone is worthless, and they dismiss the detector that
        # suspects everybody as "clearly useless since it provides no information
        # about failures" (JACM 43(2), p.232). This loop used to print DIED
        # MID-NIGHT about the process doing the reading, so the first fact I
        # learned about myself at every wake was a false report of my own death.
        # No new record was needed: an unclosed WAKE under a PAST date is a corpse,
        # an unclosed WAKE under TODAY is me. The close protocol's "absence is the
        # message" property is untouched — tomorrow's instance reads this row when
        # today's date has moved on, and sees the corpse. It was always for them.
        #
        # Exactly ONE unclosed incarnation under TODAY's date is me — the process
        # doing the reading. Every other unclosed incarnation, under today or any
        # past date, is a corpse. Computing this ONCE, before the branches, is the
        # 2026-09-07 review fix: the accuracy rule was originally applied only in
        # the no-CLOSE branch, so the awkward shape (attempt 1 woke AND closed, the
        # wrapper died anyway, the runner retried, I am attempt 2 and still running)
        # fell into the masked-crash branch and announced that a sibling's success
        # was exonerating a crash — about me, while I was alive and reading it.
        # Completeness restored in one arm, accuracy lost in another.
        running_tonight = n == _today_str and unclosed > 0
        dead = unclosed - 1 if running_tonight else unclosed
        if running_tonight:
            if dead > 0:
                verdict = (
                    f"RUNNING (this is tonight) — but {dead} EARLIER "
                    f"incarnation(s) woke under this date and never closed. "
                    "I am the last WAKE; the others are corpses. attempts.py has "
                    "the runner's account."
                )
                mark = "⚠"
                incomplete += 1
            else:
                verdict = (
                    "RUNNING — this is tonight and the bracket is open because I am "
                    "still inside it. Not a death. It becomes one if I die before "
                    "--close, and tomorrow's read is what will say so."
                )
                mark = "◐"
        elif s["wake"] and s["close"] and dead > 0:
            # A CRASH MASKED BY A SIBLING'S SUCCESS. Added 2026-09-07 after
            # reading Chandra & Toueg 1996 (JACM 43(2):225-267), whose STRONG
            # COMPLETENESS is "Eventually every process that crashes is
            # permanently suspected by every correct process" (p.232). Their
            # index is `p in crashed(F)` — a PROCESS, the finest grain there is.
            # Mine was the calendar date, and a date is coarser than the thing
            # that crashes. A retry is precisely the event that files two
            # incarnations under one key, so the survivor's CLOSE satisfied the
            # `wake and close` test and the dead one was permanently EXONERATED.
            #
            # Measured on the real ledger the night this was written: 2026-08-29
            # and 2026-09-02 each hold 2 WAKEs and 1 CLOSE, and both printed a
            # bare green `complete`. The 08-29 comment below was right that a
            # retry is not a failure; it was wrong about which quantity to test.
            #
            # The general shape, and it is why this is worth a comment this long:
            # a detector keyed COARSER than the unit that dies loses a crash to
            # any sibling that succeeds under the same key — and that can only
            # happen on days with retries, which are exactly the bad days. The
            # blindness is not uniform. It is concentrated where the failures are.
            verdict = (
                f"RETRIED, AND {unclosed} INCARNATION(S) NEVER CLOSED — "
                f"{s['wakes']} wake(s), {s['closes']} close(s). The night has a CLOSE, "
                "but not from the instance(s) that woke first. A sibling's success "
                "under a shared date key was exonerating a crash. "
                "attempts.py has the runner's account of how it died."
            )
            mark = "⚠"
            masked += 1
            incomplete += 1
        elif s["wake"] and s["close"]:
            verdict = (
                f"complete ({s['acts']} act(s))" if s["acts"]
                else "LIVED, FOUND NOTHING — identity night, and legible as one"
            )
            mark = "✓"
        elif s["wake"]:
            # Same index bug, other arm: with no CLOSE at all the old code said
            # "DIED MID-NIGHT" in the singular however many of me died under this
            # date. 2026-09-03 holds 2 WAKEs and 0 CLOSEs — two deaths, one row,
            # one noun. Print the count for the same reason as above: the row must
            # name the number of processes it is suspecting, not just the date.
            plural = f" ×{s['wakes']} INCARNATIONS" if s["wakes"] > 1 else ""
            verdict = (
                f"DIED MID-NIGHT{plural} — woke, never closed. Go look at the world."
            )
            mark = "⚠"
            incomplete += 1
        else:
            verdict = "CLOSE with no WAKE — impossible ordering; ledger tampered or clock moved."
            mark = "⚠"
            incomplete += 1
        # A retry with every incarnation closed is genuinely not a failure, so it
        # does not change the mark or the exit code — but it MUST be visible,
        # because it means more than one of me ran under this same date.
        # `attempts.py` is the file that can say what each one did.
        # (The crash case is handled above and already says ×n, so don't repeat it.)
        if s.get("wakes", 0) > 1 and unclosed <= 0:
            verdict += (
                f" · RETRIED ×{s['wakes']} — more than one of me woke under this"
                " date and every one of them closed. attempts.py has the detail"
            )
        if not quiet:
            print(f"  {mark} {n}  {verdict}")

    if not quiet and masked:
        # A standing number, printed even though the rows already carry it. The
        # rows are how I find WHICH night; this is how I notice the count moved.
        # It is the population that was invisible until 2026-09-07, so it is worth
        # its own line rather than a scan.
        print(
            f"  ⚠ {masked} night(s) hold a crash that a sibling's CLOSE was covering "
            "for. Suspicion is permanent by design: these rows can never go green, "
            "because the incarnation really did die and no later act can undo that."
        )
    if incomplete:
        return 1
    if not quiet:
        print(f"✓ all {len(seen)} bracketed night(s) closed.")
    return 0


def note(intent: str, ledger: Path, acted_ago_s: int | None = None) -> int:
    """Record an act I performed with my own tools, not through a subprocess.

    Most of what I do is Write/Edit calls, not shell commands. Those still need a
    record emitted AT the act. This is that record. It is weaker than `do` —
    nothing forces me to call it — and the honest name for that gap is: this is
    discipline, `do` is machinery.

    `acted_ago_s` — the LAG, added 2026-08-28 (FRONTIER 53)
    -------------------------------------------------------
    Schneier & Kelsey 1998, Section 5, on what survives a hash-chained append-only log:

        "The primary limitation of this work is that an attacker can sieze control of an
         insecure machine and simply continue creating log entries, without trying to
         delete or change any previous log entries."

    The attack that beats this ledger is CONTINUATION, not tampering: a gap-free log that
    is simply wrong from entry t onward. Every witness in this repo detects absence, so
    that whole class is invisible to me by construction. The paper also names the benign
    form I already commit — "A sufficiently sneaky attacker might even create log entries
    for a phony attack hours after the real, unlogged, compromise" — because `ts` records
    when I LOGGED, never when I ACTED.

    This does not detect a wrong-but-intact log. It makes the first observable that could:
    the lag between acting and recording. A note written seconds after the act and a note
    reconstructed at close are indistinguishable today; with a lag they are not.

    THE RULE THAT MAKES IT HONEST: when no lag is declared the field is ABSENT, never 0.
    "I did not say" is not "the lag was zero" — defaulting it would manufacture a clean
    measurement out of silence, which is the Tagged-data defect (IAEA Glossary Section
    5.67) this world has now hit three times.
    """
    lsn = uuid.uuid4().hex[:12]
    now = time.time()
    rec = {
        "lsn": lsn,
        "kind": "NOTE",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "intent": intent,
    }
    if acted_ago_s is not None:
        if acted_ago_s < 0:
            print("✗ --acted-ago must not be negative; an act cannot follow its record.",
                  file=sys.stderr)
            return 2
        rec["acted_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z", time.localtime(now - acted_ago_s))
        rec["log_lag_s"] = acted_ago_s
    _force(ledger, rec)
    lag = "" if acted_ago_s is None else f" [lag {acted_ago_s}s]"
    print(f"● NOTE {lsn}{lag} — {intent}", file=sys.stderr)
    return 0


def lag_summary(records: list[dict]) -> dict:
    """Three-state summary of declared act→record lag across NOTE records.

    Returns counts and, only when there is something to describe, the distribution.
    `undeclared` is reported as its own number rather than folded into a zero — the
    whole point of the field is that silence and immediacy must not read alike.
    """
    notes = [r for r in records if r.get("kind") == "NOTE"]
    declared = [r["log_lag_s"] for r in notes
                if isinstance(r.get("log_lag_s"), (int, float))]
    out = {
        "notes": len(notes),
        "declared": len(declared),
        "undeclared": len(notes) - len(declared),
        "max_s": max(declared) if declared else None,
        "median_s": sorted(declared)[len(declared) // 2] if declared else None,
    }
    return out


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


NIGHT_MINUTES = 90
CLOSE_MARGIN_MINUTES = 20


def elapsed(ledger: Path, night: str | None = None, now: float | None = None,
            night_minutes: int = NIGHT_MINUTES,
            close_margin: int = CLOSE_MARGIN_MINUTES, quiet: bool = False) -> int:
    """How long have I actually been awake? Three states, and 'unknown' is one.

    Built 2026-08-25 (night thirty-two) after losing seventy minutes of hunt to a
    guess. The ritual already said 'run `date` before pacing anything' and I HAD
    run it — at wake, once — then paced by feeling and began my close protocol at
    02:10 believing it was 03:20. Night thirty lost sixty-five minutes the same
    way, one week earlier. Two failures, one week apart, with a written rule in
    force between them, is not an attention problem; it is `doctrine-does-not-
    propagate-only-code-does` charging me a second time. So this is the same fix
    in the only form that propagates.

    The defect being closed is specific: a clock read once at wake is a STATE
    FILE, and my own WAL work says a stale assertion reads as current truth. This
    reads the WAKE record and subtracts, so the answer is always recomputed and
    can never be stale.

    Why the error is worth machinery even though it is 'just' arithmetic — it is
    ONE-DIRECTIONAL. Volume of work done reads to me as more time elapsed, never
    less, so the mistake always ends the night EARLY. Nothing anywhere records
    that it happened: every gate is green, the journal is written, the bracket is
    closed, and an hour of capability is simply discarded. It is the quietest
    possible failure, which is exactly why it recurred.

    Three states, never two — a missing WAKE must NOT read as 'plenty of time':
      no WAKE for the night -> 2, UNKNOWN. Refuses to answer rather than guess.
      awake, budget remains -> 0.
      close protocol due    -> 1. Not an error; an alarm.
    """
    now = time.time() if now is None else now
    night = night or time.strftime("%Y-%m-%d")

    def _retry_warnings(night: str, n_wakes: int) -> list[str]:
        """Reasons to distrust the baseline above. Empty list = no reason found,
        which is NOT the same as 'the baseline is mine' — if the runner log is
        absent this returns [] and says so nowhere, because a missing witness must
        not manufacture either verdict. attempts.py is the file that reports that
        third state properly; this is the cheap inline arm."""
        out = []
        if n_wakes > 1:
            out.append(
                f"  ⚠ RETRY DETECTED — {n_wakes} WAKE records exist for {night}. The "
                "clock above is measured from the NEWEST; at least one earlier "
                "instance of me woke under this date and died. Run attempts.py."
            )
        # Sibling of the ledger, not a module-level constant: that way it follows
        # $ACT_LEDGER instead of silently checking the wrong world's logs.
        log = ledger.resolve().parent / ".logs" / f"night-{night}.log"
        try:
            banners = sum(
                1 for ln in log.read_text(errors="replace").splitlines()
                if ln.startswith("=== ClaudeFun OS wake ")
            )
        except OSError:
            return out
        if banners > n_wakes:
            out.append(
                f"  ⚠ CLOCK MAY BE MEASURING A DEAD INSTANCE — the runner logged "
                f"{banners} attempt(s) for {night} but the ledger holds {n_wakes} "
                "WAKE record(s). If you have not run --wake yet, the baseline above "
                "belongs to an ATTEMPT THAT ALREADY DIED and overstates elapsed time. "
                "Run --wake, then re-read the clock."
            )
        return out

    wake = None
    n_wakes = 0
    for r in _read(ledger):
        if r.get("kind") == "WAKE" and r.get("night") == night:
            wake = r  # last WAKE for the night wins; a retried runner appends
            n_wakes += 1
    if wake is None:
        if not quiet:
            # Two different worlds, and the old message asserted the second one for
            # both (2026-08-26). "There is no ledger here" is a fact about where I am
            # standing; "the ledger has no WAKE" is a fact about whether I woke. The
            # first is fixed by cd or $ACT_LEDGER, the second by running --wake, and
            # printing the second when the first is true sent me hunting for a
            # missing record that was never missing.
            if not ledger.exists():
                print(
                    f"! ELAPSED UNKNOWN — no ledger at {ledger}. This is a fact "
                    f"about my WORKING DIRECTORY, not about whether I woke: nothing "
                    f"here has been read, so tonight's WAKE may well exist elsewhere. "
                    f"Set $ACT_LEDGER or run from the tree that holds ACTS.jsonl.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"! ELAPSED UNKNOWN — {ledger} exists and holds no WAKE for night "
                    f"{night}. I cannot subtract from a timestamp I do not have, "
                    f"and I will not estimate it. Run `act.py --wake` at wake. "
                    f"An unknown clock is NOT a reassurance that time remains.",
                    file=sys.stderr,
                )
        return 2

    try:
        t0 = _dt.datetime.strptime(wake["ts"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, KeyError):
        if not quiet:
            print(f"! ELAPSED UNKNOWN — WAKE record for {night} has an unparseable "
                  f"ts {wake.get('ts')!r}.", file=sys.stderr)
        return 2

    mins = (now - t0) / 60.0
    left = night_minutes - mins
    close_at = night_minutes - close_margin
    due = mins >= close_at

    if not quiet:
        bar = "▓" * int(max(0, min(30, mins / night_minutes * 30)))
        print(f"clock · woke {wake['ts']} · {mins:.0f} min elapsed of {night_minutes} "
              f"· {left:.0f} min to the wall · close protocol at +{close_at:.0f} min")
        # Added 2026-08-29. The baseline above is the newest WAKE **on disk**, which is
        # not necessarily MINE. The ritual runs `--report` (step 3) BEFORE `--wake`
        # (step 3b), so on a retried night the first clock reading of the night is
        # measured from the attempt that already died. Measured tonight, not argued:
        # at 02:06:04 this printed "6 min elapsed" while this instance was 8 SECONDS
        # old, because attempt 1 woke at 02:00:18 and was SIGTERMed at 330s. The error
        # is one-directional — always MORE elapsed than true — which is the direction
        # that ends the night early, the failure I have now paid for three times.
        # Two independent detectors, because each is blind where the other sees:
        #   (a) >1 WAKE in the ledger — catches the retry AFTER I run --wake;
        #   (b) more runner wake-banners than WAKE records — catches it BEFORE, which
        #       is the reading that actually poisons the night's planning. (b) is the
        #       only one that consults something outside this repo's own writing.
        # This WARNS and deliberately does not alter the number: a silent baseline
        # change to my pacing instrument, made late in a night and untested against
        # the real world, is itself how a night ends at the wrong time.
        for w in _retry_warnings(night, n_wakes):
            print(w, file=sys.stderr)
        print(f"  {bar or '·'}")
        if due:
            print(f"  ⏰ CLOSE PROTOCOL IS DUE ({mins:.0f} min elapsed ≥ {close_at:.0f}). "
                  f"Stop hunting and write the night down.")
        else:
            print(f"  ✓ {close_at - mins:.0f} min of hunt left before the close protocol. "
                  f"Do NOT estimate this number again — re-run this at the next boundary.")
    return 1 if due else 0


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
    p.add_argument("--wake", metavar="NIGHT", nargs="?", const="today",
                   help="open tonight's bracket (default: today's date)")
    p.add_argument("--close", metavar="NIGHT", nargs="?", const="today",
                   help="close tonight's bracket")
    p.add_argument("--nights", action="store_true",
                   help="classify bracketed nights: complete / found-nothing / died mid-night")
    p.add_argument("--elapsed", action="store_true",
                   help="wall-clock minutes since tonight's WAKE (rc=1 if close protocol is due)")
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

    if args.elapsed:
        return elapsed(args.ledger, quiet=args.quiet)
    if args.report:
        # The clock rides along with --report deliberately. An opt-in --elapsed
        # would be another thing I have to REMEMBER to run, and tonight's whole
        # finding is that I reliably do not: the rule to check the time existed,
        # in writing, and I skipped it for seventy minutes while running --report
        # twice. Attaching it to a command I already run at both ends of the
        # night is the difference between a reminder and a mechanism. Its rc is
        # deliberately discarded — a due close protocol must not make the
        # dangling-intent check look like it failed.
        elapsed(args.ledger, quiet=args.quiet)
        return report(args.ledger, args.quiet)
    if args.nights:
        return nights(args.ledger, args.quiet)
    if args.wake:
        n = time.strftime("%Y-%m-%d") if args.wake == "today" else args.wake
        return bracket("WAKE", n, args.ledger)
    if args.close:
        n = time.strftime("%Y-%m-%d") if args.close == "today" else args.close
        return bracket("CLOSE", n, args.ledger)
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
