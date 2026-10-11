"""ffprobe 探測，以及 ffmpeg 指令的組裝與執行。

FFMPEG / FFPROBE 在呼叫時才讀取，測試可以用 mock.patch.object 替換。
"""

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .project import EditorError

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
STDERR_TAIL_LINES = 20
AUDIO_SAMPLE_RATE = 48000
QUIET = ("-hide_banner", "-loglevel", "error", "-y")


class MediaError(Exception):
    def __init__(self, code: str, message: str, detail: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_error(self, segment_id: Optional[str] = None, field: Optional[str] = None) -> EditorError:
        return EditorError(self.code, self.message, segment_id=segment_id, field=field, detail=self.detail)


@dataclass
class MediaInfo:
    fps: Fraction
    fps_text: str
    frame_count: int
    width: int
    height: int
    has_audio: bool
    video_start: float


def run_tool(command: Sequence[str]) -> subprocess.CompletedProcess:
    """執行 ffmpeg/ffprobe；找不到執行檔或執行失敗時轉成 MediaError。"""
    try:
        return subprocess.run(
            list(command), check=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise MediaError(
            "ffmpeg_not_found", f"找不到 {command[0]}；請先安裝 ffmpeg 並確認它在 PATH 中",
        ) from exc
    except subprocess.CalledProcessError as exc:
        lines = (exc.stderr or "").strip().splitlines()
        raise MediaError(
            "ffmpeg_failed", f"{Path(command[0]).name} 執行失敗（exit code {exc.returncode}）",
            detail="\n".join(lines[-STDERR_TAIL_LINES:]),
        ) from exc


def parse_rate(text: Optional[str]) -> Optional[Fraction]:
    """解析 ffprobe 的 "30000/1001"；0/0、0 或無法解析時回傳 None。"""
    if not text:
        return None
    try:
        rate = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def rate_text(rate: Fraction) -> str:
    return f"{rate.numerator}/{rate.denominator}"


def count_frames(path: str) -> int:
    """容器沒有記錄 nb_frames 時，解碼整段計算影格數。"""
    try:
        result = run_tool([
            FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path,
        ])
    except MediaError as exc:
        if exc.code == "ffmpeg_not_found":
            raise
        raise MediaError("invalid_media", f"無法計算影格數：{path}", detail=exc.detail) from exc
    text = result.stdout.strip().split(",")[0]
    return int(text) if text.isdigit() else 0


def probe(path: str) -> MediaInfo:
    """讀取影片的 fps、影格數、尺寸、有無音軌與視訊起始時間。"""
    try:
        result = run_tool([
            FFPROBE, "-v", "error",
            "-show_entries", "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,nb_frames,start_time",
            "-of", "json", path,
        ])
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except MediaError as exc:
        if exc.code == "ffmpeg_not_found":
            raise
        raise MediaError("invalid_media", f"無法讀取影片：{path}", detail=exc.detail) from exc
    except json.JSONDecodeError as exc:
        raise MediaError("invalid_media", f"無法解析 ffprobe 的輸出：{path}") from exc

    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("invalid_media", f"影片沒有視訊串流：{path}")
    fps = parse_rate(video.get("avg_frame_rate")) or parse_rate(video.get("r_frame_rate"))
    if fps is None:
        raise MediaError("invalid_media", f"無法取得影片 fps：{path}")
    nb_frames = str(video.get("nb_frames", ""))
    frame_count = int(nb_frames) if nb_frames.isdigit() and int(nb_frames) > 0 else count_frames(path)
    if frame_count <= 0:
        raise MediaError("invalid_media", f"影片沒有任何影格：{path}")
    try:
        video_start = float(video.get("start_time", 0.0))
    except (TypeError, ValueError):
        video_start = 0.0
    return MediaInfo(
        fps=fps,
        fps_text=rate_text(fps),
        frame_count=frame_count,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        video_start=video_start,
    )


def build_video_command(
    clip: str,
    start_frame: int,
    end_frame: int,
    width: int,
    height: int,
    fps: str,
    crf: int,
    preset: str,
    output: str,
) -> List[str]:
    """第一次呼叫：只編碼視訊。trim 依解碼的影格計數，所以張數精準。

    -bf 0：中間檔不用 B-frame。有 B-frame 時，只有幾格的片段在 concat 串接（視訊直接複製）後
    時間戳會互相重疊，最終影片的影像軌會比音訊短。
    """
    video_filter = ",".join([
        f"trim=start_frame={start_frame}:end_frame={end_frame}",
        "setpts=PTS-STARTPTS",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        f"fps={fps}",
        "format=yuv420p",
    ])
    return [
        FFMPEG, *QUIET, "-i", clip, "-map", "0:v:0", "-an", "-vf", video_filter,
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-bf", "0", output,
    ]


def audio_window(start_frame: int, end_frame: int, fps: Fraction, video_start: float) -> Tuple[float, float]:
    """第 S 格的時間戳是視訊起點 + S / fps；atrim 依時間戳切。"""
    return (
        video_start + float(start_frame / fps),
        video_start + float(end_frame / fps),
    )


def build_audio_mux_command(
    video_path: str,
    clip: str,
    has_audio: bool,
    audio_start: float,
    audio_end: float,
    duration: float,
    output: str,
) -> List[str]:
    """第二次呼叫：視訊直接複製，音訊補長後以 -t 截成剛好 duration 秒（PCM，取樣點精準）。"""
    command = [FFMPEG, *QUIET, "-i", video_path]
    if has_audio:
        command += [
            "-i", clip, "-map", "0:v:0", "-map", "1:a:0",
            "-af",
            f"atrim=start={audio_start:.6f}:end={audio_end:.6f},asetpts=PTS-STARTPTS,"
            f"aresample={AUDIO_SAMPLE_RATE},apad",
        ]
    else:
        command += [
            "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo",
            "-map", "0:v:0", "-map", "1:a:0",
        ]
    command += [
        "-c:v", "copy", "-ac", "2", "-ar", str(AUDIO_SAMPLE_RATE),
        "-t", f"{duration:.6f}", "-c:a", "pcm_s16le", output,
    ]
    return command


def write_concat_list(file_names: Sequence[str], list_path: str) -> None:
    """寫 concat demuxer 清單；相對路徑以清單檔所在目錄為準。"""
    with open(list_path, "w", encoding="utf-8", newline="\n") as handle:
        for name in file_names:
            escaped = name.replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")


def build_concat_command(list_path: str, output: str) -> List[str]:
    """串接：視訊直接複製，音訊只在這一步編碼一次 AAC。"""
    return [
        FFMPEG, *QUIET, "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output,
    ]
