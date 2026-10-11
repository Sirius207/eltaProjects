"""測試用工具：以 ffmpeg lavfi 產生合成片段，並讀取影片屬性。"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
SKIP_REASON = "需要 ffmpeg 與 ffprobe 在 PATH 上"
QUIET = ["-hide_banner", "-loglevel", "error", "-y"]
MANIFEST_HEADER = [
    "clip_path", "label", "game_pk", "source_video", "inning", "half", "event_type", "description",
    "event_seconds", "event_time_source", "clip_start_seconds", "clip_end_seconds", "clip_duration_seconds",
    "is_official_highlight", "official_highlight_title", "official_highlight_url",
]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command), check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def make_clip(path, size="1280x720", rate="30000/1001", seconds=4.0, audio=True, audio_seconds=None) -> Path:
    """產生 H.264 測試片段（testsrc2 畫面＋440 Hz 音軌）；audio_seconds 可讓音軌比畫面短。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", *QUIET, "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={seconds}"]
    if audio:
        length = seconds if audio_seconds is None else audio_seconds
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={length}"]
        command += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"]
    else:
        command += ["-map", "0:v:0"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    _run(command)
    return path


def make_odd_clip(path, width=853, height=481, seconds=1.0) -> Path:
    """產生寬高為奇數的無音軌片段。

    testsrc2 只會產生偶數尺寸，libx264 也會把奇數尺寸修成偶數，所以先產生 (width-1)x(height-1)，
    再放大成奇數尺寸，以 ffv1/yuv444p 存檔。請用 .mkv 路徑。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", *QUIET, "-f", "lavfi",
        "-i", f"testsrc2=size={width - 1}x{height - 1}:rate=30000/1001:duration={seconds}",
        "-vf", f"scale={width}:{height},format=yuv444p", "-c:v", "ffv1", str(path),
    ])
    return path


def frame_count(path) -> int:
    """解碼計算影格數（測試用的權威值）。"""
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path),
    ])
    return int(result.stdout.strip().split(",")[0])


def stream_duration(path, selector: str) -> float:
    """selector 例如 "v:0"、"a:0"。"""
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", selector,
        "-show_entries", "stream=duration", "-of", "csv=p=0", str(path),
    ])
    return float(result.stdout.strip().split(",")[0])


def video_props(path) -> Tuple[int, int, str]:
    """回傳 (width, height, r_frame_rate)。"""
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", str(path),
    ])
    stream = json.loads(result.stdout)["streams"][0]
    return stream["width"], stream["height"], stream["r_frame_rate"]


def audio_codec(path) -> Optional[str]:
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path),
    ])
    return result.stdout.strip() or None


def frame_png(path, index: int, out_png) -> Path:
    """把第 index 格（0 起算）存成 PNG。"""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", *QUIET, "-i", str(path), "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", str(out_png)])
    return out_png


def psnr(png_a, png_b) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(png_a), "-i", str(png_b), "-lavfi", "psnr", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    match = re.search(r"average:(\S+)", result.stderr)
    if match is None:
        raise AssertionError(f"找不到 PSNR：{result.stderr[-500:]}")
    value = match.group(1)
    return float("inf") if value == "inf" else float(value)


def leftover_work_dirs(directory) -> List[str]:
    directory = Path(directory)
    if not directory.exists():
        return []
    return sorted(path.name for path in directory.glob(".highlight_editor_*"))


def manifest_row(clip_path: str, **fields: str) -> Dict[str, str]:
    row = {name: "" for name in MANIFEST_HEADER}
    row.update(
        clip_path=clip_path, label="hard_negative", game_pk="823734", inning="2", half="bottom",
        event_type="Strikeout", clip_duration_seconds="20.000", is_official_highlight="False",
    )
    row.update(fields)
    return row


def write_manifest(path, rows, header: Sequence[str] = MANIFEST_HEADER, bom: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def run_cli(args: Sequence[str], cwd, stdin_text: Optional[str] = None) -> Tuple[int, Any, List[Any]]:
    """以子程序執行 python -m highlight_editor，回傳 (exit code, stdout JSON, stderr 每行 JSON)。

    刻意移除 PYTHONIOENCODING 與 PYTHONUTF8，確認 CLI 自己把 stdio 設成 UTF-8。
    """
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "highlight_editor", *args],
        cwd=str(cwd), env=env, capture_output=True,
        input=stdin_text.encode("utf-8") if stdin_text is not None else None,
    )
    stdout = completed.stdout.decode("utf-8")
    stderr = completed.stderr.decode("utf-8")
    try:
        result = json.loads(stdout)
        events = [json.loads(line) for line in stderr.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise AssertionError(f"CLI 輸出不是 JSON：{exc}\nstdout={stdout!r}\nstderr={stderr!r}") from exc
    return completed.returncode, result, events
