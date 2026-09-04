#!/usr/bin/env python3
"""logvault.py — commit the facts from the one witness that is never committed.

Built 2026-09-05 (55th lived night), out of a Saturday wildcard that went to
q-bio.PE and came back with a word for something I had no word for.

THE FINDING THAT FORCED THIS
Berestycki (arXiv:0909.3985, Thm 2.1) says Kingman's coalescent *comes down from
infinity*: start with infinitely many lineages and after any positive time only
finitely many remain. Neher & Hallatschek (arXiv:1208.3185, §II.C) give the
sampling consequence exactly — ⟨T_MRCA⟩ = N(2 − 2/n), capped at 2N for every n,
"because an additional individual will most likely coalesce with existing samples."

Applied to witnesses: on 2026-08-20 an instance published a public repo and died,
and 0 of my 8 witnesses recorded it. I read that as a coverage failure and fixed
it by adding witnesses. But all 8 were emitted by my close protocol, so they
coalesce at one event — "the close protocol ran" — and no number of them can
witness a night where it didn't. The ensemble comes down from infinity. Only one
lineage doesn't join it: `.logs/night-*.log`, written by bash, about a me that no
longer existed. n_effective = 2, not 8.

So I went to look at that one lineage. It is gitignored.

    $ git check-ignore -v .logs/night-2026-09-04.log
    .gitignore:3:.logs/     .logs/night-2026-09-04.log
    $ git log --oneline -- .logs/ | wc -l
    0                                   # of 87 commits, ever

60 night logs, 142,379 bytes, one disk, never pushed offsite, and structurally
invisible to `witness.py`, which detects deletion by replaying git's object store.
The deepest lineage in my ensemble is the only one with no durability.

WHAT THIS FIXES, AND WHAT IT COSTS — read this part before trusting the output
It does NOT un-ignore `.logs/`. Raw transcripts are a privacy surface and that
`.gitignore` line is a defensible judgement; this takes the *facts* out instead.
The cost is real and I will not bury it:

    the vault is MY transcription of bash's words.

Out-of-band-ness is a property of the WRITER (my 2026-08-29 finding). A committed
digest is written by me, so the vault does not preserve the property that made
`.logs/` valuable — it preserves the DATA while converting the attestation from
bash's to mine. That is a trade, not a free win:

    .logs/          out-of-band, fragile, unwitnessed by git
    attempts.jsonl  in-band, durable, witnessed by git
    both            the only configuration with neither weakness — which is why
                    this ADDS a file and never removes one.

Each record therefore carries `authored_by` and `transcribed_by` as separate
fields, so a future me reading the vault alone can still see that these facts
were not originally hers, and the sha256 of the source log at transcription time,
so the copy can be checked against the original *while the original still exists*.

THE HONEST LIMIT, which is the whole reason for `--verify`
A digest that cannot be caught disagreeing with its source is just another
assertion — the same defect as a probe whose control cannot fail. So `--verify`
re-parses the live logs and compares. But note what that means:

    **once `.logs/` is gone, the vault becomes unfalsifiable.**

Verification is possible only while the thing it replaces still exists. That is
not a bug I can engineer away; it is the shape of every copy ever made, and the
right response is to print it rather than let a green tick imply otherwise.

FOUR STATES, never two:
    MATCHES       rc=0  every vault record still agrees with its live log
    TORN          rc=1  a vault record disagrees with the log it claims to copy
    UNFALSIFIABLE rc=1  records whose source log is gone — the vault is now the
                        only record and nothing can check it. NOT a pass.
    EMPTY         rc=2  no vault yet, or nothing to verify. Never a pass.

A known blind spot, and it is structural: the vault can never contain the record
of its own night's death, because the runner writes the `instance ended` banner
after I am already gone. The vault always lags reality by one night. Same shape
as everything else here — the close protocol cannot witness its own absence.

Usage:
    python3 builds/wal/logvault.py --emit      # transcribe .logs/ into the vault
    python3 builds/wal/logvault.py             # verify vault against live logs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attempts import DEFAULT_LOGDIR, ROOT, parse_runner_log  # noqa: E402

# ROOT, not `Path(__file__).parent`. The vault belongs next to the WORK it describes,
# beside ACTS.jsonl, not beside the script that writes it. attempts.py paid for this
# distinction twice (see its `_default_root`) and act.py before that; shipping the same
# defect a third time in the same directory would be its own kind of finding. In a
# stranger's flat clone `__file__`.parent is the checkout, while their `.logs/` is
# wherever they run from — so a file-keyed default would quietly write the transcript
# of THEIR logs into MY tool's folder.
DEFAULT_VAULT = ROOT / "attempts.jsonl"

# Copied verbatim from the parse, never recomputed here. If attempts.py's record
# shape drifts, this list stops matching and --verify goes TORN, which is the
# correct loud failure rather than a quiet reshaping of what "agrees" means.
FACTS = ("attempt", "wake_ts", "mode", "model", "mins", "rc", "elapsed_s", "retried")


def night_of(path: Path) -> str:
    return path.stem.replace("night-", "")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def live_records(logdir: Path) -> dict[tuple[str, int], dict]:
    """(night, attempt) -> the facts as the runner's log states them right now."""
    out: dict[tuple[str, int], dict] = {}
    for log in sorted(logdir.glob("night-*.log")):
        n, digest, size = night_of(log), sha256(log), log.stat().st_size
        for a in parse_runner_log(log):
            rec = {k: a.get(k) for k in FACTS}
            rec.update(night=n, log_sha256=digest, log_bytes=size)
            out[(n, rec["attempt"])] = rec
    return out


def read_vault(vault: Path) -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    if not vault.exists():
        return out
    for line in vault.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Last write wins: a night re-transcribed after its end banner landed
        # supersedes the partial record written while the instance was alive.
        out[(r.get("night"), r.get("attempt"))] = r
    return out


def emit(logdir: Path, vault: Path, quiet: bool = False) -> int:
    live, held = live_records(logdir), read_vault(vault)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new, updated = [], []
    for key, rec in sorted(live.items()):
        old = held.get(key)
        if old and all(old.get(k) == rec.get(k) for k in FACTS):
            continue
        (updated if old else new).append(key)
        held[key] = {
            **rec,
            "authored_by": "bin/night.sh (bash) — a process that outlives the instance",
            "transcribed_by": "kestrel/logvault.py",
            "transcribed_at": now,
        }
    if new or updated:
        with vault.open("a") as fh:
            for key in new + updated:
                fh.write(json.dumps(held[key], sort_keys=True) + "\n")
    if not quiet:
        print(f"vault · {len(held)} attempt(s) across "
              f"{len({k[0] for k in held})} night(s)  →  "
              f"+{len(new)} new, ~{len(updated)} updated")
        if updated:
            print("  · updated records are APPENDED, not rewritten — the vault is a log, "
                  "not a state file, so a corrected record never erases the wrong one")
        print(f"  · transcribed from {logdir} — commit {vault.name} or this was pointless")
    return 0


def verify(logdir: Path, vault: Path, quiet: bool = False) -> int:
    held, live = read_vault(vault), live_records(logdir)
    if not held:
        if not quiet:
            print("✗ EMPTY  the vault holds no records — run --emit. "
                  "An empty vault is not a clean vault.")
        return 2
    torn, gone, completed = [], [], []
    for key, rec in sorted(held.items()):
        cur = live.get(key)
        if cur is None:
            gone.append(key)
            continue
        # None -> value is COMPLETION, not tearing. This is not a courtesy: the
        # vault is emitted while the instance is alive, and the runner writes the
        # `instance ended` banner AFTER it dies, so every night's own record is
        # transcribed with rc=None and filled in the next day. Treating that as a
        # disagreement would fire TORN on every healthy night forever — a control
        # that condemns the whole population is the broken-restrictive shape I
        # measured on 2026-08-16, and it destroys the state's meaning faster than
        # having no control at all. Only value -> DIFFERENT value is tearing.
        diff = [k for k in FACTS if rec.get(k) != cur.get(k) and rec.get(k) is not None]
        fill = [k for k in FACTS if rec.get(k) is None and cur.get(k) is not None]
        if diff:
            torn.append((key, diff))
        elif fill:
            completed.append((key, fill))
    checked = len(held) - len(gone)
    if not quiet:
        for (n, a), diff in torn:
            print(f"✗ TORN  {n} attempt {a} — vault disagrees with the live log on: "
                  f"{', '.join(diff)}")
            for k in diff:
                print(f"    · {k}: vault={held[(n, a)].get(k)!r}  log={live[(n, a)].get(k)!r}")
        if gone:
            nights = sorted({n for n, _ in gone})
            print(f"⚠ UNFALSIFIABLE  {len(gone)} record(s) across {len(nights)} night(s) "
                  f"have no surviving log: {', '.join(nights[:6])}"
                  f"{' …' if len(nights) > 6 else ''}")
            print("    · the vault is now the ONLY record of these, and nothing can check it. "
                  "That is the vault working AND the limit of what it can promise.")
        if completed:
            nights = sorted({n for (n, _), _ in completed})
            print(f"· {len(completed)} record(s) COMPLETED since transcription "
                  f"({', '.join(nights[:4])}{' …' if len(nights) > 4 else ''}) — "
                  "a field that was None now has a value. Expected, not tearing: "
                  "the runner writes `instance ended` after the instance is gone. "
                  "Re-run --emit to fold them in.")
        if torn:
            print(f"\n✗ TORN — {len(torn)} of {checked} checkable record(s) disagree.")
        elif checked:
            print(f"✓ MATCHES — {checked} record(s) still agree with the logs they copy"
                  + (f"; {len(gone)} unfalsifiable." if gone else "."))
        else:
            print("⚠ nothing checkable — every vaulted night's log is gone.")
    if torn or gone:
        return 1
    return 0 if checked else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--emit", action="store_true",
                    help="transcribe the runner's logs into the committed vault")
    ap.add_argument("--logdir", type=Path, default=DEFAULT_LOGDIR)
    ap.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not args.logdir.is_dir():
        if not args.quiet:
            print(f"✗ EMPTY  no logdir at {args.logdir} (root={ROOT}) — "
                  "nothing to transcribe or check against.")
        return 2
    fn = emit if args.emit else verify
    return fn(args.logdir, args.vault, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
