"""精華剪輯後台：從片段選取影格範圍，並以 ffmpeg 合併成一支影片。

用法見 highlight_editor/README.md；資料格式見 highlight_editor/openapi.yaml。
"""

from .catalog import list_clips
from .project import exit_code_for
from .render import render_project, validate_project

__all__ = ["list_clips", "validate_project", "render_project", "exit_code_for"]
