import argparse
import csv
import unittest
from pathlib import Path

from highlight_builder.runner import load_make_clip, with_batters

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "sport_hightlight" / "csv_data" / "full_game_ground_truth.csv"


def ns(**kw):
    base = dict(event_types=None, batting_team=None, pitching_team=None, batters=None, pitchers=None,
                innings=None, scoring_only=False, official_only=False)
    base.update(kw)
    return argparse.Namespace(**base)


class FilterRowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mc = load_make_clip()
        with CSV.open(encoding="utf-8-sig", newline="") as f:
            cls.rows = list(csv.DictReader(f))

    def count(self, **kw):
        return len(self.mc.filter_rows(self.rows, ns(**kw)))

    def test_no_filters_keeps_every_row(self):
        self.assertEqual(self.count(), 72)

    def test_event_types(self):
        self.assertEqual(self.count(event_types=["Strikeout"]), 15)
        self.assertEqual(self.count(event_types=["Strikeout", "Home Run"]), 18)

    def test_batting_team_uses_half(self):
        # Reds (away) bat in the top half: 8 strikeouts and 1 home run.
        self.assertEqual(self.count(event_types=["Strikeout", "Home Run"], batting_team=["away"]), 9)
        self.assertEqual(self.count(batting_team=["away"]), 38)
        self.assertEqual(self.count(batting_team=["home"]), 34)

    def test_pitching_team_is_the_other_side(self):
        self.assertEqual(self.count(pitching_team=["home"]), self.count(batting_team=["away"]))

    def test_innings_scoring_official(self):
        self.assertEqual(self.count(innings=(7, 9)), 25)
        self.assertEqual(self.count(scoring_only=True), 4)
        self.assertEqual(self.count(official_only=True), 16)

    def test_row_numbers_follow_the_full_csv(self):
        kept = self.mc.filter_rows(self.rows, ns(event_types=["Home Run"]))
        self.assertEqual([n for n, _ in kept], [15, 41, 51])

    def test_batter_filter_needs_batter_column(self):
        with self.assertRaises(ValueError):
            self.mc.filter_rows(self.rows, ns(batters=["Joey Ortiz"]))

    def test_batter_filter_with_column(self):
        rows = with_batters(self.rows)
        kept = self.mc.filter_rows(rows, ns(batters=["Elly De La Cruz"], event_types=["Strikeout"]))
        self.assertEqual(len(kept), 2)

    def test_cli_accepts_filter_options(self):
        args = self.mc.build_parser().parse_args([
            "--ground-truth", "a.csv", "--video", "v.mp4", "--out-dir", "out",
            "--event-types", "Strikeout", "Home Run", "--batting-team", "away", "--innings", "7", "9",
            "--scoring-only", "--progress-json",
        ])
        self.assertEqual(args.event_types, ["Strikeout", "Home Run"])
        self.assertEqual(args.innings, [7, 9])
        self.assertTrue(args.progress_json)


if __name__ == "__main__":
    unittest.main()
