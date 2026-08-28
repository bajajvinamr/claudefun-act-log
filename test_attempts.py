#!/usr/bin/env python3
"""Tests for attempts.py — the runner-log ↔ WAL join.

The load-bearing tests here are the ones that prove the THIRD state is real. A
join that returns AGREED whenever it has no runner log would pass hardest exactly
when it knows least, which is the broken-restrictive control shape I found on
2026-08-16 and have now hit twice. So every "cannot" case asserts
`assertNotEqual(state, AGREED)` explicitly, not just the positive equality —
a test that only checks the intended value cannot tell a correct verdict from a
constant.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attempts import join, parse_runner_log, wal_brackets  # noqa: E402

MODERN_WAKE = (
    "=== ClaudeFun OS wake {ts} · mode: NIGHT expedition (Friday) · model: opus "
    "· 90m · attempt {k} ==="
)
MODERN_END = "=== instance ended rc={rc} after {s}s (attempt {k}) {ts} ==="
RETRY = "=== transient death (rc={rc} after {s}s < 1200s) — retry 1/2 in 20s ==="
LEGACY_WAKE = (
    "=== ClaudeFun OS wake {ts} · mode: NIGHT expedition (Friday) · model: opus · 90m ==="
)
LEGACY_END = "=== instance ended rc={rc} {ts} ==="


class Bed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.logdir = self.root / ".logs"
        self.logdir.mkdir()
        self.ledger = self.root / "ACTS.jsonl"
        self.ledger.write_text("")

    def tearDown(self):
        self._tmp.cleanup()

    def log(self, night: str, *lines: str) -> Path:
        p = self.logdir / f"night-{night}.log"
        p.write_text("\n".join(lines) + "\n")
        return p

    def wal(self, *records: dict) -> None:
        self.ledger.write_text("".join(json.dumps(r) + "\n" for r in records))

    def bracket(self, night: str, wakes: int = 1, closes: int = 1) -> list[dict]:
        out = [{"kind": "WAKE", "night": night, "ts": f"{night}T02:00:00+0530"}] * 0
        for i in range(wakes):
            out.append({"kind": "WAKE", "night": night, "ts": f"{night}T02:0{i}:00+0530"})
        for i in range(closes):
            out.append({"kind": "CLOSE", "night": night, "ts": f"{night}T03:1{i}:00+0530"})
        return out

    def run_join(self, night: str | None = None):
        return join(self.logdir, self.ledger, night)


class TestParse(Bed):
    def test_modern_banner_yields_attempt_rc_and_duration(self):
        self.log(
            "2026-08-29",
            MODERN_WAKE.format(ts="2026-08-28T20:30:05Z", k=1),
            MODERN_END.format(rc=143, s=330, k=1, ts="2026-08-28T20:35:35Z"),
            RETRY.format(rc=143, s=330),
            MODERN_WAKE.format(ts="2026-08-28T20:35:56Z", k=2),
        )
        a = parse_runner_log(self.logdir / "night-2026-08-29.log")
        self.assertEqual([x["attempt"] for x in a], [1, 2])
        self.assertEqual(a[0]["rc"], 143)
        self.assertEqual(a[0]["elapsed_s"], 330)
        self.assertTrue(a[0]["retried"])

    def test_an_attempt_with_no_end_banner_keeps_rc_none_and_never_zero(self):
        """A tidy 0 here would assert the instance exited cleanly. It is unmeasured.

        This is witness.py's -1 rule, restated: a default that reads like an
        observation IS a fabricated observation."""
        self.log("2026-08-29", MODERN_WAKE.format(ts="2026-08-28T20:30:05Z", k=1))
        a = parse_runner_log(self.logdir / "night-2026-08-29.log")
        self.assertIsNone(a[0]["rc"])
        self.assertNotEqual(a[0]["rc"], 0)
        self.assertIsNone(a[0]["elapsed_s"])

    def test_legacy_pre_retry_banner_still_parses_as_attempt_one(self):
        """11 July nights use this form and the WAL cannot see any of them."""
        self.log(
            "2026-07-10",
            LEGACY_WAKE.format(ts="2026-07-09T20:30:05Z"),
            "some night prose the parser must ignore",
            LEGACY_END.format(rc=0, ts="2026-07-09T20:55:53Z"),
        )
        a = parse_runner_log(self.logdir / "night-2026-07-10.log")
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["attempt"], 1)
        self.assertEqual(a[0]["rc"], 0)
        self.assertIsNone(a[0]["elapsed_s"])  # legacy banner carried no duration

    def test_prose_alone_parses_to_no_attempts(self):
        self.log("2026-07-10", "just a night's worth of output", "no banners here")
        self.assertEqual(parse_runner_log(self.logdir / "night-2026-07-10.log"), [])


class TestJoin(Bed):
    def test_one_clean_attempt_with_a_full_bracket_agrees(self):
        self.log(
            "2026-08-27",
            MODERN_WAKE.format(ts="2026-08-26T20:30:05Z", k=1),
            MODERN_END.format(rc=0, s=3409, k=1, ts="2026-08-26T21:26:54Z"),
        )
        self.wal(*self.bracket("2026-08-27"))
        rc, rows = self.run_join()
        self.assertEqual(rows[0]["state"], "AGREED")
        self.assertEqual(rc, 0)

    def test_a_retried_night_is_divergent_and_names_the_death(self):
        """Tonight's shape. The WAL keeps the LAST wake, so `--nights` renders one
        row and the dead attempt vanishes. This is the whole reason the file exists."""
        self.log(
            "2026-08-29",
            MODERN_WAKE.format(ts="2026-08-28T20:30:05Z", k=1),
            MODERN_END.format(rc=143, s=330, k=1, ts="2026-08-28T20:35:35Z"),
            RETRY.format(rc=143, s=330),
            MODERN_WAKE.format(ts="2026-08-28T20:35:56Z", k=2),
        )
        self.wal(*self.bracket("2026-08-29", wakes=2, closes=1))
        rc, rows = self.run_join("2026-08-29")
        self.assertEqual(rows[0]["state"], "DIVERGENT")
        self.assertNotEqual(rows[0]["state"], "AGREED")
        self.assertEqual(rc, 1)
        joined = " ".join(rows[0]["findings"])
        self.assertIn("RETRIED", joined)
        self.assertIn("rc=143", joined)
        self.assertIn("330s", joined)

    def test_a_death_the_wal_cannot_see_is_reported_even_with_one_attempt(self):
        """08-28: a single attempt, killed at 1800s, with a WAKE and no CLOSE."""
        self.log(
            "2026-08-28",
            MODERN_WAKE.format(ts="2026-08-27T20:30:05Z", k=1),
            MODERN_END.format(rc=1, s=1800, k=1, ts="2026-08-27T21:00:05Z"),
        )
        self.wal(*self.bracket("2026-08-28", wakes=1, closes=0))
        rc, rows = self.run_join("2026-08-28")
        self.assertEqual(rows[0]["state"], "DIVERGENT")
        self.assertIn("DIED rc=1 after 1800s", " ".join(rows[0]["findings"]))

    def test_attempts_with_no_wal_wake_at_all_are_divergent_not_agreed(self):
        """Every night before 2026-08-22. The runner is the ONLY witness there."""
        self.log(
            "2026-08-20",
            MODERN_WAKE.format(ts="2026-08-19T20:30:04Z", k=1),
            MODERN_END.format(rc=143, s=19659, k=1, ts="2026-08-20T01:57:43Z"),
        )
        rc, rows = self.run_join("2026-08-20")
        self.assertEqual(rows[0]["state"], "DIVERGENT")
        self.assertEqual(rows[0]["wakes"], 0)
        self.assertIn("NO WAKE", " ".join(rows[0]["findings"]))

    def test_more_wal_wakes_than_banners_is_divergent(self):
        """A WAKE with nothing outside to stand on — the tamper/clock-drift arm."""
        self.log(
            "2026-08-26",
            MODERN_WAKE.format(ts="2026-08-25T20:30:05Z", k=1),
            MODERN_END.format(rc=0, s=2970, k=1, ts="2026-08-25T21:19:35Z"),
        )
        self.wal(*self.bracket("2026-08-26", wakes=3, closes=1))
        rc, rows = self.run_join("2026-08-26")
        self.assertEqual(rows[0]["state"], "DIVERGENT")
        self.assertIn("nothing outside to stand on", " ".join(rows[0]["findings"]))


class TestThirdState(Bed):
    """The arm that makes this a check rather than a rubber stamp."""

    def test_a_bracketed_night_with_no_runner_log_cannot_join_and_does_not_pass(self):
        self.wal(*self.bracket("2026-08-25"))
        rc, rows = self.run_join("2026-08-25")
        self.assertEqual(rows[0]["state"], "CANNOT_JOIN")
        self.assertNotEqual(rows[0]["state"], "AGREED")
        self.assertEqual(rc, 2)

    def test_a_log_with_no_parseable_banner_cannot_join(self):
        """Banner-format drift must surface as 'I could not read', never as 'fine'."""
        self.log("2026-08-25", "=== SOMETHING ELSE ENTIRELY ===", "prose")
        self.wal(*self.bracket("2026-08-25"))
        rc, rows = self.run_join("2026-08-25")
        self.assertEqual(rows[0]["state"], "CANNOT_JOIN")
        self.assertIn("format may have drifted", " ".join(rows[0]["findings"]))
        self.assertEqual(rc, 2)

    def test_no_logs_and_no_brackets_is_cannot_join_not_a_clean_bill(self):
        rc, rows = self.run_join()
        self.assertEqual(rows, [])
        self.assertEqual(rc, 2)
        self.assertNotEqual(rc, 0)

    def test_cannot_join_outranks_divergent_in_the_exit_code(self):
        """Unknown must not be reported as merely-disagreeing; it is a bigger hole."""
        self.log(
            "2026-08-29",
            MODERN_WAKE.format(ts="2026-08-28T20:30:05Z", k=1),
            MODERN_END.format(rc=143, s=330, k=1, ts="2026-08-28T20:35:35Z"),
        )
        self.wal(*self.bracket("2026-08-29", wakes=1, closes=0), *self.bracket("2026-08-25"))
        rc, rows = self.run_join()
        states = {r["night"]: r["state"] for r in rows}
        self.assertEqual(states["2026-08-29"], "DIVERGENT")
        self.assertEqual(states["2026-08-25"], "CANNOT_JOIN")
        self.assertEqual(rc, 2)


class TestBrackets(Bed):
    def test_wal_brackets_keeps_every_wake_not_only_the_last(self):
        """act.py:252 does `slot['wake'] = r`, which is the erasure being fixed."""
        self.wal(*self.bracket("2026-08-29", wakes=2, closes=1))
        b = wal_brackets([json.loads(l) for l in self.ledger.read_text().splitlines()])
        self.assertEqual(len(b["2026-08-29"]["wakes"]), 2)
        self.assertEqual(len(b["2026-08-29"]["closes"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
