#!/usr/bin/env python3
"""Tests for witness.py — built against throwaway git repos, not against my own history.

Testing the witness on my real repo would be a tautology: the only history available is
the one I hope is clean, so a green run proves the world is fine and says nothing about
whether the instrument works. Each test here BUILDS a repo with a known property and
checks the instrument reports it.

Both arms, always: tests that must pass AND tests that must fail. A control that cannot
fail is not a control (2026-08-17).

Run: python3 builds/wal/test_witness.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from witness import (  # noqa: E402
    CANNOT_WITNESS,
    CONTINUOUS,
    TORN,
    GitUnavailable,
    blob_lines,
    commits_touching,
    render,
    witness,
)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout


class Repo:
    """A throwaway git repo with a controllable file history."""

    def __init__(self, tmp: Path):
        self.path = tmp
        tmp.mkdir(parents=True, exist_ok=True)
        git(tmp, "init", "-q")
        git(tmp, "config", "user.email", "test@example.invalid")
        git(tmp, "config", "user.name", "test")

    def commit(self, relpath: str, text: str, message: str = "c") -> None:
        target = self.path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        git(self.path, "add", relpath)
        git(self.path, "commit", "-q", "-m", message)

    def write_only(self, relpath: str, text: str) -> None:
        """Change the working tree without committing."""
        (self.path / relpath).write_text(text, encoding="utf-8")


class WitnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- the passing arm -------------------------------------------------

    def test_pure_appends_are_continuous(self):
        self.repo.commit("log.jsonl", "a\n")
        self.repo.commit("log.jsonl", "a\nb\n")
        self.repo.commit("log.jsonl", "a\nb\nc\n")
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.state, CONTINUOUS)
        self.assertEqual(w.tears, [])
        self.assertEqual(w.committed_lines, 3)
        self.assertEqual(w.unwitnessed_tail, 0)

    def test_uncommitted_tail_is_counted_not_flagged(self):
        self.repo.commit("log.jsonl", "a\n")
        self.repo.commit("log.jsonl", "a\nb\n")
        self.repo.write_only("log.jsonl", "a\nb\nc\nd\n")
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.state, CONTINUOUS, "an uncommitted append is not a tear")
        self.assertEqual(w.unwitnessed_tail, 2)

    # ---- the failing arm, which is the point -----------------------------

    def test_rewritten_past_line_is_torn(self):
        self.repo.commit("log.jsonl", "a\nb\n")
        self.repo.commit("log.jsonl", "a\nZ\nc\n")  # line 2 rewritten
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.state, TORN)
        self.assertEqual(len(w.tears), 1)
        self.assertEqual(w.tears[0].line_no, 2)
        self.assertIn("rewritten", w.tears[0].reason)

    def test_truncation_is_torn_and_names_the_length_change(self):
        self.repo.commit("log.jsonl", "a\nb\nc\n")
        self.repo.commit("log.jsonl", "a\n")  # two entries deleted
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.state, TORN)
        self.assertIn("truncated 3 -> 1", w.tears[0].reason)

    def test_tear_names_both_commits(self):
        self.repo.commit("log.jsonl", "a\nb\n")
        self.repo.commit("log.jsonl", "a\nZ\n")
        w = witness(self.repo.path, "log.jsonl")
        self.assertTrue(w.tears[0].parent and w.tears[0].child)
        self.assertNotEqual(w.tears[0].parent, w.tears[0].child)

    def test_every_tear_in_a_long_history_is_reported(self):
        self.repo.commit("log.jsonl", "a\nb\n")
        self.repo.commit("log.jsonl", "a\nZ\n")
        self.repo.commit("log.jsonl", "a\nZ\nc\n")
        self.repo.commit("log.jsonl", "a\nY\nc\n")
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(len(w.tears), 2, "reporting only the first tear hides the rest")

    # ---- the third state, which two-state checkers get wrong -------------

    def test_single_commit_cannot_witness(self):
        self.repo.commit("log.jsonl", "a\nb\nc\n")
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.state, CANNOT_WITNESS)
        self.assertNotEqual(w.state, CONTINUOUS, "one snapshot is not an append-only proof")

    def test_single_commit_does_not_claim_a_tail_it_never_measured(self):
        self.repo.commit("log.jsonl", "a\nb\nc\n")
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.unwitnessed_tail, -1)
        self.assertIsNone(w.committed_lines)
        self.assertIn("NEVER MEASURED", render(w))

    def test_untracked_path_cannot_witness(self):
        self.repo.commit("other.txt", "x\n")
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.state, CANNOT_WITNESS)

    def test_not_a_repo_cannot_witness(self):
        with tempfile.TemporaryDirectory() as plain:
            w = witness(Path(plain), "log.jsonl")
            self.assertEqual(w.state, CANNOT_WITNESS)
            self.assertNotEqual(w.state, CONTINUOUS)

    def test_cannot_witness_render_says_it_is_not_a_pass(self):
        self.repo.commit("log.jsonl", "a\n")
        self.assertIn("not a pass", render(witness(self.repo.path, "log.jsonl")).lower())

    # ---- the working file diverging from HEAD ----------------------------

    def test_working_file_diverging_from_head_gives_undefined_tail(self):
        self.repo.commit("log.jsonl", "a\nb\n")
        self.repo.commit("log.jsonl", "a\nb\nc\n")
        self.repo.write_only("log.jsonl", "a\nQ\nc\n")  # edited, not appended
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.unwitnessed_tail, -1)
        self.assertIn("UNDEFINED", render(w))

    # ---- the object store is the source, not the disk --------------------

    def test_reads_history_not_the_working_file(self):
        """Deleting the working file must not erase the witnessed history.

        This is the property that makes the instrument out-of-band: if it read the
        file, a deletion would silently shrink the evidence to nothing.
        """
        self.repo.commit("log.jsonl", "a\n")
        self.repo.commit("log.jsonl", "a\nb\n")
        (self.repo.path / "log.jsonl").unlink()
        w = witness(self.repo.path, "log.jsonl")
        self.assertEqual(w.state, CONTINUOUS)
        self.assertEqual(w.committed_lines, 2)
        self.assertEqual(w.working_lines, 0)

    def test_blob_lines_reads_the_old_version_not_the_new(self):
        self.repo.commit("log.jsonl", "a\n")
        first = commits_touching(self.repo.path, "log.jsonl")[0]
        self.repo.commit("log.jsonl", "a\nb\n")
        self.assertEqual(blob_lines(self.repo.path, first, "log.jsonl"), ["a"])

    def test_git_failure_raises_rather_than_returning_empty(self):
        with self.assertRaises(GitUnavailable):
            blob_lines(self.repo.path, "0" * 40, "log.jsonl")

    # ---- exit codes ------------------------------------------------------

    def test_main_exit_codes(self):
        import witness as mod

        self.repo.commit("log.jsonl", "a\n")
        self.repo.commit("log.jsonl", "a\nb\n")
        self.assertEqual(mod.main(["--repo", str(self.repo.path), "log.jsonl"]), CONTINUOUS)
        self.repo.commit("log.jsonl", "Z\n")
        self.assertEqual(mod.main(["--repo", str(self.repo.path), "log.jsonl"]), TORN)
        self.assertEqual(
            mod.main(["--repo", str(self.repo.path), "nope.jsonl"]), CANNOT_WITNESS
        )

    def test_torn_outranks_cannot_witness_in_the_exit_code(self):
        import witness as mod

        self.repo.commit("log.jsonl", "a\nb\n")
        self.repo.commit("log.jsonl", "Z\n")
        rc = mod.main(["--repo", str(self.repo.path), "log.jsonl", "missing.log"])
        self.assertEqual(rc, TORN, "a real tear must not be masked by an unwitnessable path")


class RemoteWitnessTests(unittest.TestCase):
    """The remote path, exercised against a local bare repo over file://.

    No network in the tests. What is under test is the CLONE-AND-REPLAY logic and the
    refusal to invent a working-tree measurement for a repo that has no working tree —
    not GitHub's availability.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origin = Repo(root / "origin")
        self.url = str(root / "origin")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_remote_append_only_history_is_continuous(self):
        from witness import witness_remote

        self.origin.commit("log.jsonl", "a\n")
        self.origin.commit("log.jsonl", "a\nb\n")
        w = witness_remote(self.url, "log.jsonl")
        self.assertEqual(w.state, CONTINUOUS)
        self.assertEqual(w.committed_lines, 2)

    def test_remote_tear_is_detected(self):
        from witness import witness_remote

        self.origin.commit("log.jsonl", "a\nb\n")
        self.origin.commit("log.jsonl", "a\nZ\n")
        w = witness_remote(self.url, "log.jsonl")
        self.assertEqual(w.state, TORN)
        self.assertEqual(w.tears[0].line_no, 2)

    def test_remote_never_reports_a_working_tree_it_does_not_have(self):
        """A bare clone has no files on disk; a measured 0 there would be a lie."""
        from witness import witness_remote

        self.origin.commit("log.jsonl", "a\n")
        self.origin.commit("log.jsonl", "a\nb\n")
        w = witness_remote(self.url, "log.jsonl")
        self.assertEqual(w.unwitnessed_tail, -1)
        self.assertIn("not applicable", w.note)

    def test_unfetchable_remote_cannot_witness_and_does_not_pass(self):
        from witness import witness_remote

        w = witness_remote(str(Path(self._tmp.name) / "does-not-exist"), "log.jsonl")
        self.assertEqual(w.state, CANNOT_WITNESS)
        self.assertNotEqual(w.state, CONTINUOUS)
        self.assertIn("could not fetch", w.note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
