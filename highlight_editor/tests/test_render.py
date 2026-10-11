import tempfile
import unittest
from pathlib import Path
from unittest import mock

from highlight_editor import media
from highlight_editor.project import exit_code_for
from highlight_editor.render import validate_project
from highlight_editor.tests.helpers import (
    FFMPEG_AVAILABLE,
    SKIP_REASON,
    frame_count,
    make_clip,
    make_odd_clip,
)


def error_codes(result):
    return [(error["code"], error["segment_id"], error["field"]) for error in result["errors"]]


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class ValidateProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        cls.clip_a = make_clip(cls.dir / "clips" / "a.mp4", size="640x360", rate="30000/1001", seconds=2)
        cls.frames_a = frame_count(cls.clip_a)
        cls.clip_odd = make_odd_clip(cls.dir / "clips" / "odd.mkv", width=853, height=481, seconds=1)
        bad = cls.dir / "clips" / "bad.mp4"
        bad.write_bytes(b"not a video")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def validate(self, *segments, **fields):
        data = {"output": fields.pop("output", "out/reel.mp4"), "segments": list(segments), **fields}
        return validate_project(data, base_dir=str(self.dir))

    def test_resolves_end_frame_and_settings(self):
        result = self.validate({"id": "a", "clip": "clips/a.mp4", "start_frame": 6}, output="fresh/dir/reel.mp4")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"], "fresh/dir/reel.mp4")
        self.assertEqual(result["settings"], {
            "width": 640, "height": 360, "fps": "30000/1001", "crf": 20, "preset": "fast",
        })
        self.assertEqual(result["segments"], [{
            "id": "a", "clip": "clips/a.mp4", "start_frame": 6, "end_frame": self.frames_a,
            "fps": "30000/1001", "frame_count": self.frames_a,
        }])
        self.assertEqual(result["estimated_duration"], round((self.frames_a - 6) * 1001 / 30000, 3))
        self.assertFalse((self.dir / "fresh").exists(), "validate 不可建立任何目錄或檔案")

    def test_settings_override_first_segment(self):
        result = self.validate({"clip": "clips/a.mp4"}, settings={"width": 320, "height": 180, "fps": 25, "crf": 23})
        self.assertEqual(result["settings"], {"width": 320, "height": 180, "fps": "25", "crf": 23, "preset": "fast"})

    def test_odd_dimension_first_clip_rounds_to_even(self):
        result = self.validate({"clip": "clips/odd.mkv"}, {"clip": "clips/a.mp4"})
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["settings"]["width"], result["settings"]["height"]), (852, 480))

    def test_end_frame_equal_to_frame_count_is_valid(self):
        result = self.validate({"clip": "clips/a.mp4", "end_frame": self.frames_a})
        self.assertTrue(result["ok"], result)

    def test_end_frame_beyond_clip(self):
        result = self.validate({"clip": "clips/a.mp4", "end_frame": self.frames_a + 1})
        self.assertEqual(error_codes(result), [("out_of_range", "1", "end_frame")])
        self.assertIn(str(self.frames_a), result["errors"][0]["message"])

    def test_start_frame_at_frame_count_with_open_end(self):
        result = self.validate({"clip": "clips/a.mp4", "start_frame": self.frames_a})
        self.assertEqual(error_codes(result), [("out_of_range", "1", "start_frame")])

    def test_missing_clip(self):
        result = self.validate({"clip": "clips/nope.mp4"})
        self.assertEqual(error_codes(result), [("clip_not_found", "1", "clip")])
        self.assertEqual(exit_code_for(result), 1)

    def test_garbage_clip(self):
        result = self.validate({"clip": "clips/bad.mp4"})
        self.assertEqual(error_codes(result), [("invalid_media", "1", "clip")])

    def test_output_exists_unless_overwrite(self):
        existing = self.dir / "out" / "exists.mp4"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"old")
        self.addCleanup(existing.unlink)
        result = self.validate({"clip": "clips/a.mp4"}, output="out/exists.mp4")
        self.assertEqual(error_codes(result), [("output_exists", None, "output")])
        result = self.validate({"clip": "clips/a.mp4"}, output="out/exists.mp4", overwrite=True)
        self.assertTrue(result["ok"], result)

    def test_output_same_as_clip(self):
        result = self.validate({"id": "a", "clip": "clips/a.mp4"}, output="clips/a.mp4", overwrite=True)
        self.assertEqual(error_codes(result), [("invalid_value", "a", "output")])

    def test_collects_structure_and_probe_errors_together(self):
        result = self.validate(
            {"id": "bad", "clip": "clips/a.mp4", "start_frame": -1},
            {"id": "missing", "clip": "clips/nope.mp4"},
            {"id": "long", "clip": "clips/a.mp4", "end_frame": 10 ** 6},
            settings={"crf": 99},
        )
        self.assertEqual(error_codes(result), [
            ("invalid_value", None, "settings.crf"),
            ("invalid_value", "bad", "start_frame"),
            ("clip_not_found", "missing", "clip"),
            ("out_of_range", "long", "end_frame"),
        ])

    def test_probes_each_clip_once(self):
        with mock.patch.object(media, "probe", wraps=media.probe) as probe:
            result = self.validate({"clip": "clips/a.mp4"}, {"clip": "./clips/a.mp4", "end_frame": 10})
        self.assertTrue(result["ok"], result)
        self.assertEqual(probe.call_count, 1)

    def test_ffmpeg_not_found(self):
        with mock.patch.object(media, "FFPROBE", "ffprobe-not-installed-xyz"):
            result = self.validate({"clip": "clips/a.mp4"})
        self.assertEqual(error_codes(result), [("ffmpeg_not_found", None, None)])
        self.assertEqual(exit_code_for(result), 2)

    def test_top_level_not_object(self):
        result = validate_project(["not", "an", "object"], base_dir=str(self.dir))
        self.assertEqual(error_codes(result), [("invalid_json", None, None)])


if __name__ == "__main__":
    unittest.main()
