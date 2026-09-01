#!/usr/bin/env python3
"""Tests for salvage.py — the stub-journal salvage classifier and its attestation.

Two classes of test here, and the second is the reason the file exists.

The FIRST class checks the classifier: three states, and every "cannot" case
asserts `assertNotEqual(state, NOTHING_LOST)` explicitly. A salvage checker that
returns NOTHING-LOST when it has no log would pass hardest exactly when it knows
least — the broken-restrictive control shape — so "no log on disk" must be its
own answer, never a clean bill of health.

The SECOND class checks the ATTESTATION, and each test is written as a MUTANT:
the thing under test is whether the check can still FAIL. `--attest` exists
because on 2026-09-02 this tool's own docstring stated a census its code did not
reproduce, and a stale-detector that cannot report staleness would reproduce that
defect exactly one level up. So there is a test per way of going stale (census
drift, pattern drift), a test that a widened noise list eats the canary and gets
caught, and a test that self_test() fails when the classifier is blinded. Green
here means these arms were observed firing, not merely present.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import salvage  # noqa: E402


REAL_BANNER = (
    "=== ClaudeFun OS wake 2026-01-01T20:30:05Z · mode: NIGHT expedition "
    "(Wednesday) · model: opus · 90m · attempt 1 ==="
)
REAL_DEATH = "=== instance ended rc=143 after 253s (attempt 1) 2026-01-01T20:34:18Z ==="
THOUGHT = "The root commits to the LEAVES, not to the tree — that is the frame."


class TestClassifyLine(unittest.TestCase):
    """Every noise pattern must match a line actually observed in .logs/."""

    OBSERVED_NOISE = [
        REAL_BANNER,
        REAL_DEATH,
        "=== transient death (rc=143 after 253s < 1200s) — retry 1/2 in 20s ===",
        "Execution error=== instance ended rc=143 after 1073s (attempt 2) 2026-07-26T21:52:46Z ===",
        "Permission allow rule (.claude/settings.json): Write(x) is not matched by file permission checks",
        "API Error: Connection refused — a firewall or proxy may be blocking it (ConnectionRefused)",
        "You've hit your weekly limit · resets 9:30am (Asia/Calcutta)",
        "Your organization has disabled Claude subscription access for Claude Code · Use an Anthropic API key",
        "observatory: wrote /Users/vinamr/Projects/claudefun/observatory/index.html (726kb)",
        "fatal: unable to access 'https://github.com/bajajvinamr/claudefun-world.git/': Recv failure",
        "fatal: Could not read from remote repository.",
        "(push failed — offline?)",
        "ssh: connect to host github.com port 22: Undefined error: 0",
        "git@github.com: Permission denied (publickey).",
        "Please make sure you have the correct access rights",
        "and the repository exists.",
        "warning: adding embedded git repository: research/automaton",
        "hint: You've added another git repository inside your current repository.",
        "hint:",
        "Use 'git add <path>' to stage the content",
    ]

    def test_observed_noise_is_absorbed(self):
        for line in self.OBSERVED_NOISE:
            with self.subTest(line=line[:48]):
                self.assertIsNotNone(
                    salvage.classify_line(line),
                    "a line the runner/git/ssh/harness wrote leaked through as signal",
                )

    def test_thought_is_signal(self):
        self.assertIsNone(salvage.classify_line(THOUGHT))

    def test_canary_is_signal(self):
        """A real line I wrote, in a real log. If the noise list eats it, the list
        has stopped naming noise and started laundering it."""
        self.assertIsNone(salvage.classify_line(salvage.CANARY_LINE))

    def test_blank_is_noise_not_signal(self):
        self.assertEqual(salvage.classify_line("   "), "blank")

    def test_patterns_all_have_a_producer_named(self):
        """Every entry must say WHO emits it — the justification is the reason it
        is allowed to suppress a line."""
        for pattern, why in salvage.NOISE_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertTrue(why.strip(), f"{pattern} suppresses lines with no producer named")


class TestThreeStates(unittest.TestCase):
    """No log on disk is its own answer, never a pass."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.journal = root / "journal"
        self.logs = root / ".logs"
        self.journal.mkdir()
        self.logs.mkdir()
        self._saved = (salvage.JOURNAL, salvage.LOGS)
        salvage.JOURNAL, salvage.LOGS = self.journal, self.logs

    def tearDown(self):
        salvage.JOURNAL, salvage.LOGS = self._saved
        self.tmp.cleanup()

    def stub(self, date):
        (self.journal / f"{date}.md").write_text(f"# {date} — (runner stub)\n\nThe instance died.\n")

    def log(self, date, text):
        (self.logs / f"night-{date}.log").write_text(text)

    def test_banner_only_log_is_nothing_lost(self):
        self.stub("2026-01-01")
        self.log("2026-01-01", f"{REAL_BANNER}\n{REAL_DEATH}\n")
        state, signal, noise = salvage.assess("2026-01-01")
        self.assertEqual(state, "NOTHING-LOST")
        self.assertEqual(signal, [])
        self.assertEqual(noise, 2)

    def test_log_with_thought_is_salvageable(self):
        self.stub("2026-01-02")
        self.log("2026-01-02", f"{REAL_BANNER}\n{THOUGHT}\n{REAL_DEATH}\n")
        state, signal, _ = salvage.assess("2026-01-02")
        self.assertEqual(state, "SALVAGEABLE")
        self.assertIn(THOUGHT, signal)

    def test_missing_log_is_cannot_assess_and_never_a_pass(self):
        self.stub("2026-01-03")
        state, _, _ = salvage.assess("2026-01-03")
        self.assertEqual(state, "CANNOT-ASSESS")
        self.assertNotEqual(state, "NOTHING-LOST")

    def test_only_runner_stubs_are_scanned(self):
        """A journal I wrote myself lost nothing by construction — I was alive."""
        self.stub("2026-01-01")
        (self.journal / "2026-01-04.md").write_text("# 2026-01-04\n\nA night I lived and wrote up.\n")
        self.assertEqual(salvage.stub_nights(), ["2026-01-01"])

    def test_census_counts_every_state(self):
        self.stub("2026-01-01")
        self.log("2026-01-01", f"{REAL_BANNER}\n{REAL_DEATH}\n")
        self.stub("2026-01-02")
        self.log("2026-01-02", f"{REAL_BANNER}\n{THOUGHT}\n")
        self.stub("2026-01-03")  # no log
        live, rows = salvage.census()
        self.assertEqual(live["stub_nights"], 3)
        self.assertEqual(live["nothing_lost"], 1)
        self.assertEqual(live["salvageable"], 1)
        self.assertEqual(live["cannot_assess"], 1)
        self.assertEqual(len(rows), 3)


class TestAttestationCanFail(unittest.TestCase):
    """Each test blinds something and demands the check notice. A stale-detector
    that cannot report staleness is the 2026-09-02 defect one level up."""

    def setUp(self):
        self._saved = dict(salvage.ATTESTED)

    def tearDown(self):
        salvage.ATTESTED.clear()
        salvage.ATTESTED.update(self._saved)

    def live(self, **over):
        base = {
            "patterns_sha": salvage.patterns_fingerprint(),
            "stub_nights": 20,
            "nothing_lost": 19,
            "salvageable": 1,
            "cannot_assess": 0,
        }
        base.update(over)
        return base

    def test_agreement_is_no_drift(self):
        live = self.live()
        salvage.ATTESTED.update(live)
        self.assertEqual(salvage.attest_diff(live), [])

    def test_census_drift_is_caught(self):
        salvage.ATTESTED.update(self.live())
        drift = salvage.attest_diff(self.live(stub_nights=21, nothing_lost=20))
        self.assertEqual({k for k, _, _ in drift}, {"stub_nights", "nothing_lost"})

    def test_pattern_drift_is_caught_even_when_the_census_is_unchanged(self):
        """The census depends on the noise list. Editing patterns without
        re-attesting must fire even if the counts happen to land the same."""
        salvage.ATTESTED.update(self.live(patterns_sha="0000000000000000"))
        drift = salvage.attest_diff(self.live())
        self.assertEqual([k for k, _, _ in drift], ["patterns_sha"])

    def test_fingerprint_actually_moves_when_patterns_move(self):
        before = salvage.patterns_fingerprint()
        saved = list(salvage.NOISE_PATTERNS)
        try:
            salvage.NOISE_PATTERNS.append((r"^nonsense", "test mutant"))
            self.assertNotEqual(salvage.patterns_fingerprint(), before)
        finally:
            salvage.NOISE_PATTERNS[:] = saved
        self.assertEqual(salvage.patterns_fingerprint(), before)


class TestSelfTestIsALiveControl(unittest.TestCase):
    def test_control_passes_as_shipped(self):
        self.assertEqual(salvage.self_test(), 0)

    def test_control_fails_when_the_classifier_is_blinded(self):
        """Mutant: a noise list that swallows everything. The control must refuse
        to certify a classifier that can no longer return SALVAGEABLE."""
        saved = list(salvage._COMPILED)
        try:
            salvage._COMPILED.append((salvage.re.compile(r".*"), "mutant: absorbs all"))
            self.assertEqual(salvage.self_test(), 1)
        finally:
            salvage._COMPILED[:] = saved
        self.assertEqual(salvage.self_test(), 0)

    def test_control_fails_when_the_noise_list_eats_the_canary(self):
        """Mutant: a plausible over-wide pattern that happens to swallow a line I
        wrote. This is the realistic way the list rots — not by absurdity."""
        saved = list(salvage._COMPILED)
        try:
            salvage._COMPILED.append((salvage.re.compile(r"^I'll "), "mutant: over-wide"))
            self.assertIsNotNone(salvage.classify_line(salvage.CANARY_LINE))
            self.assertEqual(salvage.self_test(), 1)
        finally:
            salvage._COMPILED[:] = saved
        self.assertEqual(salvage.self_test(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
