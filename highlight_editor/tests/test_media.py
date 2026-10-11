import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from highlight_editor import media
from highlight_editor.media import (
    MediaError,
    audio_window,
    build_audio_mux_command,
    build_concat_command,
    build_video_command,
    parse_rate,
    probe,
    rate_text,
    run_tool,
    write_concat_list,
)
from highlight_editor.tests.helpers import FFMPEG_AVAILABLE, SKIP_REASON, frame_count, make_clip

QUIET = ["-hide_banner", "-loglevel", "error", "-y"]


class ParseRateTests(unittest.TestCase):
    def test_parse_rate(self):
        self.assertEqual(parse_rate("30000/1001"), Fraction(30000, 1001))
        self.assertEqual(parse_rate("25/1"), Fraction(25))
        for bad in ("0/0", "0", "", None, "abc"):
            with self.subTest(text=bad):
                self.assertIsNone(parse_rate(bad))

    def test_rate_text(self):
        self.assertEqual(rate_text(Fraction(30000, 1001)), "30000/1001")
        self.assertEqual(rate_text(Fraction(25)), "25/1")


class AudioWindowTests(unittest.TestCase):
    def test_frames_to_seconds(self):
        start, end = audio_window(45, 135, Fraction(30000, 1001), 0.0)
        self.assertAlmostEqual(start, 1.5015, places=9)
        self.assertAlmostEqual(end, 4.5045, places=9)

    def test_adds_video_start_time(self):
        self.assertEqual(audio_window(0, 30, Fraction(30), 0.5), (0.5, 1.5))


class CommandBuilderTests(unittest.TestCase):
    def test_video_command(self):
        command = build_video_command("in.mp4", 45, 135, 1280, 720, "30000/1001", 20, "fast", "seg_0001_v.mov")
        self.assertEqual(command, [
            "ffmpeg", *QUIET, "-i", "in.mp4", "-map", "0:v:0", "-an",
            "-vf",
            "trim=start_frame=45:end_frame=135,setpts=PTS-STARTPTS,"
            "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,fps=30000/1001,format=yuv420p",
            "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-bf", "0", "seg_0001_v.mov",
        ])

    def test_audio_mux_command_with_audio(self):
        command = build_audio_mux_command("v.mov", "in.mp4", True, 1.5015, 4.5045, 3.003, "seg_0001.mov")
        self.assertEqual(command, [
            "ffmpeg", *QUIET, "-i", "v.mov", "-i", "in.mp4", "-map", "0:v:0", "-map", "1:a:0",
            "-af", "atrim=start=1.501500:end=4.504500,asetpts=PTS-STARTPTS,aresample=48000,apad",
            "-c:v", "copy", "-ac", "2", "-ar", "48000", "-t", "3.003000", "-c:a", "pcm_s16le", "seg_0001.mov",
        ])

    def test_audio_mux_command_without_audio_uses_silence(self):
        command = build_audio_mux_command("v.mov", "in.mp4", False, 0.0, 2.0, 2.002, "seg_0002.mov")
        self.assertEqual(command, [
            "ffmpeg", *QUIET, "-i", "v.mov", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-ac", "2", "-ar", "48000", "-t", "2.002000", "-c:a", "pcm_s16le", "seg_0002.mov",
        ])

    def test_concat_command(self):
        self.assertEqual(build_concat_command("list.txt", "output.mp4"), [
            "ffmpeg", *QUIET, "-f", "concat", "-safe", "0", "-i", "list.txt",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "output.mp4",
        ])

    def test_builders_read_binary_name_at_call_time(self):
        original = media.FFMPEG
        try:
            media.FFMPEG = "custom-ffmpeg"
            self.assertEqual(build_concat_command("l.txt", "o.mp4")[0], "custom-ffmpeg")
        finally:
            media.FFMPEG = original

    def test_write_concat_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            list_path = Path(tmp) / "list.txt"
            write_concat_list(["seg_0001.mov", "it's.mov"], str(list_path))
            self.assertEqual(list_path.read_bytes(), b"file 'seg_0001.mov'\nfile 'it'\\''s.mov'\n")


class RunToolTests(unittest.TestCase):
    def test_missing_binary(self):
        with self.assertRaises(MediaError) as ctx:
            run_tool(["definitely-not-a-real-binary-xyz", "-version"])
        self.assertEqual(ctx.exception.code, "ffmpeg_not_found")
        self.assertIn("definitely-not-a-real-binary-xyz", ctx.exception.message)

    def test_failure_keeps_last_stderr_lines(self):
        script = "import sys\nfor i in range(30):\n    print(f'line {i}', file=sys.stderr)\nsys.exit(3)"
        with self.assertRaises(MediaError) as ctx:
            run_tool([sys.executable, "-c", script])
        error = ctx.exception
        self.assertEqual(error.code, "ffmpeg_failed")
        self.assertIn("exit code 3", error.message)
        lines = error.detail.splitlines()
        self.assertEqual(len(lines), 20)
        self.assertEqual((lines[0], lines[-1]), ("line 10", "line 29"))

    def test_to_error(self):
        error = MediaError("ffmpeg_failed", "失敗", detail="tail").to_error(segment_id="k")
        self.assertEqual(error.to_dict(), {
            "code": "ffmpeg_failed", "segment_id": "k", "field": None, "message": "失敗", "detail": "tail",
        })


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class ProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_probe_reads_clip_properties(self):
        clip = make_clip(self.dir / "a.mp4", size="640x360", rate="30000/1001", seconds=2)
        info = probe(str(clip))
        self.assertEqual((info.width, info.height), (640, 360))
        self.assertEqual(info.fps, Fraction(30000, 1001))
        self.assertEqual(info.fps_text, "30000/1001")
        self.assertEqual(info.frame_count, frame_count(clip))
        self.assertTrue(info.has_audio)
        self.assertAlmostEqual(info.video_start, 0.0, places=3)

    def test_probe_without_audio(self):
        clip = make_clip(self.dir / "b.mp4", size="320x240", rate="25", seconds=1, audio=False)
        info = probe(str(clip))
        self.assertFalse(info.has_audio)
        self.assertEqual(info.fps_text, "25/1")

    def test_probe_counts_frames_when_container_has_no_nb_frames(self):
        clip = make_clip(self.dir / "c.mkv", size="320x240", seconds=1)
        self.assertEqual(probe(str(clip)).frame_count, frame_count(clip))

    def test_probe_garbage_is_invalid_media(self):
        bad = self.dir / "bad.mp4"
        bad.write_bytes(b"not a video at all")
        with self.assertRaises(MediaError) as ctx:
            probe(str(bad))
        self.assertEqual(ctx.exception.code, "invalid_media")

    def test_probe_missing_ffprobe(self):
        clip = make_clip(self.dir / "d.mp4", size="320x240", seconds=1)
        original = media.FFPROBE
        try:
            media.FFPROBE = "ffprobe-not-installed-xyz"
            with self.assertRaises(MediaError) as ctx:
                probe(str(clip))
        finally:
            media.FFPROBE = original
        self.assertEqual(ctx.exception.code, "ffmpeg_not_found")


if __name__ == "__main__":
    unittest.main()
