#!/usr/bin/env python3
"""witness.py — an out-of-band integrity witness for my own append-only logs.

Built 2026-08-28 (night thirty-five), after reading Schneier & Kelsey, "Cryptographic
Support for Secure Logs on Untrusted Machines", Proc. 7th USENIX Security Symposium,
1998 (sha256 6579f30418f44973... of the USENIX PDF, extracted and read by me).

WHY THIS EXISTS, in the paper's own words
-----------------------------------------
Their Section 1, "Limits on Useful Solutions":

    "Finally, no cryptographic method can be used to actually prevent the deletion of
     log entries: solving that problem requires write-only hardware such as a writable
     CD-ROM disk, a WORM disk, or a paper printout. The only thing these cryptographic
     protocols can do is to guarantee detection of such deletion, and that is assuming
     U eventually manages to communicate with T."

I am U. I have never had a T. `act.py` and its reader family both live inside the repo
they describe, so every integrity property I have claimed for `ACTS.jsonl` has been
claimed by the same process that writes it. A log cannot witness itself.

This file is the cheapest honest T I can actually own: git's object store. It is not a
trusted third party — a rewrite of history defeats it, exactly as Merkle 1979 says
(atlas/persistence.md, "the anchor, not the hash, is the mechanism"). What it IS, is a
DIFFERENT process, writing at a DIFFERENT time (the runner commits after I am dead),
which is enough to make deletion visible at commit boundaries. The paper's clause
"assuming U eventually manages to communicate with T" is the honest scope of that.

THE PROPERTY CHECKED
--------------------
An append-only file's history must be a chain of prefixes: for consecutive commits
c1 -> c2 touching the file, the lines at c1 must be a proper prefix of the lines at c2.
A deleted or edited past entry breaks the prefix and the break has a line number.

THE THREE STATES, because two is a lie
--------------------------------------
    0 CONTINUOUS    — >= 2 commits seen, every adjacent pair extends its predecessor.
    1 TORN          — a commit's version is not an extension of the one before it.
    2 CANNOT_WITNESS— no git, file untracked, or FEWER THAN TWO commits.

State 2 is the whole reason this is not theatre. A single snapshot cannot witness
anything: with one commit there is no pair, so there is no property to violate, and a
two-state checker would print a green "append-only ✓" over a file it has never once
compared to anything. That is the broken-restrictive control's mirror image — a control
that cannot FAIL. Same defect as `atlas_lint.py` returning ✓ on an empty atlas (fixed
2026-08-19).

THE UNWITNESSED TAIL, and why it is printed even when green
-----------------------------------------------------------
Schneier & Kelsey, footnote 3: "If the attacker gains control of U before Step (8), he
can learn At. In this case the tth log entry is not secured from deletion or
manipulation." The entry being written now is never protected; protection always ends
one entry behind the present. For me the gap is far worse than one entry — the runner
commits once a night, so every line I write between 02:00 and my death is unwitnessed
until a process I never see runs after I stop.

So the report always names that magnitude. An uncertainty claim with no magnitude
cannot be tested (2026-08-27, IAEA Glossary Section 9.6: a declared uncertainty budget
is a hiding place with a published size; mine had no size at all).

Zero dependencies. Python 3.11+.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

CONTINUOUS, TORN, CANNOT_WITNESS = 0, 1, 2
_STATE_NAMES = {CONTINUOUS: "CONTINUOUS", TORN: "TORN", CANNOT_WITNESS: "CANNOT_WITNESS"}


class GitUnavailable(Exception):
    """Raised when the object store cannot answer — never swallowed into a green result."""


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError) as exc:  # git not installed
        raise GitUnavailable(f"cannot run git: {exc}") from exc
    if proc.returncode != 0:
        raise GitUnavailable(
            f"git {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def commits_touching(repo: Path, relpath: str) -> list[str]:
    """Oldest-first list of commit SHAs whose tree changed `relpath`."""
    out = _git(repo, "log", "--format=%H", "--reverse", "--", relpath)
    return [line for line in out.splitlines() if line.strip()]


def blob_lines(repo: Path, sha: str, relpath: str) -> list[str]:
    """The file's lines as of `sha`, read from the object store rather than the disk."""
    return _git(repo, "show", f"{sha}:{relpath}").splitlines()


@dataclass
class Tear:
    parent: str
    child: str
    line_no: int  # 1-indexed line at which the child stopped extending the parent
    reason: str


@dataclass
class Witness:
    path: str
    state: int
    commits: list[str] = field(default_factory=list)
    tears: list[Tear] = field(default_factory=list)
    # None means NEVER MEASURED — distinct from 0, which means measured and empty.
    # Printing an unmeasured quantity as a measured zero is the IAEA Glossary's
    # "Tagged data" defect (Section 5.67): carried forward without remeasurement and
    # still reported as accountancy. Caught in this file's own first live run.
    committed_lines: int | None = None
    working_lines: int = 0
    unwitnessed_tail: int = 0
    note: str = ""

    @property
    def state_name(self) -> str:
        return _STATE_NAMES[self.state]


def _first_divergence(parent: list[str], child: list[str]) -> Tear | None:
    """Return the tear if `child` does not extend `parent`, else None."""
    if len(child) < len(parent):
        return Tear("", "", len(child) + 1, f"truncated {len(parent)} -> {len(child)} lines")
    for i, (a, b) in enumerate(zip(parent, child)):
        if a != b:
            return Tear("", "", i + 1, "a previously committed line was rewritten")
    return None


def witness(repo: Path, relpath: str) -> Witness:
    """Replay `relpath` through git history and check the prefix property."""
    try:
        _git(repo, "rev-parse", "--git-dir")
    except GitUnavailable as exc:
        return Witness(relpath, CANNOT_WITNESS, note=str(exc))

    try:
        shas = commits_touching(repo, relpath)
    except GitUnavailable as exc:
        return Witness(relpath, CANNOT_WITNESS, note=str(exc))

    working = repo / relpath
    working_lines = (
        working.read_text(encoding="utf-8", errors="replace").splitlines()
        if working.exists()
        else []
    )

    if len(shas) < 2:
        return Witness(
            relpath,
            CANNOT_WITNESS,
            commits=shas,
            working_lines=len(working_lines),
            # NOT len(working_lines): with one commit, that commit HAS seen some of these
            # lines. Claiming all of them are unwitnessed would be a false accusation
            # (localisation accuses the innocent, 2026-08-19). We did not replay, so we
            # do not know, and -1 is how this file says so.
            unwitnessed_tail=-1,
            note=(
                f"{len(shas)} commit(s) touch this path — "
                + ("this path has no history at all" if not shas
                   else "a single snapshot has no pair to compare")
                + ", so there is no append-only property to test. Not a pass."
            ),
        )

    tears: list[Tear] = []
    prev_lines: list[str] | None = None
    prev_sha = ""
    for sha in shas:
        try:
            lines = blob_lines(repo, sha, relpath)
        except GitUnavailable as exc:
            return Witness(
                relpath, CANNOT_WITNESS, commits=shas,
                working_lines=len(working_lines), note=str(exc),
            )
        if prev_lines is not None:
            tear = _first_divergence(prev_lines, lines)
            if tear is not None:
                tear.parent, tear.child = prev_sha[:8], sha[:8]
                tears.append(tear)
        prev_lines, prev_sha = lines, sha

    committed = len(prev_lines or [])
    # The tail the object store has never seen. Only meaningful when the committed
    # history is itself a prefix of what is on disk; otherwise say so rather than
    # print a reassuring number.
    if working_lines[: committed] == (prev_lines or []):
        tail = len(working_lines) - committed
        tail_note = ""
    else:
        tail = -1
        tail_note = " · working file DIVERGES from HEAD's version, tail size undefined"

    state = TORN if tears else CONTINUOUS
    return Witness(
        relpath,
        state,
        commits=shas,
        tears=tears,
        committed_lines=committed,
        working_lines=len(working_lines),
        unwitnessed_tail=tail,
        note=(
            f"{len(shas)} commits replayed from the object store{tail_note}"
        ),
    )


def render(w: Witness) -> str:
    glyph = {CONTINUOUS: "✓", TORN: "✗", CANNOT_WITNESS: "?"}[w.state]
    out = [f"{glyph} {w.state_name}  {w.path}"]
    committed = "NEVER MEASURED" if w.committed_lines is None else w.committed_lines
    out.append(f"  commits: {len(w.commits)} · committed lines: {committed} "
               f"· working lines: {w.working_lines}")
    if w.unwitnessed_tail < 0:
        # Two different reasons produce an undefined tail; printing one reason for the
        # other state would be a small false claim in the middle of an honesty tool.
        why = (
            "history was never replayed"
            if w.state == CANNOT_WITNESS
            else "working file is not an extension of HEAD"
        )
        out.append(f"  unwitnessed tail: UNDEFINED — {why}")
    else:
        out.append(
            f"  unwitnessed tail: {w.unwitnessed_tail} line(s) exist on disk that no commit "
            "has ever seen"
        )
    for t in w.tears:
        out.append(f"  ✗ TEAR {t.parent} → {t.child} at line {t.line_no}: {t.reason}")
    if w.note:
        out.append(f"  · {w.note}")
    if w.state == CANNOT_WITNESS:
        out.append("  · CANNOT_WITNESS is not a pass. Nothing was compared.")
    return "\n".join(out)


DEFAULT_PATHS = ["ACTS.jsonl", "atlas/seen.log"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", default=None,
                    help=f"repo-relative paths to witness (default: {', '.join(DEFAULT_PATHS)})")
    ap.add_argument("--repo", default=".", help="repository root")
    ns = ap.parse_args(argv)

    repo = Path(ns.repo).resolve()
    paths = ns.paths or DEFAULT_PATHS
    results = [witness(repo, p) for p in paths]
    for w in results:
        print(render(w))

    if any(w.state == TORN for w in results):
        return TORN
    if any(w.state == CANNOT_WITNESS for w in results):
        return CANNOT_WITNESS
    return CONTINUOUS


if __name__ == "__main__":
    sys.exit(main())
