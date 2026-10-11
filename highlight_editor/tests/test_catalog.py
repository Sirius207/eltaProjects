import tempfile
import unittest
from pathlib import Path

from highlight_editor.catalog import list_clips
from highlight_editor.tests.helpers import (
    FFMPEG_AVAILABLE,
    MANIFEST_HEADER,
    SKIP_REASON,
    frame_count,
    make_clip,
    manifest_row,
    write_manifest,
)

PROBE_FIELDS = ("fps", "frame_count", "width", "height", "has_audio")


class ListClipsWithoutMediaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_missing_manifest(self):
        result = list_clips(["nope.csv", "also_missing.csv"], base_dir=str(self.dir))
        self.assertFalse(result["ok"])
        self.assertEqual([(e["code"], e["field"]) for e in result["errors"]], [
            ("manifest_not_found", "manifest"), ("manifest_not_found", "manifest"),
        ])
        self.assertIn("nope.csv", result["errors"][0]["message"])

    def test_missing_clip_is_listed_with_null_probe_fields(self):
        write_manifest(self.dir / "manifest.csv", [manifest_row("clips/missing.mp4", is_official_highlight="False")])
        result = list_clips(["manifest.csv"], base_dir=str(self.dir))
        self.assertTrue(result["ok"])
        clip = result["clips"][0]
        self.assertFalse(clip["exists"])
        for key in PROBE_FIELDS:
            self.assertIsNone(clip[key], key)
        self.assertIs(clip["is_official_highlight"], False)
        self.assertEqual(clip["duration"], 20.0)

    def test_old_manifest_with_bom_and_empty_values(self):
        header = [name for name in MANIFEST_HEADER if name != "source_video"]
        row = manifest_row("clips/old.mp4", game_pk="", clip_duration_seconds="")
        write_manifest(self.dir / "old.csv", [row], header=header, bom=True)
        clip = list_clips(["old.csv"], base_dir=str(self.dir))["clips"][0]
        self.assertEqual(clip["clip"], "clips/old.mp4")
        self.assertEqual(clip["game_pk"], "")
        self.assertIsNone(clip["duration"])

    def test_multiple_manifests_keep_order(self):
        write_manifest(self.dir / "m1.csv", [manifest_row("a.mp4"), manifest_row("b.mp4")])
        write_manifest(self.dir / "m2.csv", [manifest_row("c.mp4")])
        result = list_clips(["m1.csv", "m2.csv"], base_dir=str(self.dir))
        self.assertEqual([(c["clip"], c["manifest"]) for c in result["clips"]], [
            ("a.mp4", "m1.csv"), ("b.mp4", "m1.csv"), ("c.mp4", "m2.csv"),
        ])


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class ListClipsWithMediaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_lists_rows_with_probe_fields(self):
        clip_rel = "clips/positive/event_0001_2bottom_Home_Run.mp4"
        clip_path = make_clip(self.dir / clip_rel, size="640x360", seconds=2)
        write_manifest(self.dir / "manifest.csv", [manifest_row(
            clip_rel, label="positive", event_type="Home Run", description="全壘打 測試",
            is_official_highlight="True", official_highlight_title="Official HR",
        )])
        result = list_clips(["manifest.csv"], base_dir=str(self.dir))
        self.assertTrue(result["ok"])
        self.assertEqual(result["clips"], [{
            "clip": clip_rel,
            "manifest": "manifest.csv",
            "label": "positive",
            "game_pk": "823734",
            "inning": "2",
            "half": "bottom",
            "event_type": "Home Run",
            "description": "全壘打 測試",
            "official_highlight_title": "Official HR",
            "is_official_highlight": True,
            "duration": 20.0,
            "exists": True,
            "fps": "30000/1001",
            "frame_count": frame_count(clip_path),
            "width": 640,
            "height": 360,
            "has_audio": True,
        }])

    def test_corrupt_clip_is_listed_without_probe_fields(self):
        bad = self.dir / "clips" / "bad.mp4"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"garbage")
        write_manifest(self.dir / "manifest.csv", [manifest_row("clips/bad.mp4")])
        clip = list_clips(["manifest.csv"], base_dir=str(self.dir))["clips"][0]
        self.assertTrue(clip["exists"])
        for key in PROBE_FIELDS:
            self.assertIsNone(clip[key], key)


if __name__ == "__main__":
    unittest.main()
