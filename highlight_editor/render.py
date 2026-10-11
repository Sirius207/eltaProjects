"""驗證專案，並以兩階段 ffmpeg 流程渲染：逐段正規化成 .mov，再用 concat demuxer 串接。"""

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from . import media
from .project import EditorError, Project, Segment, error_result, parse_project, resolve_path, same_path

ProgressCallback = Callable[[Dict[str, Any]], None]


@dataclass
class PreparedSegment:
    segment: Segment
    path: str
    info: media.MediaInfo
    end_frame: int


@dataclass
class Prepared:
    project: Project
    output_path: str
    segments: List[PreparedSegment]
    width: int
    height: int
    fps_text: str
    fps: Fraction


def _even(value: int) -> int:
    return max(2, value - value % 2)


def _seconds(frames: int, fps: Fraction) -> float:
    return round(float(frames / fps), 3)


def _settings_dict(prepared: Prepared) -> Dict[str, Any]:
    settings = prepared.project.settings
    return {
        "width": prepared.width,
        "height": prepared.height,
        "fps": prepared.fps_text,
        "crf": settings.crf,
        "preset": settings.preset,
    }


def _prepare(data: Any, base_dir: str, overwrite: bool) -> Tuple[Optional[Prepared], List[EditorError]]:
    """結構驗證、檔案檢查、探測，並決定輸出規格。有任何錯誤時回傳 (None, errors)。"""
    project, errors = parse_project(data)
    if project is None:
        return None, errors
    if overwrite:
        project.overwrite = True
    output_path = resolve_path(base_dir, project.output) if project.output else ""
    if output_path and os.path.exists(output_path) and not project.overwrite:
        errors.append(EditorError(
            "output_exists", f"輸出檔已存在：{project.output}；如要覆寫請設定 overwrite", field="output",
        ))

    probed: Dict[str, Union[media.MediaInfo, media.MediaError]] = {}
    prepared: List[PreparedSegment] = []
    for segment in project.segments:
        path = resolve_path(base_dir, segment.clip)
        if output_path and same_path(path, output_path):
            errors.append(EditorError(
                "invalid_value", "output 不可與片段指向同一個檔案", segment_id=segment.id, field="output",
            ))
            continue
        if not os.path.isfile(path):
            errors.append(EditorError(
                "clip_not_found", f"找不到片段：{segment.clip}", segment_id=segment.id, field="clip",
            ))
            continue
        key = os.path.normcase(path)
        if key not in probed:
            try:
                probed[key] = media.probe(path)
            except media.MediaError as exc:
                if exc.code == "ffmpeg_not_found":
                    return None, errors + [exc.to_error()]
                probed[key] = exc
        info = probed[key]
        if isinstance(info, media.MediaError):
            errors.append(info.to_error(segment_id=segment.id, field="clip"))
            continue
        end_frame = info.frame_count if segment.end_frame is None else segment.end_frame
        if end_frame > info.frame_count:
            errors.append(EditorError(
                "out_of_range", f"end_frame（{end_frame}）超過片段影格數 {info.frame_count}",
                segment_id=segment.id, field="end_frame",
            ))
            continue
        if segment.start_frame >= end_frame:
            errors.append(EditorError(
                "out_of_range", f"start_frame（{segment.start_frame}）必須小於片段影格數 {info.frame_count}",
                segment_id=segment.id, field="start_frame",
            ))
            continue
        prepared.append(PreparedSegment(segment=segment, path=path, info=info, end_frame=end_frame))

    if errors:
        return None, errors
    first = prepared[0].info
    settings = project.settings
    fps_text = settings.fps if settings.fps is not None else first.fps_text
    return Prepared(
        project=project,
        output_path=output_path,
        segments=prepared,
        width=settings.width if settings.width is not None else _even(first.width),
        height=settings.height if settings.height is not None else _even(first.height),
        fps_text=fps_text,
        fps=Fraction(fps_text),
    ), []


def validate_project(data: Any, base_dir: str = ".") -> Dict[str, Any]:
    """檢查專案並補上實際的 end_frame 與輸出規格；不編碼，也不建立任何檔案或目錄。

    回傳 ValidateResult 或 ErrorResult（見 openapi.yaml）。
    """
    prepared, errors = _prepare(data, base_dir, overwrite=False)
    if prepared is None:
        return error_result(errors)
    estimated = sum(
        (Fraction(item.end_frame - item.segment.start_frame) / item.info.fps for item in prepared.segments),
        Fraction(0),
    )
    return {
        "ok": True,
        "output": prepared.project.output,
        "settings": _settings_dict(prepared),
        "estimated_duration": round(float(estimated), 3),
        "segments": [
            {
                "id": item.segment.id,
                "clip": item.segment.clip,
                "start_frame": item.segment.start_frame,
                "end_frame": item.end_frame,
                "fps": item.info.fps_text,
                "frame_count": item.info.frame_count,
            }
            for item in prepared.segments
        ],
    }


def _render_result(prepared: Prepared, frame_counts: List[int]) -> Dict[str, Any]:
    segments = []
    cursor = 0
    for item, frames in zip(prepared.segments, frame_counts):
        start, end = cursor, cursor + frames
        segments.append({
            "id": item.segment.id,
            "clip": item.segment.clip,
            "start_frame": item.segment.start_frame,
            "end_frame": item.end_frame,
            "output_start_frame": start,
            "output_end_frame": end,
            "output_start": _seconds(start, prepared.fps),
            "output_end": _seconds(end, prepared.fps),
        })
        cursor = end
    return {
        "ok": True,
        "output": prepared.project.output,
        "settings": _settings_dict(prepared),
        "duration": _seconds(cursor, prepared.fps),
        "frame_count": cursor,
        "segments": segments,
    }


def _render(prepared: Prepared, work_dir: str, emit: ProgressCallback) -> Dict[str, Any]:
    settings = prepared.project.settings
    total = len(prepared.segments)
    segment_files: List[str] = []
    frame_counts: List[int] = []
    for index, item in enumerate(prepared.segments, start=1):
        segment = item.segment
        emit({"event": "segment_start", "index": index, "total": total, "segment_id": segment.id})
        video_path = os.path.join(work_dir, f"seg_{index:04d}_v.mov")
        segment_path = os.path.join(work_dir, f"seg_{index:04d}.mov")
        try:
            media.run_tool(media.build_video_command(
                item.path, segment.start_frame, item.end_frame,
                prepared.width, prepared.height, prepared.fps_text,
                settings.crf, settings.preset, video_path,
            ))
            frames = media.probe(video_path).frame_count
            audio_start, audio_end = media.audio_window(
                segment.start_frame, item.end_frame, item.info.fps, item.info.video_start,
            )
            media.run_tool(media.build_audio_mux_command(
                video_path, item.path, item.info.has_audio, audio_start, audio_end,
                float(frames / prepared.fps), segment_path,
            ))
        except media.MediaError as exc:
            return error_result([exc.to_error(segment_id=segment.id)])
        with contextlib.suppress(OSError):
            os.remove(video_path)
        segment_files.append(os.path.basename(segment_path))
        frame_counts.append(frames)
        emit({"event": "segment_done", "index": index, "total": total, "segment_id": segment.id})

    emit({"event": "concat_start"})
    list_path = os.path.join(work_dir, "list.txt")
    temp_output = os.path.join(work_dir, "output.mp4")
    try:
        media.write_concat_list(segment_files, list_path)
        media.run_tool(media.build_concat_command(list_path, temp_output))
        os.replace(temp_output, prepared.output_path)
    except media.MediaError as exc:
        return error_result([exc.to_error()])
    except OSError as exc:
        return error_result([EditorError("io_error", f"無法寫入輸出檔：{exc}", field="output")])
    emit({"event": "done", "output": prepared.project.output})
    return _render_result(prepared, frame_counts)


def render_project(
    data: Any,
    base_dir: str = ".",
    overwrite: bool = False,
    on_progress: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """渲染專案，回傳 RenderResult 或 ErrorResult（見 openapi.yaml）。

    暫存目錄建在輸出檔旁邊，結束後一律刪除；失敗時不會產生或覆蓋輸出檔。
    """
    prepared, errors = _prepare(data, base_dir, overwrite)
    if prepared is None:
        return error_result(errors)
    emit = on_progress or (lambda event: None)
    output_dir = os.path.dirname(prepared.output_path)
    try:
        os.makedirs(output_dir, exist_ok=True)
        work_dir = tempfile.mkdtemp(prefix=".highlight_editor_", dir=output_dir)
    except OSError as exc:
        return error_result([EditorError("io_error", f"無法建立輸出目錄：{exc}", field="output")])
    try:
        return _render(prepared, work_dir, emit)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
