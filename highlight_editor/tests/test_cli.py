import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from highlight_editor import media
from highlight_editor.tests.helpers import (
    FFMPEG_AVAILABLE,
    SKIP_REASON,
    frame_count,
    make_clip,
    manifest_row,
    run_cli,
    write_manifest,
)


def error_codes(result):
    return [(error["code"], error["segment_id"], error["field"]) for error in result["errors"]]


class CliInputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_invalid_json_file(self):
        (self.dir / "p.json").write_text("{not json", encoding="utf-8")
        code, out, _ = run_cli(["validate", "p.json"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(error_codes(out), [("invalid_json", None, None)])
        self.assertIn("第 1 行", out["errors"][0]["message"])

    def test_missing_project_file(self):
        code, out, _ = run_cli(["validate", "missing.json"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(out["errors"][0]["code"], "invalid_json")
        self.assertIn("無法讀取專案檔", out["errors"][0]["message"])

    def test_top_level_must_be_object(self):
        (self.dir / "p.json").write_text("[1]", encoding="utf-8")
        code, out, _ = run_cli(["render", "p.json"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(error_codes(out), [("invalid_json", None, None)])

    def test_validation_errors_are_reported_together(self):
        (self.dir / "p.json").write_text(json.dumps({"output": "x.mov", "segments": []}), encoding="utf-8")
        code, out, _ = run_cli(["validate", "p.json"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(error_codes(out), [("invalid_value", None, "output"), ("invalid_value", None, "segments")])

    def test_missing_manifest(self):
        code, out, _ = run_cli(["list", "--manifest", "nope.csv"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(error_codes(out), [("manifest_not_found", None, "manifest")])


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class CliMediaTests(unittest.TestCase):
    clip_rel = "clips/中文 片段.mp4"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        make_clip(cls.dir / cls.clip_rel, size="320x240", seconds=1)
        write_manifest(cls.dir / "manifest.csv", [manifest_row(
            cls.clip_rel, label="positive", event_type="Home Run",
            description="全壘打（測試）", is_official_highlight="True",
        )])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def write_project(self, name, segments, bom=False, **fields):
        data = {"output": f"out/{name}.mp4", "segments": segments, **fields}
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig" if bom else "utf-8")
        return path.name

    def test_list_outputs_utf8_json(self):
        code, out, _ = run_cli(["list", "--manifest", "manifest.csv"], cwd=self.dir)
        self.assertEqual(code, 0, out)
        clip = out["clips"][0]
        self.assertEqual(clip["clip"], self.clip_rel)
        self.assertEqual(clip["description"], "全壘打（測試）")
        self.assertTrue(clip["exists"])

    def test_base_dir_option(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        code, out, _ = run_cli(["list", "--manifest", "manifest.csv", "--base-dir", str(self.dir)], cwd=other.name)
        self.assertEqual(code, 0, out)
        self.assertTrue(out["clips"][0]["exists"])

    def test_validate_project_saved_with_bom(self):
        name = self.write_project("bom", [{"clip": self.clip_rel, "end_frame": 10}], bom=True)
        code, out, _ = run_cli(["validate", name], cwd=self.dir)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["segments"][0]["end_frame"], 10)

    def test_validate_from_stdin(self):
        text = json.dumps({"output": "out/stdin.mp4", "segments": [{"clip": self.clip_rel}]}, ensure_ascii=False)
        code, out, _ = run_cli(["validate", "-"], cwd=self.dir, stdin_text=text)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["segments"][0]["clip"], self.clip_rel)

    def test_render_reports_progress_on_stderr(self):
        name = self.write_project("render", [{"id": "hr", "clip": self.clip_rel, "start_frame": 5, "end_frame": 20}])
        code, out, events = run_cli(["render", name], cwd=self.dir)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["frame_count"], 15)
        self.assertEqual([event["event"] for event in events], ["segment_start", "segment_done", "concat_start", "done"])
        self.assertEqual(frame_count(self.dir / "out" / "render.mp4"), 15)

    def test_render_overwrite_flag(self):
        name = self.write_project("again", [{"clip": self.clip_rel, "end_frame": 5}])
        self.assertEqual(run_cli(["render", name], cwd=self.dir)[0], 0)
        code, out, events = run_cli(["render", name], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(out["errors"][0]["code"], "output_exists")
        self.assertEqual(events, [])
        code, out, _ = run_cli(["render", name, "--overwrite"], cwd=self.dir)
        self.assertEqual(code, 0, out)

    def test_missing_ffmpeg_exits_with_2(self):
        from highlight_editor.__main__ import main

        project_path = self.dir / "nf.json"
        project_path.write_text(json.dumps({"output": "out/nf.mp4", "segments": [{"clip": self.clip_rel}]}), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(media, "FFPROBE", "ffprobe-not-installed-xyz"), \
                mock.patch("sys.stdin", io.StringIO("")), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["validate", str(project_path), "--base-dir", str(self.dir)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["errors"][0]["code"], "ffmpeg_not_found")


if __name__ == "__main__":
    unittest.main()
