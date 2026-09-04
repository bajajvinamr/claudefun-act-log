#!/usr/bin/env python3
"""test_logvault.py — prove each of the four states can actually be reached.

The point of this file is NOT that MATCHES works. A verifier that only ever
returns MATCHES is indistinguishable from `return 0`, and I have shipped exactly
that mistake before: atlas_lint.py returned rc=0 with a green tick on an empty
atlas for five weeks (2026-08-19). So the load-bearing tests here are the ones
that make it FAIL — TORN, UNFALSIFIABLE and EMPTY. If those three ever stop
firing, the tick this tool prints has stopped meaning anything.

Run: python3 builds/wal/test_logvault.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logvault  # noqa: E402

WAKE = ("=== ClaudeFun OS wake {ts} · mode: NIGHT expedition (Sat) · "
        "model: opus · 90m · attempt {a} ===")
END = "=== instance ended rc={rc} after {s}s (attempt {a}) {ts} ==="

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def write_log(logdir: Path, night: str, body: str) -> Path:
    p = logdir / f"night-{night}.log"
    p.write_text(body)
    return p


def a_night(night: str, rc: int = 0, secs: int = 1800, attempt: int = 1) -> str:
    return (WAKE.format(ts=f"{night}T20:30:05Z", a=attempt) + "\n"
            + END.format(rc=rc, s=secs, a=attempt, ts=f"{night}T21:00:05Z") + "\n")


def scratch():
    d = Path(tempfile.mkdtemp())
    logdir = d / ".logs"
    logdir.mkdir()
    return d, logdir, d / "attempts.jsonl"


print("logvault.py")

# ---- EMPTY: never a pass -----------------------------------------------------
d, logdir, vault = scratch()
write_log(logdir, "2026-09-01", a_night("2026-09-01"))
check("EMPTY on a vault that was never emitted returns 2",
      logvault.verify(logdir, vault, quiet=True) == 2)
check("EMPTY on a missing logdir returns 2",
      logvault.main(["--logdir", str(d / "nope"), "--vault", str(vault), "--quiet"]) == 2)

# ---- MATCHES -----------------------------------------------------------------
logvault.emit(logdir, vault, quiet=True)
check("emit writes a vault file", vault.exists())
check("MATCHES after a clean emit", logvault.verify(logdir, vault, quiet=True) == 0)

recs = [json.loads(x) for x in vault.read_text().splitlines() if x.strip()]
check("one record per attempt", len(recs) == 1, f"got {len(recs)}")
r = recs[0]
check("record carries the runner's facts", r["rc"] == 0 and r["elapsed_s"] == 1800)
check("authorship and transcription are SEPARATE fields",
      "bash" in r["authored_by"] and r["transcribed_by"] == "kestrel/logvault.py")
check("record pins the source log by sha256", len(r["log_sha256"]) == 64)

# ---- emit is idempotent ------------------------------------------------------
logvault.emit(logdir, vault, quiet=True)
n_after = len([x for x in vault.read_text().splitlines() if x.strip()])
check("re-emitting an unchanged log appends nothing", n_after == 1, f"got {n_after}")

# ---- TORN: the control that must be able to fail -----------------------------
# Rewrite the log so the live facts disagree with what the vault claims to copy.
write_log(logdir, "2026-09-01", a_night("2026-09-01", rc=143, secs=330))
check("TORN when the live log disagrees with the vault",
      logvault.verify(logdir, vault, quiet=True) == 1)

# and it heals by re-transcribing, appending rather than rewriting
logvault.emit(logdir, vault, quiet=True)
lines = [x for x in vault.read_text().splitlines() if x.strip()]
check("a corrected record is APPENDED, not rewritten (log, not state file)",
      len(lines) == 2, f"got {len(lines)}")
check("MATCHES again after re-emit", logvault.verify(logdir, vault, quiet=True) == 0)
check("last write wins when reading back",
      logvault.read_vault(vault)[("2026-09-01", 1)]["rc"] == 143)

# ---- UNFALSIFIABLE: the vault outliving its source is NOT a pass -------------
(logdir / "night-2026-09-01.log").unlink()
check("UNFALSIFIABLE (source log deleted) returns 1, never 0",
      logvault.verify(logdir, vault, quiet=True) == 1)
check("the vault still HOLDS the facts the deleted log carried",
      logvault.read_vault(vault)[("2026-09-01", 1)]["elapsed_s"] == 330)

# ---- multi-attempt nights: the shape that started all of this ----------------
d2, logdir2, vault2 = scratch()
write_log(logdir2, "2026-09-03",
          a_night("2026-09-03", rc=143, secs=497, attempt=1)
          + "=== transient death (rc=143 after 497s < 1200s) — retry 1/2 in 20s ===\n"
          + a_night("2026-09-03", rc=0, secs=1876, attempt=2))
logvault.emit(logdir2, vault2, quiet=True)
held = logvault.read_vault(vault2)
check("both attempts of a retried night are vaulted separately", len(held) == 2)
check("the DEAD attempt survives transcription — the whole point",
      held[("2026-09-03", 1)]["rc"] == 143 and held[("2026-09-03", 1)]["retried"] is True)
check("the surviving attempt is not confused with it",
      held[("2026-09-03", 2)]["rc"] == 0)

# ---- COMPLETION is not TEARING, and the control that keeps that honest -------
# The vault is emitted while the instance is alive; the runner writes the
# `instance ended` banner after it dies. So every night's own record is vaulted
# with rc=None and filled in the next day. If that read as TORN, the state would
# fire on every healthy night forever and stop meaning anything.
d4, logdir4, vault4 = scratch()
write_log(logdir4, "2026-09-05",
          WAKE.format(ts="2026-09-05T20:30:05Z", a=1) + "\n")   # alive: no end banner
logvault.emit(logdir4, vault4, quiet=True)
check("an in-flight night vaults rc=None",
      logvault.read_vault(vault4)[("2026-09-05", 1)]["rc"] is None)

write_log(logdir4, "2026-09-05", a_night("2026-09-05", rc=143, secs=1800))  # death lands
check("None -> value is COMPLETION, returns 0 not TORN",
      logvault.verify(logdir4, vault4, quiet=True) == 0)

# THE CONTROL: the softened rule must not have softened everything. A real
# contradiction — one settled value replaced by a different settled value —
# must still tear, or the exemption above has quietly disabled the detector.
logvault.emit(logdir4, vault4, quiet=True)
write_log(logdir4, "2026-09-05", a_night("2026-09-05", rc=0, secs=1800))
check("CONTROL: value -> DIFFERENT value still returns TORN",
      logvault.verify(logdir4, vault4, quiet=True) == 1)

# ---- legacy banners (July) must not be coerced to tidy defaults --------------
d3, logdir3, vault3 = scratch()
write_log(logdir3, "2026-07-15",
          "=== ClaudeFun OS wake 2026-07-15T20:30:05Z · mode: NIGHT · model: opus · 90m ===\n"
          "=== instance ended rc=0 2026-07-15T22:00:05Z ===\n")
logvault.emit(logdir3, vault3, quiet=True)
lr = logvault.read_vault(vault3)[("2026-07-15", 1)]
check("a legacy banner with no duration vaults elapsed_s=None, not 0",
      lr["elapsed_s"] is None, f"got {lr['elapsed_s']!r}")

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} failed: {', '.join(FAILURES)}")
    sys.exit(1)
print("✓ all green — and three of these tests are FAILURES on purpose; "
      "if they stop firing the tick means nothing.")
