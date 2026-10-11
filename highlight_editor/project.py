"""專案 JSON 的資料模型與結構驗證。純函式，不呼叫 ffmpeg，也不讀寫檔案系統。"""

import os
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

PRESETS: Tuple[str, ...] = (
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
)
DEFAULT_CRF = 20
DEFAULT_PRESET = "fast"
SYSTEM_ERROR_CODES = frozenset({"ffmpeg_not_found", "ffmpeg_failed", "io_error"})


@dataclass
class EditorError:
    code: str
    message: str
    segment_id: Optional[str] = None
    field: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "code": self.code,
            "segment_id": self.segment_id,
            "field": self.field,
            "message": self.message,
        }
        if self.detail is not None:
            data["detail"] = self.detail
        return data


def error_result(errors: List[EditorError]) -> Dict[str, Any]:
    return {"ok": False, "errors": [error.to_dict() for error in errors]}


def exit_code_for(result: Dict[str, Any]) -> int:
    """0：成功；1：輸入或驗證錯誤；2：ffmpeg 或檔案系統錯誤。"""
    if result.get("ok"):
        return 0
    codes = {error.get("code") for error in result.get("errors", [])}
    return 2 if codes & SYSTEM_ERROR_CODES else 1


@dataclass
class OutputSettings:
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[str] = None
    crf: int = DEFAULT_CRF
    preset: str = DEFAULT_PRESET


@dataclass
class Segment:
    id: str
    clip: str
    start_frame: int = 0
    end_frame: Optional[int] = None


@dataclass
class Project:
    output: str
    segments: List[Segment]
    settings: OutputSettings = field(default_factory=OutputSettings)
    overwrite: bool = False
    version: int = 1


def resolve_path(base_dir: str, path: str) -> str:
    """相對路徑以 base_dir 為基準，回傳絕對路徑（不檢查檔案是否存在）。"""
    return os.path.abspath(os.path.join(base_dir, path))


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _as_int(value: Any) -> Optional[int]:
    """整數，或整數值的浮點數（120.0）轉成 int；其他型別（含布林）回傳 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def parse_fps(value: Any) -> Optional[str]:
    """把 fps 正規化成字串（30 → "30"、" 25 " → "25"）；不合法時回傳 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    try:
        rate = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return text if rate > 0 else None


def _parse_settings(raw: Any, errors: List[EditorError]) -> OutputSettings:
    settings = OutputSettings()
    if raw is None:
        return settings
    if not isinstance(raw, dict):
        errors.append(EditorError("invalid_value", "settings 必須是物件", field="settings"))
        return settings
    for name in ("width", "height"):
        value = raw.get(name)
        if value is None:
            continue
        number = _as_int(value)
        if number is None or number < 2 or number % 2:
            errors.append(EditorError(
                "invalid_value", f"settings.{name} 必須是大於 0 的偶數：{value!r}", field=f"settings.{name}",
            ))
        else:
            setattr(settings, name, number)
    fps = raw.get("fps")
    if fps is not None:
        text = parse_fps(fps)
        if text is None:
            errors.append(EditorError(
                "invalid_value", f"settings.fps 必須是大於 0 的數字或分數字串：{fps!r}", field="settings.fps",
            ))
        else:
            settings.fps = text
    crf = raw.get("crf")
    if crf is not None:
        number = _as_int(crf)
        if number is None or not 0 <= number <= 51:
            errors.append(EditorError(
                "invalid_value", f"settings.crf 必須是 0 到 51 的整數：{crf!r}", field="settings.crf",
            ))
        else:
            settings.crf = number
    preset = raw.get("preset")
    if preset is not None:
        if preset not in PRESETS:
            errors.append(EditorError(
                "invalid_value", f"settings.preset 不支援：{preset!r}；可用值：{'、'.join(PRESETS)}",
                field="settings.preset",
            ))
        else:
            settings.preset = preset
    return settings


_INVALID = object()


def _frame_value(
    item: Dict[str, Any],
    name: str,
    default: Optional[int],
    minimum: int,
    segment_id: str,
    errors: List[EditorError],
) -> Any:
    """回傳 int、default（欄位缺少或為 null 時），或 _INVALID（並記錄錯誤）。"""
    value = item.get(name)
    if value is None:
        return default
    number = _as_int(value)
    if number is None or number < minimum:
        errors.append(EditorError(
            "invalid_value", f"{name} 必須是 ≥ {minimum} 的整數：{value!r}",
            segment_id=segment_id, field=name,
        ))
        return _INVALID
    return number


def _parse_segments(raw: Any, errors: List[EditorError]) -> List[Segment]:
    if raw is None:
        errors.append(EditorError("missing_field", "缺少 segments", field="segments"))
        return []
    if not isinstance(raw, list):
        errors.append(EditorError("invalid_value", "segments 必須是陣列", field="segments"))
        return []
    if not raw:
        errors.append(EditorError("invalid_value", "segments 至少要有 1 段", field="segments"))
        return []

    candidates: List[Tuple[Segment, bool]] = []
    for index, item in enumerate(raw, start=1):
        default_id = str(index)
        if not isinstance(item, dict):
            errors.append(EditorError("invalid_value", f"第 {index} 段必須是物件", segment_id=default_id))
            continue
        segment_errors: List[EditorError] = []
        segment_id = item.get("id")
        if segment_id is None:
            segment_id = default_id
        elif not isinstance(segment_id, str) or not segment_id:
            segment_errors.append(EditorError(
                "invalid_value", f"第 {index} 段的 id 必須是非空字串", segment_id=default_id, field="id",
            ))
            segment_id = default_id
        clip = item.get("clip")
        if clip is None:
            segment_errors.append(EditorError("missing_field", "缺少 clip", segment_id=segment_id, field="clip"))
        elif not isinstance(clip, str) or not clip.strip():
            segment_errors.append(EditorError(
                "invalid_value", "clip 必須是非空字串", segment_id=segment_id, field="clip",
            ))
        start = _frame_value(item, "start_frame", 0, 0, segment_id, segment_errors)
        end = _frame_value(item, "end_frame", None, 1, segment_id, segment_errors)
        if isinstance(start, int) and isinstance(end, int) and end <= start:
            segment_errors.append(EditorError(
                "out_of_range", f"end_frame（{end}）必須大於 start_frame（{start}）",
                segment_id=segment_id, field="end_frame",
            ))
        errors.extend(segment_errors)
        candidates.append((
            Segment(
                id=segment_id,
                clip=clip if isinstance(clip, str) else "",
                start_frame=start if isinstance(start, int) else 0,
                end_frame=end if isinstance(end, int) else None,
            ),
            not segment_errors,
        ))

    seen = set()
    segments: List[Segment] = []
    for segment, valid in candidates:
        if segment.id in seen:
            errors.append(EditorError(
                "invalid_value", f"id「{segment.id}」重複", segment_id=segment.id, field="id",
            ))
            continue
        seen.add(segment.id)
        if valid:
            segments.append(segment)
    return segments


def parse_project(data: Any) -> Tuple[Optional[Project], List[EditorError]]:
    """解析並驗證專案 dict。

    回傳 (project, errors)。頂層不是物件時 project 為 None；否則 project 只包含結構正確的
    段落，讓呼叫端可以繼續探測這些段落，一次回報所有錯誤。output 不合法時為 ""。
    """
    if not isinstance(data, dict):
        return None, [EditorError("invalid_json", "專案 JSON 的頂層必須是物件")]
    errors: List[EditorError] = []

    version = data.get("version")
    if version is not None and _as_int(version) != 1:
        errors.append(EditorError(
            "invalid_value", f"不支援的 version：{version!r}；目前只支援 1", field="version",
        ))

    output = data.get("output")
    if output is None:
        errors.append(EditorError("missing_field", "缺少 output", field="output"))
        output = ""
    elif not isinstance(output, str) or not output.strip():
        errors.append(EditorError("invalid_value", "output 必須是非空字串", field="output"))
        output = ""
    elif not output.lower().endswith(".mp4"):
        errors.append(EditorError("invalid_value", f"output 的副檔名必須是 .mp4：{output}", field="output"))
        output = ""

    overwrite = data.get("overwrite")
    if overwrite is None:
        overwrite = False
    elif not isinstance(overwrite, bool):
        errors.append(EditorError("invalid_value", "overwrite 必須是 true 或 false", field="overwrite"))
        overwrite = False

    settings = _parse_settings(data.get("settings"), errors)
    segments = _parse_segments(data.get("segments"), errors)
    return Project(output=output, segments=segments, settings=settings, overwrite=overwrite), errors
