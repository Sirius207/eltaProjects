import os
import unittest

from highlight_editor.project import (
    DEFAULT_CRF,
    DEFAULT_PRESET,
    EditorError,
    error_result,
    exit_code_for,
    parse_project,
    resolve_path,
    same_path,
)


def project(**overrides):
    data = {"output": "out/reel.mp4", "segments": [{"clip": "a.mp4", "start_frame": 0, "end_frame": 30}]}
    data.update(overrides)
    return data


def codes(errors):
    return [(error.code, error.segment_id, error.field) for error in errors]


class ParseProjectTests(unittest.TestCase):
    def test_minimal_project_uses_defaults(self):
        proj, errors = parse_project({"output": "o.mp4", "segments": [{"clip": "a.mp4"}]})
        self.assertEqual(errors, [])
        self.assertEqual(proj.version, 1)
        self.assertFalse(proj.overwrite)
        self.assertEqual((proj.settings.crf, proj.settings.preset), (DEFAULT_CRF, DEFAULT_PRESET))
        self.assertIsNone(proj.settings.width)
        self.assertIsNone(proj.settings.height)
        self.assertIsNone(proj.settings.fps)
        segment = proj.segments[0]
        self.assertEqual((segment.id, segment.clip, segment.start_frame, segment.end_frame), ("1", "a.mp4", 0, None))

    def test_top_level_must_be_object(self):
        proj, errors = parse_project([1, 2])
        self.assertIsNone(proj)
        self.assertEqual(codes(errors), [("invalid_json", None, None)])

    def test_missing_output_and_segments(self):
        proj, errors = parse_project({})
        self.assertEqual(codes(errors), [("missing_field", None, "output"), ("missing_field", None, "segments")])
        self.assertEqual(proj.output, "")

    def test_output_must_be_mp4(self):
        _, errors = parse_project(project(output="out/reel.mov"))
        self.assertEqual(codes(errors), [("invalid_value", None, "output")])
        _, errors = parse_project(project(output="out/REEL.MP4"))
        self.assertEqual(errors, [])
        _, errors = parse_project(project(output="  "))
        self.assertEqual(codes(errors), [("invalid_value", None, "output")])

    def test_version(self):
        self.assertEqual(parse_project(project(version=1.0))[1], [])
        self.assertEqual(parse_project(project(version=None))[1], [])
        for bad in (2, "1", True):
            with self.subTest(version=bad):
                self.assertEqual(codes(parse_project(project(version=bad))[1]), [("invalid_value", None, "version")])

    def test_overwrite_must_be_bool(self):
        self.assertTrue(parse_project(project(overwrite=True))[0].overwrite)
        self.assertEqual(codes(parse_project(project(overwrite="yes"))[1]), [("invalid_value", None, "overwrite")])

    def test_segments_must_be_non_empty_list(self):
        self.assertEqual(codes(parse_project(project(segments=[]))[1]), [("invalid_value", None, "segments")])
        self.assertEqual(codes(parse_project(project(segments={"clip": "a.mp4"}))[1]), [("invalid_value", None, "segments")])

    def test_frame_rules(self):
        cases = [
            ({"start_frame": -1}, ("invalid_value", "1", "start_frame")),
            ({"start_frame": 1.5}, ("invalid_value", "1", "start_frame")),
            ({"start_frame": True}, ("invalid_value", "1", "start_frame")),
            ({"start_frame": "3"}, ("invalid_value", "1", "start_frame")),
            ({"end_frame": 0}, ("invalid_value", "1", "end_frame")),
            ({"start_frame": 10, "end_frame": 10}, ("out_of_range", "1", "end_frame")),
            ({"start_frame": 20, "end_frame": 10}, ("out_of_range", "1", "end_frame")),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                proj, errors = parse_project(project(segments=[{"clip": "a.mp4", **fields}]))
                self.assertEqual(codes(errors), [expected])
                self.assertEqual(proj.segments, [])

    def test_integral_float_frames_are_accepted(self):
        proj, errors = parse_project(project(segments=[{"clip": "a.mp4", "start_frame": 120.0, "end_frame": 480.0}]))
        self.assertEqual(errors, [])
        segment = proj.segments[0]
        self.assertEqual((segment.start_frame, segment.end_frame), (120, 480))
        self.assertIsInstance(segment.start_frame, int)
        self.assertIsInstance(segment.end_frame, int)

    def test_null_frames_use_defaults(self):
        proj, errors = parse_project(project(segments=[{"clip": "a.mp4", "start_frame": None, "end_frame": None}]))
        self.assertEqual(errors, [])
        self.assertEqual((proj.segments[0].start_frame, proj.segments[0].end_frame), (0, None))

    def test_segment_must_be_object_and_have_clip(self):
        proj, errors = parse_project(project(segments=["a.mp4", {"start_frame": 0}, {"clip": ""}]))
        self.assertEqual(codes(errors), [
            ("invalid_value", "1", None),
            ("missing_field", "2", "clip"),
            ("invalid_value", "3", "clip"),
        ])
        self.assertEqual(proj.segments, [])

    def test_ids_default_and_must_be_unique(self):
        proj, errors = parse_project(project(segments=[
            {"clip": "a.mp4"},
            {"clip": "b.mp4", "id": "1"},
            {"clip": "c.mp4", "id": "hr"},
        ]))
        self.assertEqual(codes(errors), [("invalid_value", "1", "id")])
        self.assertEqual([segment.id for segment in proj.segments], ["1", "hr"])

    def test_id_must_be_non_empty_string(self):
        _, errors = parse_project(project(segments=[{"clip": "a.mp4", "id": 7}]))
        self.assertEqual(codes(errors), [("invalid_value", "1", "id")])

    def test_same_clip_can_repeat(self):
        proj, errors = parse_project(project(segments=[
            {"clip": "a.mp4", "end_frame": 10},
            {"clip": "a.mp4", "start_frame": 50, "end_frame": 70},
        ]))
        self.assertEqual(errors, [])
        self.assertEqual([segment.id for segment in proj.segments], ["1", "2"])

    def test_unknown_fields_are_ignored(self):
        proj, errors = parse_project({
            "output": "o.mp4", "ui_note": "keep",
            "segments": [{"clip": "a.mp4", "thumbnail": "x.png"}],
        })
        self.assertEqual(errors, [])
        self.assertEqual(len(proj.segments), 1)

    def test_all_errors_are_collected(self):
        _, errors = parse_project({
            "output": "x.mov",
            "settings": {"width": 1281, "preset": "placebo"},
            "segments": [
                {"clip": "a.mp4", "start_frame": -5},
                {"id": "k", "clip": "b.mp4", "start_frame": 9, "end_frame": 3},
            ],
        })
        self.assertEqual(codes(errors), [
            ("invalid_value", None, "output"),
            ("invalid_value", None, "settings.width"),
            ("invalid_value", None, "settings.preset"),
            ("invalid_value", "1", "start_frame"),
            ("out_of_range", "k", "end_frame"),
        ])


class SettingsTests(unittest.TestCase):
    def settings_errors(self, **settings):
        return codes(parse_project(project(settings=settings))[1])

    def test_settings_must_be_object(self):
        self.assertEqual(codes(parse_project(project(settings="fast"))[1]), [("invalid_value", None, "settings")])

    def test_width_and_height_must_be_positive_even(self):
        proj, errors = parse_project(project(settings={"width": 1280, "height": 720.0}))
        self.assertEqual(errors, [])
        self.assertEqual((proj.settings.width, proj.settings.height), (1280, 720))
        for bad in (1281, 0, -2, "1280", 1.5, True):
            with self.subTest(width=bad):
                self.assertEqual(self.settings_errors(width=bad), [("invalid_value", None, "settings.width")])
        self.assertEqual(self.settings_errors(height=481), [("invalid_value", None, "settings.height")])

    def test_fps_forms(self):
        for value, expected in ((30, "30"), (29.97, "29.97"), ("30000/1001", "30000/1001"), (" 25 ", "25")):
            with self.subTest(fps=value):
                proj, errors = parse_project(project(settings={"fps": value}))
                self.assertEqual(errors, [])
                self.assertEqual(proj.settings.fps, expected)
        for bad in (0, -1, "0", "abc", "1/0", True, [30]):
            with self.subTest(fps=bad):
                self.assertEqual(self.settings_errors(fps=bad), [("invalid_value", None, "settings.fps")])

    def test_crf_range(self):
        for good in (0, 51, 20.0):
            with self.subTest(crf=good):
                self.assertEqual(self.settings_errors(crf=good), [])
        for bad in (-1, 52, 20.5, "20"):
            with self.subTest(crf=bad):
                self.assertEqual(self.settings_errors(crf=bad), [("invalid_value", None, "settings.crf")])

    def test_preset_names(self):
        self.assertEqual(parse_project(project(settings={"preset": "veryslow"}))[0].settings.preset, "veryslow")
        for bad in ("placebo", "FAST", 3):
            with self.subTest(preset=bad):
                self.assertEqual(self.settings_errors(preset=bad), [("invalid_value", None, "settings.preset")])


class ResultHelperTests(unittest.TestCase):
    def test_error_dict_includes_detail_only_when_present(self):
        self.assertEqual(
            EditorError("out_of_range", "m", segment_id="k", field="end_frame").to_dict(),
            {"code": "out_of_range", "segment_id": "k", "field": "end_frame", "message": "m"},
        )
        self.assertEqual(EditorError("ffmpeg_failed", "m", detail="tail").to_dict()["detail"], "tail")

    def test_exit_codes(self):
        self.assertEqual(exit_code_for({"ok": True}), 0)
        self.assertEqual(exit_code_for(error_result([EditorError("out_of_range", "m")])), 1)
        self.assertEqual(
            exit_code_for(error_result([EditorError("out_of_range", "m"), EditorError("ffmpeg_failed", "m")])), 2,
        )
        for code in ("ffmpeg_not_found", "io_error"):
            self.assertEqual(exit_code_for(error_result([EditorError(code, "m")])), 2)

    def test_paths(self):
        base = os.path.abspath("base")
        self.assertEqual(resolve_path(base, "clips/a.mp4"), os.path.join(base, "clips", "a.mp4"))
        absolute = os.path.abspath("elsewhere.mp4")
        self.assertEqual(resolve_path(base, absolute), absolute)
        self.assertTrue(same_path(os.path.join(base, "x", "..", "a.mp4"), os.path.join(base, "a.mp4")))
        self.assertFalse(same_path(os.path.join(base, "a.mp4"), os.path.join(base, "b.mp4")))


if __name__ == "__main__":
    unittest.main()
