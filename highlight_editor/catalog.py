"""讀取 make_clip.py 產生的 manifest.csv，列出 UI 可選的片段。"""

import csv
import os
from typing import Any, Dict, List, Optional, Sequence

from . import media
from .project import EditorError, error_result, resolve_path

TEXT_FIELDS = ("label", "game_pk", "inning", "half", "event_type", "description", "official_highlight_title")


def _parse_bool(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def _parse_float(value: Optional[str]) -> Optional[float]:
    try:
        return float((value or "").strip())
    except ValueError:
        return None


def _clip_entry(row: Dict[str, Optional[str]], manifest: str, base_dir: str) -> Dict[str, Any]:
    clip = (row.get("clip_path") or "").strip()
    entry: Dict[str, Any] = {"clip": clip, "manifest": manifest}
    for name in TEXT_FIELDS:
        entry[name] = row.get(name) or ""
    entry["is_official_highlight"] = _parse_bool(row.get("is_official_highlight"))
    entry["duration"] = _parse_float(row.get("clip_duration_seconds"))
    path = resolve_path(base_dir, clip) if clip else ""
    entry.update(
        exists=bool(path) and os.path.isfile(path),
        fps=None, frame_count=None, width=None, height=None, has_audio=None,
    )
    if entry["exists"]:
        try:
            info = media.probe(path)
        except media.MediaError as exc:
            if exc.code == "ffmpeg_not_found":
                raise
            return entry
        entry.update(
            fps=info.fps_text, frame_count=info.frame_count,
            width=info.width, height=info.height, has_audio=info.has_audio,
        )
    return entry


def list_clips(manifests: Sequence[str], base_dir: str = ".") -> Dict[str, Any]:
    """列出一或多個 manifest 中的片段；回傳 ListClipsResult 或 ErrorResult。"""
    missing = [manifest for manifest in manifests if not os.path.isfile(resolve_path(base_dir, manifest))]
    if missing:
        return error_result([
            EditorError("manifest_not_found", f"找不到 manifest：{manifest}", field="manifest")
            for manifest in missing
        ])
    clips: List[Dict[str, Any]] = []
    try:
        for manifest in manifests:
            with open(resolve_path(base_dir, manifest), encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    clips.append(_clip_entry(row, manifest, base_dir))
    except media.MediaError as exc:
        return error_result([exc.to_error()])
    except (OSError, UnicodeDecodeError) as exc:
        return error_result([EditorError("io_error", f"無法讀取 manifest：{exc}", field="manifest")])
    return {"ok": True, "clips": clips}
