"""Highlight Builder backend.

Follows the same contract as highlight_editor (docs/api/highlight-editor.openapi.yaml):
``python -m highlight_builder <command>`` prints one JSON object on stdout, progress as
JSON Lines on stderr, and exits 0 (ok), 1 (input or validation error) or 2 (tool or
filesystem error). The HTTP server in ``highlight_builder.server`` calls these commands
as subprocesses.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

__all__ = ["REPO_ROOT"]
