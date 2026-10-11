import csv
import json
import shutil
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from highlight_builder import REPO_ROOT, server

CLIP_DIR = "sport_hightlight/dataset/_test_editor"
OUT_DIR = "out/_test_editor"


def make_clip(path, seconds=2, freq=440):
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class EditorRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clip_dir = REPO_ROOT / CLIP_DIR
        cls.clip_dir.mkdir(parents=True, exist_ok=True)
        cls.clips = [f"{CLIP_DIR}/a.mp4", f"{CLIP_DIR}/b.mp4"]
        make_clip(REPO_ROOT / cls.clips[0], 2, 440)
        make_clip(REPO_ROOT / cls.clips[1], 2, 660)
        cls.manifest = f"{CLIP_DIR}/manifest.csv"
        with (REPO_ROOT / cls.manifest).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["clip_path", "label", "event_type", "inning", "half", "clip_duration_seconds", "batter"])
            w.writeheader()
            for clip, event in zip(cls.clips, ("Home Run", "Strikeout")):
                w.writerow({"clip_path": clip, "label": "positive", "event_type": event, "inning": "2", "half": "bottom",
                            "clip_duration_seconds": "2.000", "batter": "Joey Ortiz"})
        server.Handler.manager = server.JobManager(demo=True, demo_delay=0.05)
        server.Handler.renders = server.RenderManager(server.now)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.clip_dir, ignore_errors=True)
        shutil.rmtree(REPO_ROOT / OUT_DIR, ignore_errors=True)

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read() or b"null")

    def project(self, output, **over):
        p = {"version": 1, "output": output, "overwrite": True, "settings": {"crf": 23, "preset": "ultrafast"},
             "segments": [{"id": "1", "clip": self.clips[0], "start_frame": 15, "end_frame": 45},
                          {"id": "2", "clip": self.clips[1], "start_frame": 0}]}
        p.update(over)
        return p

    def test_list_reads_real_fps_and_frames(self):
        status, body = self.call("POST", "/api/v1/edit/clips", {"manifests": [self.manifest]})
        self.assertEqual(status, 200, body)
        self.assertEqual([c["exists"] for c in body["clips"]], [True, True])
        self.assertEqual(body["clips"][0]["fps"], "30/1")
        self.assertEqual(body["clips"][0]["frame_count"], 60)

    def test_missing_manifest_maps_to_404(self):
        status, body = self.call("POST", "/api/v1/edit/clips", {"manifests": [f"{CLIP_DIR}/nope.csv"]})
        self.assertEqual(status, 404)
        self.assertEqual(body["editor_errors"][0]["code"], "manifest_not_found")

    def test_output_must_stay_under_out(self):
        for output in ("video/eltaMax10_Reds_Brewers_0913_bottom2nd.mp4", "../x.mp4", f"{OUT_DIR}/reel.mov"):
            status, body = self.call("POST", "/api/v1/renders", self.project(output))
            self.assertEqual(status, 400, output)
            self.assertEqual(body["errors"]["output"]["_errors"][0]["code"], 50036)

    def test_validate_reports_out_of_range(self):
        bad = self.project(f"{OUT_DIR}/bad.mp4")
        bad["segments"][0]["end_frame"] = 999
        status, body = self.call("POST", "/api/v1/edit/validate", bad)
        self.assertEqual(status, 400)
        self.assertEqual(body["editor_errors"][0]["code"], "out_of_range")
        self.assertEqual(body["editor_errors"][0]["segment_id"], "1")

    def test_render_makes_a_playable_video(self):
        output = f"{OUT_DIR}/reel.mp4"
        status, render = self.call("POST", "/api/v1/renders", self.project(output))
        self.assertEqual(status, 200, render)
        self.assertTrue(render["id"].startswith("render_"))
        with urllib.request.urlopen(f"{self.base}/api/v1/renders/{render['id']}/events", timeout=120) as resp:
            events = resp.read().decode()
        self.assertTrue(events.rstrip().split("\n\n")[-1].startswith("event: JOB_DONE"))
        _, done = self.call("GET", f"/api/v1/renders/{render['id']}")
        self.assertEqual(done["status"], "succeeded", done)
        self.assertEqual(done["result"]["frame_count"], 30 + 60)
        self.assertEqual([s["id"] for s in done["result"]["segments"]], ["1", "2"])
        with urllib.request.urlopen(f"{self.base}/media/{output}", timeout=10) as resp:
            self.assertEqual(resp.headers["Content-Type"], "video/mp4")
            self.assertGreater(len(resp.read()), 1000)

    def test_unknown_render(self):
        status, body = self.call("GET", "/api/v1/renders/render_nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["code"], 10002)


if __name__ == "__main__":
    unittest.main()
