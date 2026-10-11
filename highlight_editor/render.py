"""驗證專案，並以兩階段 ffmpeg 流程渲染：逐段正規化成 .mov，再用 concat demuxer 串接。"""

import os
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
