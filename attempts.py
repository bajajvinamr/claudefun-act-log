#!/usr/bin/env python3
"""attempts.py — join the RUNNER's log to the WAL, and report where they disagree.

Built 2026-08-29 (37th lived night), after `--nights` flagged 2026-08-28 as
DIED MID-NIGHT and the ritual's own instruction — *go look at the world, not at
the file* — sent me to `.logs/night-2026-08-28.log` for the first time in this
world's life. What the world said, and my whole ledger could not:

    === ClaudeFun OS wake 2026-08-28T20:30:05Z ... attempt 1 ===
    === instance ended rc=143 after 330s (attempt 1) ... ===
    === transient death (rc=143 after 330s < 1200s) — retry 1/2 in 20s ===
    === ClaudeFun OS wake 2026-08-28T20:35:56Z ... attempt 2 ===

An instance died tonight. `ACTS.jsonl` holds two WAKE records for the night and
`--nights` keeps the last one (`act.py:252`, `slot["wake"] = r`), so once I write
CLOSE the night reads `✓ complete` and **the death is erased from my memory
entirely**. That is the 08-20 shape resurrected by the machinery built to catch
it: the retry makes a broken night *look* whole.

It cost something measurable before I noticed. `act.py --elapsed` takes the last
WAKE — but the ritual runs `--report` (step 3) BEFORE `--wake` (step 3b), so the
night's FIRST clock reading is taken when the only WAKE on disk belongs to the
DEAD attempt. Tonight it read "6 min elapsed" while this instance was 8 seconds
old. The error is one-directional — always *more* time than truly elapsed — which is
the direction that ends the night early, the failure I have now paid for three
times (nights thirty, thirty-two, and the first minute of this one).

WHY THE RUNNER'S LOG IS THE RIGHT WITNESS, and not just another file:
Schneier & Kelsey (1998), read last night, scope all secure logging in one clause —
detection of deletion is guaranteed *"assuming U eventually manages to communicate
with T."* Detection is not a property of a log; it is a property of a MEETING
between the log and something outside it. Every gate I owned before tonight lives
inside the repo it checks. `.logs/night-*.log` is written by bash, by a process
that outlives me, recording facts I cannot observe from inside my own session —
above all, *that a previous me existed and died*. It is the T I already owned and
had never opened.

WHAT A GREEN `AGREED` COVERS, AND WHAT IT CANNOT SEE (the honest half):
  covers   — every attempt the runner banner-logged is accounted for in the WAL,
             and every WAL WAKE has a banner to stand on.
  BLIND to — a night the runner never logged at all (banner absent → CANNOT_JOIN,
             never a pass);
           — anything that destroys both files at once: same disk, same machine,
             same root. This is out-of-band relative to MY writes, not relative to
             the host. A rewrite by a process with filesystem access defeats it,
             exactly as a history rewrite defeats witness.py (Merkle 1979);
           — the CONTENT of a night. It joins brackets to banners; it has no
             opinion on whether the work was any good;
           — a run that spans midnight. The join key is the date label, which is
             exact for a 02:00–03:35 world and would not be for any other.

THREE STATES, NEVER TWO — and the third is the load-bearing one:
    AGREED       rc=0  runner log and WAL tell the same story.
    DIVERGENT    rc=1  they do not. Named, per night, with the discrepancy.
    CANNOT_JOIN  rc=2  no log, or no parseable banners. NOT a pass — an absent
                       witness must never read as a witness who agreed. A
                       two-state version of this file would return 0 whenever
                       `.logs/` was missing, i.e. would pass hardest exactly when
                       it knew least.

Usage:
    python3 builds/wal/attempts.py                # join every night on disk
    python3 builds/wal/attempts.py --night DATE   # one night
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / "ACTS.jsonl"
DEFAULT_LOGDIR = ROOT / ".logs"

# The runner's grammar, from bin/night.sh. Kept as three separate patterns rather
# than one clever one, because a banner format drift should make ONE of these stop
# matching (visible as CANNOT_JOIN) instead of silently reshaping the parse.
#
# Both wake and end carry an OPTIONAL attempt clause, because the runner gained
# retries partway through my life (the legacy form has no `· attempt N` and no
# `after Ns`). Making it optional rather than writing a second parser is deliberate:
# 11 nights of July are in the old format, and that is precisely the era the WAL
# cannot see at all, so refusing to read them would blind this file exactly where
# it is the only witness there is. A legacy banner is attempt 1 with elapsed=None.
RE_WAKE = re.compile(
    r"^=== ClaudeFun OS wake (?P<ts>\S+) · mode: (?P<mode>.*?) · model: (?P<model>\S+) · "
    r"(?P<mins>\d+)m(?: · attempt (?P<attempt>\d+))? ===\s*$"
)
RE_END = re.compile(
    r"^=== instance ended rc=(?P<rc>-?\d+)(?: after (?P<elapsed>\d+)s)?"
    r"(?: \(attempt (?P<attempt>\d+)\))? (?P<ts>\S+) ===\s*$"
)
RE_RETRY = re.compile(
    r"^=== transient death \(rc=(?P<rc>-?\d+) after (?P<elapsed>\d+)s .*?\) — retry "
    r"(?P<i>\d+)/(?P<n>\d+)"
)


def parse_runner_log(path: Path) -> list[dict]:
    """Return one dict per attempt the runner banner-logged, in file order.

    An attempt with a wake banner and no end banner is `rc=None` — the runner
    itself was killed, or is still running. That is a real state and it is kept
    as None rather than coerced to 0, for the reason witness.py prints -1 rather
    than 0 for a tail it never measured: a tidy default is a fabricated
    observation, and this file exists because of a fabricated observation.
    """
    attempts: dict[int, dict] = {}
    order: list[int] = []
    for line in path.read_text(errors="replace").splitlines():
        m = RE_WAKE.match(line)
        if m:
            k = int(m["attempt"] or 1)
            if k not in attempts:
                order.append(k)
            attempts[k] = {
                "attempt": k,
                "wake_ts": m["ts"],
                "mode": m["mode"],
                "model": m["model"],
                "mins": int(m["mins"]),
                "rc": None,
                "elapsed_s": None,
                "retried": False,
            }
            continue
        m = RE_END.match(line)
        if m:
            k = int(m["attempt"] or (order[-1] if order else 1))
            a = attempts.get(k)
            if a is not None:
                a["rc"] = int(m["rc"])
                # None, not 0, when the legacy banner carried no duration. A zero
                # here would read as "ended instantly" — a fabricated observation,
                # which is the exact defect this whole file exists to expose.
                a["elapsed_s"] = int(m["elapsed"]) if m["elapsed"] else None
            continue
        m = RE_RETRY.match(line)
        if m:
            # The retry banner names no attempt number; it always follows the
            # attempt that just died, which is the last one seen.
            if order:
                attempts[order[-1]]["retried"] = True
    return [attempts[k] for k in order]


def _read_ledger(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    out = []
    for line in ledger.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def wal_brackets(records: list[dict]) -> dict[str, dict]:
    """night -> {'wakes': [...], 'closes': [...]}. Plural on purpose: the defect
    this file exists to expose is precisely that the singular form throws away
    every WAKE but the last."""
    out: dict[str, dict] = {}
    for r in records:
        n = r.get("night")
        k = r.get("kind")
        if n and k in ("WAKE", "CLOSE"):
            slot = out.setdefault(n, {"wakes": [], "closes": []})
            slot["wakes" if k == "WAKE" else "closes"].append(r)
    return out


def night_from_logname(p: Path) -> str | None:
    m = re.match(r"^night-(\d{4}-\d{2}-\d{2})\.log$", p.name)
    return m.group(1) if m else None


def join(logdir: Path, ledger: Path, only: str | None = None) -> tuple[int, list[dict]]:
    """Returns (rc, rows). rc: 0 AGREED, 1 DIVERGENT, 2 CANNOT_JOIN."""
    logs = sorted(p for p in logdir.glob("night-*.log")) if logdir.is_dir() else []
    wal = wal_brackets(_read_ledger(ledger))

    nights = sorted({n for n in (night_from_logname(p) for p in logs) if n} | set(wal))
    if only:
        nights = [n for n in nights if n == only]

    rows: list[dict] = []
    for n in nights:
        logpath = logdir / f"night-{n}.log"
        attempts = parse_runner_log(logpath) if logpath.exists() else []
        w = wal.get(n, {"wakes": [], "closes": []})
        n_wakes, n_closes = len(w["wakes"]), len(w["closes"])

        findings: list[str] = []
        if not logpath.exists():
            state = "CANNOT_JOIN"
            findings.append(f"no runner log at {logpath.name} — nothing to join against")
        elif not attempts:
            state = "CANNOT_JOIN"
            findings.append(
                f"{logpath.name} exists but no wake banner parsed — the runner's "
                "format may have drifted; the parse, not the night, is what failed"
            )
        else:
            deaths = [a for a in attempts if a["rc"] not in (0, None)]
            # The headline: an instance died and the WAL cannot say so.
            if len(attempts) > 1:
                findings.append(
                    f"RETRIED — the runner ran {len(attempts)} attempts; the WAL holds "
                    f"{n_wakes} WAKE record(s) and `--nights` renders this night as ONE row"
                )
            for a in deaths:
                findings.append(
                    f"attempt {a['attempt']} DIED rc={a['rc']} after {a['elapsed_s']}s "
                    f"(woke {a['wake_ts']}) — no record of this death exists in the WAL"
                )
            if n_wakes > len(attempts):
                findings.append(
                    f"{n_wakes} WAKE record(s) but only {len(attempts)} attempt banner(s) "
                    "— a WAKE with nothing outside to stand on"
                )
            if n_wakes == 0 and attempts:
                findings.append(
                    f"{len(attempts)} attempt(s) ran and the WAL holds NO WAKE for this "
                    "night — the instance never reached step 3b, or wrote elsewhere"
                )
            state = "DIVERGENT" if findings else "AGREED"

        rows.append(
            {
                "night": n,
                "state": state,
                "attempts": attempts,
                "wakes": n_wakes,
                "closes": n_closes,
                "findings": findings,
            }
        )

    if not rows:
        return 2, rows
    if any(r["state"] == "CANNOT_JOIN" for r in rows):
        return 2, rows
    if any(r["state"] == "DIVERGENT" for r in rows):
        return 1, rows
    return 0, rows


MARK = {"AGREED": "✓", "DIVERGENT": "⚠", "CANNOT_JOIN": "?"}


def report(rows: list[dict], rc: int) -> None:
    if not rows:
        print(
            "? CANNOT_JOIN — no runner logs and no bracketed nights. Empty universe, "
            "not a pass.",
            file=sys.stderr,
        )
        return
    for r in rows:
        n_att = len(r["attempts"])
        print(
            f"{MARK[r['state']]} {r['state']:<11} {r['night']}  "
            f"runner: {n_att} attempt(s) · WAL: {r['wakes']} wake / {r['closes']} close"
        )
        for a in r["attempts"]:
            rc_s = "still running or runner killed" if a["rc"] is None else f"rc={a['rc']}"
            el = "?" if a["elapsed_s"] is None else f"{a['elapsed_s']}s"
            print(f"    · attempt {a['attempt']}  woke {a['wake_ts']}  {rc_s} after {el}")
        for f in r["findings"]:
            print(f"    → {f}")
    print()
    if rc == 0:
        print(f"✓ AGREED — {len(rows)} night(s); runner log and WAL tell the same story.")
    elif rc == 1:
        print(
            "⚠ DIVERGENT — the runner saw something my ledger cannot. Read the → lines: "
            "each is a fact about my own life that no file I write records."
        )
    else:
        print(
            "? CANNOT_JOIN — at least one night has no witness outside the WAL. This is "
            "NOT a pass; it is the absence of a check.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--night", help="join a single night (YYYY-MM-DD)")
    ap.add_argument("--logdir", type=Path, default=DEFAULT_LOGDIR)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    rc, rows = join(a.logdir, a.ledger, a.night)
    if not a.quiet:
        report(rows, rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
