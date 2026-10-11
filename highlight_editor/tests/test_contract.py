import tempfile
import unittest
from pathlib import Path
from unittest import mock

from highlight_editor import list_clips, media, render_project, validate_project
from highlight_editor.tests import schema_check
from highlight_editor.tests.helpers import FFMPEG_AVAILABLE, SKIP_REASON, make_clip, manifest_row, write_manifest

NO_YAML = "需要 PyYAML 才能讀取 openapi.yaml"


@unittest.skipIf(schema_check.yaml is None, NO_YAML)
class OpenApiDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = schema_check.load_document()
        cls.checker = schema_check.SchemaChecker(cls.document)

    def test_document_shape(self):
        self.assertEqual(self.document["openapi"], "3.0.3")
        self.assertEqual(self.document["paths"], {})
        for key in ("title", "version", "description"):
            self.assertTrue(self.document["info"].get(key), key)

    def test_refs_resolve(self):
        self.assertEqual(self.checker.unresolved_refs(), [])

    def test_required_fields_are_declared(self):
        self.assertEqual(self.checker.undeclared_required(), [])

    def test_examples_match_their_schema(self):
        examples = {name: schema["example"] for name, schema in self.checker.schemas.items() if "example" in schema}
        self.assertGreaterEqual(len(examples), 9)
        for name, example in examples.items():
            with self.subTest(schema=name):
                self.assertEqual(self.checker.check(example, name), [])

    def test_checker_rejects_bad_values(self):
        project = self.checker.schemas["Project"]["example"]
        cases = [
            ("Project", {**project, "segments": [{"clip": "a.mp4", "start_frame": -1}]}),
            ("Project", {**project, "segments": [{"clip": "a.mp4", "start_frame": 1.5}]}),
            ("Project", {**project, "settings": {"width": 1281}}),
            ("Project", {**project, "settings": {"fps": 0}}),
            ("Project", {**project, "output": "out/x.mov"}),
            ("Project", {**project, "segments": []}),
            ("ListClipsResponse", {"ok": False, "clips": []}),
            ("ErrorResult", {"ok": False, "errors": [
                {"code": "boom", "segment_id": None, "field": None, "message": "x"}]}),
            ("ProgressEvent", {"event": "paused"}),
        ]
        for name, value in cases:
            with self.subTest(schema=name, value=value):
                self.assertNotEqual(self.checker.check(value, name), [])


@unittest.skipIf(schema_check.yaml is None, NO_YAML)
@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class RealOutputContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = schema_check.SchemaChecker(schema_check.load_document())
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        make_clip(cls.dir / "clips" / "a.mp4", size="320x240", seconds=1)
        make_clip(cls.dir / "clips" / "b.mp4", size="320x240", rate="25", seconds=1, audio=False)
        write_manifest(cls.dir / "manifest.csv", [
            manifest_row("clips/a.mp4", label="positive", is_official_highlight="True"),
            manifest_row("clips/missing.mp4"),
        ])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def assert_conforms(self, value, schema_name):
        self.assertEqual(self.checker.check(value, schema_name), [], value)

    def test_list_outputs(self):
        self.assert_conforms(list_clips(["manifest.csv"], base_dir=str(self.dir)), "ListClipsResponse")
        self.assert_conforms(list_clips(["nope.csv"], base_dir=str(self.dir)), "ListClipsResponse")

    def test_validate_outputs(self):
        ok = validate_project({"output": "out/v.mp4", "segments": [{"clip": "clips/a.mp4"}]}, base_dir=str(self.dir))
        self.assertTrue(ok["ok"], ok)
        self.assert_conforms(ok, "ValidateResponse")
        failed = validate_project({"output": "out/v.mp4", "segments": [{"clip": "clips/nope.mp4"}]}, base_dir=str(self.dir))
        self.assert_conforms(failed, "ValidateResponse")

    def test_render_outputs_and_progress_events(self):
        events = []
        result = render_project(
            {"output": "out/r.mp4", "segments": [
                {"id": "a", "clip": "clips/a.mp4", "end_frame": 10},
                {"id": "b", "clip": "clips/b.mp4", "end_frame": 10},
            ]},
            base_dir=str(self.dir), on_progress=events.append,
        )
        self.assertTrue(result["ok"], result)
        self.assert_conforms(result, "RenderResponse")
        self.assertEqual(len(events), 6)
        for event in events:
            self.assert_conforms(event, "ProgressEvent")

    def test_failure_outputs(self):
        real_run_tool = media.run_tool

        def failing(command):
            if command[-1].endswith("_v.mov"):
                raise media.MediaError("ffmpeg_failed", "模擬失敗", detail="boom")
            return real_run_tool(command)

        with mock.patch.object(media, "run_tool", side_effect=failing):
            result = render_project({"output": "out/f.mp4", "segments": [{"clip": "clips/a.mp4"}]}, base_dir=str(self.dir))
        self.assertEqual(result["errors"][0]["code"], "ffmpeg_failed")
        self.assert_conforms(result, "RenderResponse")
        with mock.patch.object(media, "FFPROBE", "ffprobe-not-installed-xyz"):
            result = validate_project({"output": "out/f.mp4", "segments": [{"clip": "clips/a.mp4"}]}, base_dir=str(self.dir))
        self.assertEqual(result["errors"][0]["code"], "ffmpeg_not_found")
        self.assert_conforms(result, "ValidateResponse")


if __name__ == "__main__":
    unittest.main()
