import tempfile
import unittest
from pathlib import Path
from unittest import mock

import highlight_editor
from highlight_editor import media
from highlight_editor.project import exit_code_for
from highlight_editor.render import render_project, validate_project
from highlight_editor.tests.helpers import (
    FFMPEG_AVAILABLE,
    SKIP_REASON,
    audio_codec,
    frame_count,
    frame_png,
    leftover_work_dirs,
    make_clip,
    make_odd_clip,
    psnr,
    stream_duration,
    video_props,
)

ONE_FRAME_2997 = 1001 / 30000


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


class PackageExportTests(unittest.TestCase):
    def test_public_api(self):
        self.assertIs(highlight_editor.render_project, render_project)
        self.assertIs(highlight_editor.validate_project, validate_project)
        self.assertTrue(callable(highlight_editor.list_clips))
        self.assertIs(highlight_editor.exit_code_for, exit_code_for)


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class RenderProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        clips = cls.dir / "clips"
        cls.clip_a = make_clip(clips / "a.mp4", size="640x360", rate="30000/1001", seconds=3)
        cls.clip_b = make_clip(clips / "b_noaudio.mp4", size="320x240", rate="25", seconds=3, audio=False)
        cls.clip_c = make_clip(clips / "空白 與 中文" / "c.mp4", size="640x360", rate="30000/1001", seconds=2)
        cls.clip_short = make_clip(
            clips / "short_audio.mp4", size="640x360", rate="30000/1001", seconds=3, audio_seconds=1,
        )
        cls.clip_odd = make_odd_clip(clips / "odd.mkv", width=853, height=481, seconds=1)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def render(self, *segments, output=None, force=False, **fields):
        """force 對應 render_project(overwrite=...)；fields 是專案 JSON 的欄位（例如 overwrite=True）。"""
        output = output or f"out/{self._testMethodName}.mp4"
        events = []
        data = {"output": output, "segments": list(segments), **fields}
        result = render_project(data, base_dir=str(self.dir), overwrite=force, on_progress=events.append)
        return result, events, self.dir / output

    def assert_av_aligned(self, path):
        difference = abs(stream_duration(path, "a:0") - stream_duration(path, "v:0"))
        self.assertLess(difference, ONE_FRAME_2997, f"音訊與視訊長度差 {difference:.4f} 秒")

    def test_same_fps_segments_are_frame_exact(self):
        result, events, out = self.render(
            {"id": "x", "clip": "clips/a.mp4", "start_frame": 15, "end_frame": 45},
            {"id": "y", "clip": "clips/空白 與 中文/c.mp4", "start_frame": 10, "end_frame": 30},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame_count"], 50)
        self.assertEqual(frame_count(out), 50)
        self.assertEqual(result["segments"], [
            {"id": "x", "clip": "clips/a.mp4", "start_frame": 15, "end_frame": 45,
             "output_start_frame": 0, "output_end_frame": 30,
             "output_start": 0.0, "output_end": round(30 * ONE_FRAME_2997, 3)},
            {"id": "y", "clip": "clips/空白 與 中文/c.mp4", "start_frame": 10, "end_frame": 30,
             "output_start_frame": 30, "output_end_frame": 50,
             "output_start": round(30 * ONE_FRAME_2997, 3), "output_end": round(50 * ONE_FRAME_2997, 3)},
        ])
        self.assertEqual(result["duration"], round(50 * ONE_FRAME_2997, 3))
        self.assertEqual(result["settings"], {
            "width": 640, "height": 360, "fps": "30000/1001", "crf": 20, "preset": "fast",
        })
        self.assertEqual(video_props(out), (640, 360, "30000/1001"))
        self.assertEqual(audio_codec(out), "aac")
        self.assert_av_aligned(out)
        self.assertEqual([event["event"] for event in events], [
            "segment_start", "segment_done", "segment_start", "segment_done", "concat_start", "done",
        ])
        self.assertEqual(events[0], {"event": "segment_start", "index": 1, "total": 2, "segment_id": "x"})
        self.assertEqual(events[3], {"event": "segment_done", "index": 2, "total": 2, "segment_id": "y"})
        self.assertEqual(events[-1], {"event": "done", "output": f"out/{self._testMethodName}.mp4"})
        self.assertEqual(leftover_work_dirs(out.parent), [])

    def test_first_frame_matches_start_frame(self):
        result, _, out = self.render({"clip": "clips/a.mp4", "start_frame": 45, "end_frame": 60})
        self.assertTrue(result["ok"], result)
        pngs = self.dir / "png" / self._testMethodName
        out_first = frame_png(out, 0, pngs / "out0.png")
        source_45 = frame_png(self.clip_a, 45, pngs / "src45.png")
        source_46 = frame_png(self.clip_a, 46, pngs / "src46.png")
        self.assertGreater(psnr(out_first, source_45), psnr(out_first, source_46) + 10)

    def test_mixed_sizes_fps_and_missing_audio(self):
        result, _, out = self.render(
            {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 30},
            {"clip": "clips/b_noaudio.mp4", "start_frame": 0, "end_frame": 25},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(video_props(out), (640, 360, "30000/1001"))
        second = result["segments"][1]
        self.assertIn(second["output_end_frame"] - second["output_start_frame"], (29, 30, 31))
        self.assertEqual(frame_count(out), result["frame_count"])
        self.assertEqual(audio_codec(out), "aac")
        self.assert_av_aligned(out)

    def test_same_clip_twice(self):
        result, _, out = self.render(
            {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 10},
            {"clip": "clips/a.mp4", "start_frame": 50, "end_frame": 70},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 30)

    def test_single_segment_simple_edit(self):
        result, _, out = self.render({"id": "only", "clip": "clips/a.mp4", "start_frame": 30, "end_frame": 60})
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 30)
        self.assertEqual(
            (result["segments"][0]["output_start_frame"], result["segments"][0]["output_end_frame"]), (0, 30),
        )

    def test_very_short_segments_keep_video_timing(self):
        result, _, out = self.render(
            {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 1},
            {"clip": "clips/a.mp4", "start_frame": 10, "end_frame": 12},
            {"clip": "clips/a.mp4", "start_frame": 20, "end_frame": 23},
            {"clip": "clips/a.mp4", "start_frame": 30, "end_frame": 60},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 36)
        self.assertAlmostEqual(stream_duration(out, "v:0"), 36 * ONE_FRAME_2997, delta=0.002)
        self.assert_av_aligned(out)

    def test_segment_audio_matches_video_length(self):
        captured = {}
        real_run_tool = media.run_tool

        def spy(command):
            if "concat" in command:
                list_path = Path(command[command.index("-i") + 1])
                for segment_file in sorted(list_path.parent.glob("seg_*.mov")):
                    captured[segment_file.name] = (
                        stream_duration(segment_file, "v:0"), stream_duration(segment_file, "a:0"),
                    )
            return real_run_tool(command)

        with mock.patch.object(media, "run_tool", side_effect=spy):
            result, _, out = self.render(
                {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 20},
                {"clip": "clips/b_noaudio.mp4", "start_frame": 0, "end_frame": 25},
                {"clip": "clips/short_audio.mp4", "start_frame": 0, "end_frame": 80},
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(sorted(captured), ["seg_0001.mov", "seg_0002.mov", "seg_0003.mov"])
        for name, (video_seconds, audio_seconds) in captured.items():
            self.assertAlmostEqual(video_seconds, audio_seconds, delta=0.001, msg=name)
        self.assert_av_aligned(out)

    def test_odd_dimension_first_clip(self):
        result, _, out = self.render(
            {"clip": "clips/odd.mkv", "start_frame": 0, "end_frame": 20},
            {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 10},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["settings"]["width"], result["settings"]["height"]), (852, 480))
        self.assertEqual(video_props(out)[:2], (852, 480))
        self.assertEqual(frame_count(out), 30)

    def test_output_directory_is_created(self):
        result, _, out = self.render(
            {"clip": "clips/a.mp4", "end_frame": 10}, output="out/new/nested/dir/reel.mp4",
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(out.is_file())
        self.assertEqual(leftover_work_dirs(out.parent), [])

    def test_refuses_existing_output_unless_overwrite(self):
        out = self.dir / "out" / "existing.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"old")
        segment = {"clip": "clips/a.mp4", "end_frame": 10}
        result, events, _ = self.render(segment, output="out/existing.mp4")
        self.assertEqual(result["errors"][0]["code"], "output_exists")
        self.assertEqual(events, [])
        self.assertEqual(out.read_bytes(), b"old")
        result, _, _ = self.render(segment, output="out/existing.mp4", force=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 10)
        result, _, _ = self.render({"clip": "clips/a.mp4", "end_frame": 12}, output="out/existing.mp4", overwrite=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 12)

    def test_io_error_when_output_parent_is_a_file(self):
        blocker = self.dir / "out" / "blocker"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_bytes(b"")
        result, events, _ = self.render({"clip": "clips/a.mp4", "end_frame": 10}, output="out/blocker/reel.mp4")
        self.assertEqual([(e["code"], e["field"]) for e in result["errors"]], [("io_error", "output")])
        self.assertEqual(exit_code_for(result), 2)
        self.assertEqual(events, [])

    def test_ffmpeg_failure_leaves_no_output(self):
        real_run_tool = media.run_tool

        def failing(command):
            if command[-1].endswith("seg_0002_v.mov"):
                raise media.MediaError("ffmpeg_failed", "模擬失敗", detail="boom")
            return real_run_tool(command)

        with mock.patch.object(media, "run_tool", side_effect=failing):
            result, events, out = self.render(
                {"id": "first", "clip": "clips/a.mp4", "end_frame": 10},
                {"id": "second", "clip": "clips/a.mp4", "start_frame": 10, "end_frame": 20},
            )
        self.assertEqual(result, {"ok": False, "errors": [{
            "code": "ffmpeg_failed", "segment_id": "second", "field": None, "message": "模擬失敗", "detail": "boom",
        }]})
        self.assertEqual(exit_code_for(result), 2)
        self.assertFalse(out.exists())
        self.assertEqual(leftover_work_dirs(out.parent), [])
        self.assertEqual([event["event"] for event in events], ["segment_start", "segment_done", "segment_start"])

    def test_missing_ffmpeg_during_render(self):
        with mock.patch.object(media, "FFMPEG", "ffmpeg-not-installed-xyz"):
            result, _, out = self.render({"clip": "clips/a.mp4", "end_frame": 10})
        self.assertEqual([(e["code"], e["segment_id"]) for e in result["errors"]], [("ffmpeg_not_found", "1")])
        self.assertEqual(exit_code_for(result), 2)
        self.assertFalse(out.exists())
        self.assertEqual(leftover_work_dirs(out.parent), [])


if __name__ == "__main__":
    unittest.main()
