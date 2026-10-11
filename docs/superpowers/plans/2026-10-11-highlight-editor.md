# highlight_editor 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 建立 `highlight_editor/` 套件。UI 送出「選取的片段＋每段 `[start_frame, end_frame)`」，後台用 ffmpeg 把它們精準裁切、統一規格，再合併成一支 mp4，並以 JSON 回報結果或錯誤。

**Architecture：**
- 純函式的專案驗證（`project.py`）、ffprobe/ffmpeg 包裝（`media.py`）、manifest 片段清單（`catalog.py`）、兩階段渲染流程（`render.py`），以及輸出 JSON 的 CLI（`__main__.py`）。
- 渲染分兩階段。第一階段逐段處理：先編碼視訊、讀出實際影格數 n，再把音訊精準截成 `n / fps` 並封裝成 .mov 中間檔。第二階段用 concat demuxer 串接，視訊直接複製，音訊只編一次 AAC。
- Python API 與 CLI 回傳相同結構的 dict / JSON。

**Tech Stack：** Python 標準庫（argparse、dataclasses、fractions、json、csv、subprocess、tempfile、unittest），系統 PATH 上的 ffmpeg 8.x / ffprobe。PyYAML 只在合約測試中使用。

**Spec：** `docs/superpowers/specs/2026-10-11-highlight-editor-design.md`；資料格式：`highlight_editor/openapi.yaml`

## Global Constraints

- **依賴**：只用 Python 標準庫，加上系統 `PATH` 上的 `ffmpeg`、`ffprobe`。PyYAML 只在 `test_contract.py` 使用，無法 import 時 `skipTest`。
- **語法**：相容 Python 3.8，使用 `typing.List/Optional/Dict/Tuple`。不用 `X | None`、`match`、`list[int]` 這類寫法。
- **不修改任何既有檔案**，包括 `sport_hightlight/`、`target_mask/`、`README.md`、`CLAUDE.md`、`.gitignore`、`.gitattributes`。只新增 `highlight_editor/` 與 `docs/` 底下的檔案。
- **語言**：使用者看得到的文字（`errors[].message`、CLI help、README）用繁體中文；識別字與 JSON 欄位名用英文。
- **影格**：0 起算的半開區間 `[start_frame, end_frame)`，以該片段自己的 fps 計。
- **stdout**：只輸出一個 JSON 物件（`ensure_ascii=False`、UTF-8）。進度以 JSON Lines 輸出到 stderr。
- **exit code**：`0` 成功；`1` 輸入或驗證錯誤；`2` 表示錯誤代碼中含 `ffmpeg_not_found`、`ffmpeg_failed` 或 `io_error`。
- **秒數**：一律 `round(x, 3)`。
- **執行位置**：在 worktree 根目錄 `D:\Developing\sprint\.claude\worktrees\highlight-editor` 執行所有指令。worktree 內沒有 `.venv`，下文的 `python` 指 `D:\Developing\sprint\.venv\Scripts\python.exe`。
- **測試指令**：全部測試用 `python -m unittest discover -s highlight_editor/tests -t .`；單一模組用 `python -m unittest highlight_editor.tests.test_project -v`。測試用 lavfi 產生合成片段，不需要 LFS 影片或網路。
- **程式風格**：四格縮排、snake_case、小函式、dataclass、type annotations、argparse，與既有的 `sport_hightlight/make_clip.py` 一致。
- **Commit 訊息**：結尾加上這兩行：
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
  ```

## Review Focus

以下是 spec 沒有直接寫成測試、但最可能在實際使用時出錯的輸入情況。每一項都已在負責的 task 裡加上對應測試。

1. **第一段來源的寬高是奇數**（例如 853×481）：輸出規格要修成 852×480 並成功編碼，而不是讓 libx264 因奇數尺寸失敗。驗證與渲染兩邊都要測，見 Task 4 `test_odd_dimension_first_clip_rounds_to_even` 與 Task 5 `test_odd_dimension_first_clip`。
2. **片段的音軌比畫面短**（音訊在影片中途結束）：合併後音訊仍與視訊等長，缺的部分補靜音。見 Task 5 `test_segment_audio_matches_video_length`。
3. **專案 JSON 以 UTF-8 BOM 存檔**（Windows 記事本的預設）：CLI 要能正常讀取。見 Task 6 `test_validate_project_saved_with_bom`。
4. **省略 `end_frame`，但 `start_frame` ≥ 片段影格數**：要回傳 `out_of_range`（field 為 `start_frame`），不執行 ffmpeg。見 Task 4 `test_start_frame_at_frame_count_with_open_end`。
5. **輸出目錄還不存在**（例如 `out/new/nested/reel.mp4`）：`render` 會自動建立，`validate` 不建立任何東西。見 Task 5 `test_output_directory_is_created` 與 Task 4 `test_resolves_end_frame_and_settings` 的最後一行斷言。

---

## 檔案結構

| 檔案 | 職責 | Task |
|---|---|---|
| `highlight_editor/__init__.py` | 套件說明。Task 5 起對外匯出 `list_clips`、`validate_project`、`render_project`、`exit_code_for` | 1、5 |
| `highlight_editor/project.py` | `EditorError`、`error_result`、`exit_code_for`、資料模型（`Project`、`Segment`、`OutputSettings`）、`parse_project` 結構驗證、`resolve_path`、`same_path` | 1 |
| `highlight_editor/media.py` | `MediaError`、`MediaInfo`、`run_tool`、`parse_rate`、`probe`、`count_frames`、ffmpeg 指令組裝、`audio_window`、`write_concat_list` | 2 |
| `highlight_editor/catalog.py` | `list_clips`：讀 manifest，探測存在的片段 | 3 |
| `highlight_editor/render.py` | `_prepare`（結構驗證＋檔案檢查＋探測＋決定規格）、`validate_project`、`render_project` | 4、5 |
| `highlight_editor/__main__.py` | CLI：`list`、`validate`、`render`；stdio 設成 UTF-8；決定 exit code | 6 |
| `highlight_editor/tests/__init__.py` | 空檔，讓 `tests` 成為套件 | 1 |
| `highlight_editor/tests/helpers.py` | 測試工具：合成片段、ffprobe 讀值、PSNR、manifest、`run_cli` | 2 |
| `highlight_editor/tests/test_project.py` | 純邏輯測試 | 1 |
| `highlight_editor/tests/test_media.py` | 指令組裝、`run_tool`、`probe` | 2 |
| `highlight_editor/tests/test_catalog.py` | `list_clips` | 3 |
| `highlight_editor/tests/test_render.py` | `validate_project`（Task 4）、`render_project`（Task 5） | 4、5 |
| `highlight_editor/tests/test_cli.py` | CLI 子程序與 exit code | 6 |
| `highlight_editor/tests/schema_check.py` | `openapi.yaml` 用到的 schema 規則的簡易驗證器 | 7 |
| `highlight_editor/tests/test_contract.py` | `openapi.yaml` 本身，以及實際輸出符合它 | 7 |
| `highlight_editor/README.md`、`highlight_editor/example_project.json` | 給 UI 同事的串接說明與範例 | 8 |

`highlight_editor/openapi.yaml` 已經存在（commit `fc61e7d`），本計畫不修改它。若實作時發現它與 spec 不一致，先停下來回報，不要自行修改。

---

### Task 1：專案資料模型與結構驗證（`project.py`）

**Files:**
- Create: `highlight_editor/__init__.py`
- Create: `highlight_editor/project.py`
- Create: `highlight_editor/tests/__init__.py`
- Test: `highlight_editor/tests/test_project.py`

**Interfaces:**
- Consumes：無
- Produces：
  - `EditorError(code: str, message: str, segment_id: Optional[str] = None, field: Optional[str] = None, detail: Optional[str] = None)`，以及 `.to_dict() -> Dict[str, Any]`。`detail` 只在不是 None 時才輸出。
  - `error_result(errors: List[EditorError]) -> Dict[str, Any]`，回傳 `{"ok": False, "errors": [...]}`。
  - `exit_code_for(result: Dict[str, Any]) -> int`
  - `SYSTEM_ERROR_CODES: frozenset`、`PRESETS: Tuple[str, ...]`、`DEFAULT_CRF = 20`、`DEFAULT_PRESET = "fast"`
  - `OutputSettings(width: Optional[int], height: Optional[int], fps: Optional[str], crf: int, preset: str)`
  - `Segment(id: str, clip: str, start_frame: int = 0, end_frame: Optional[int] = None)`
  - `Project(output: str, segments: List[Segment], settings: OutputSettings, overwrite: bool = False, version: int = 1)`
  - `parse_project(data: Any) -> Tuple[Optional[Project], List[EditorError]]`。頂層不是 dict 時 project 為 None。否則 project 只包含結構正確的段落；`output` 不合法時為 `""`。
  - `parse_fps(value: Any) -> Optional[str]`
  - `resolve_path(base_dir: str, path: str) -> str`，回傳絕對路徑，不檢查檔案是否存在。
  - `same_path(a: str, b: str) -> bool`

- [ ] **Step 1：建立套件骨架**

`highlight_editor/__init__.py`：

```python
"""精華剪輯後台：從片段選取影格範圍，並以 ffmpeg 合併成一支影片。"""
```

`highlight_editor/tests/__init__.py`：空檔（0 bytes）。

- [ ] **Step 2：寫失敗的測試**

`highlight_editor/tests/test_project.py`：

```python
import os
import unittest

from highlight_editor.project import (
    DEFAULT_CRF,
    DEFAULT_PRESET,
    EditorError,
    error_result,
    exit_code_for,
    parse_project,
    resolve_path,
    same_path,
)


def project(**overrides):
    data = {"output": "out/reel.mp4", "segments": [{"clip": "a.mp4", "start_frame": 0, "end_frame": 30}]}
    data.update(overrides)
    return data


def codes(errors):
    return [(error.code, error.segment_id, error.field) for error in errors]


class ParseProjectTests(unittest.TestCase):
    def test_minimal_project_uses_defaults(self):
        proj, errors = parse_project({"output": "o.mp4", "segments": [{"clip": "a.mp4"}]})
        self.assertEqual(errors, [])
        self.assertEqual(proj.version, 1)
        self.assertFalse(proj.overwrite)
        self.assertEqual((proj.settings.crf, proj.settings.preset), (DEFAULT_CRF, DEFAULT_PRESET))
        self.assertIsNone(proj.settings.width)
        self.assertIsNone(proj.settings.height)
        self.assertIsNone(proj.settings.fps)
        segment = proj.segments[0]
        self.assertEqual((segment.id, segment.clip, segment.start_frame, segment.end_frame), ("1", "a.mp4", 0, None))

    def test_top_level_must_be_object(self):
        proj, errors = parse_project([1, 2])
        self.assertIsNone(proj)
        self.assertEqual(codes(errors), [("invalid_json", None, None)])

    def test_missing_output_and_segments(self):
        proj, errors = parse_project({})
        self.assertEqual(codes(errors), [("missing_field", None, "output"), ("missing_field", None, "segments")])
        self.assertEqual(proj.output, "")

    def test_output_must_be_mp4(self):
        _, errors = parse_project(project(output="out/reel.mov"))
        self.assertEqual(codes(errors), [("invalid_value", None, "output")])
        _, errors = parse_project(project(output="out/REEL.MP4"))
        self.assertEqual(errors, [])
        _, errors = parse_project(project(output="  "))
        self.assertEqual(codes(errors), [("invalid_value", None, "output")])

    def test_version(self):
        self.assertEqual(parse_project(project(version=1.0))[1], [])
        self.assertEqual(parse_project(project(version=None))[1], [])
        for bad in (2, "1", True):
            with self.subTest(version=bad):
                self.assertEqual(codes(parse_project(project(version=bad))[1]), [("invalid_value", None, "version")])

    def test_overwrite_must_be_bool(self):
        self.assertTrue(parse_project(project(overwrite=True))[0].overwrite)
        self.assertEqual(codes(parse_project(project(overwrite="yes"))[1]), [("invalid_value", None, "overwrite")])

    def test_segments_must_be_non_empty_list(self):
        self.assertEqual(codes(parse_project(project(segments=[]))[1]), [("invalid_value", None, "segments")])
        self.assertEqual(codes(parse_project(project(segments={"clip": "a.mp4"}))[1]), [("invalid_value", None, "segments")])

    def test_frame_rules(self):
        cases = [
            ({"start_frame": -1}, ("invalid_value", "1", "start_frame")),
            ({"start_frame": 1.5}, ("invalid_value", "1", "start_frame")),
            ({"start_frame": True}, ("invalid_value", "1", "start_frame")),
            ({"start_frame": "3"}, ("invalid_value", "1", "start_frame")),
            ({"end_frame": 0}, ("invalid_value", "1", "end_frame")),
            ({"start_frame": 10, "end_frame": 10}, ("out_of_range", "1", "end_frame")),
            ({"start_frame": 20, "end_frame": 10}, ("out_of_range", "1", "end_frame")),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                proj, errors = parse_project(project(segments=[{"clip": "a.mp4", **fields}]))
                self.assertEqual(codes(errors), [expected])
                self.assertEqual(proj.segments, [])

    def test_integral_float_frames_are_accepted(self):
        proj, errors = parse_project(project(segments=[{"clip": "a.mp4", "start_frame": 120.0, "end_frame": 480.0}]))
        self.assertEqual(errors, [])
        segment = proj.segments[0]
        self.assertEqual((segment.start_frame, segment.end_frame), (120, 480))
        self.assertIsInstance(segment.start_frame, int)
        self.assertIsInstance(segment.end_frame, int)

    def test_null_frames_use_defaults(self):
        proj, errors = parse_project(project(segments=[{"clip": "a.mp4", "start_frame": None, "end_frame": None}]))
        self.assertEqual(errors, [])
        self.assertEqual((proj.segments[0].start_frame, proj.segments[0].end_frame), (0, None))

    def test_segment_must_be_object_and_have_clip(self):
        proj, errors = parse_project(project(segments=["a.mp4", {"start_frame": 0}, {"clip": ""}]))
        self.assertEqual(codes(errors), [
            ("invalid_value", "1", None),
            ("missing_field", "2", "clip"),
            ("invalid_value", "3", "clip"),
        ])
        self.assertEqual(proj.segments, [])

    def test_ids_default_and_must_be_unique(self):
        proj, errors = parse_project(project(segments=[
            {"clip": "a.mp4"},
            {"clip": "b.mp4", "id": "1"},
            {"clip": "c.mp4", "id": "hr"},
        ]))
        self.assertEqual(codes(errors), [("invalid_value", "1", "id")])
        self.assertEqual([segment.id for segment in proj.segments], ["1", "hr"])

    def test_id_must_be_non_empty_string(self):
        _, errors = parse_project(project(segments=[{"clip": "a.mp4", "id": 7}]))
        self.assertEqual(codes(errors), [("invalid_value", "1", "id")])

    def test_same_clip_can_repeat(self):
        proj, errors = parse_project(project(segments=[
            {"clip": "a.mp4", "end_frame": 10},
            {"clip": "a.mp4", "start_frame": 50, "end_frame": 70},
        ]))
        self.assertEqual(errors, [])
        self.assertEqual([segment.id for segment in proj.segments], ["1", "2"])

    def test_unknown_fields_are_ignored(self):
        proj, errors = parse_project({
            "output": "o.mp4", "ui_note": "keep",
            "segments": [{"clip": "a.mp4", "thumbnail": "x.png"}],
        })
        self.assertEqual(errors, [])
        self.assertEqual(len(proj.segments), 1)

    def test_all_errors_are_collected(self):
        _, errors = parse_project({
            "output": "x.mov",
            "settings": {"width": 1281, "preset": "placebo"},
            "segments": [
                {"clip": "a.mp4", "start_frame": -5},
                {"id": "k", "clip": "b.mp4", "start_frame": 9, "end_frame": 3},
            ],
        })
        self.assertEqual(codes(errors), [
            ("invalid_value", None, "output"),
            ("invalid_value", None, "settings.width"),
            ("invalid_value", None, "settings.preset"),
            ("invalid_value", "1", "start_frame"),
            ("out_of_range", "k", "end_frame"),
        ])


class SettingsTests(unittest.TestCase):
    def settings_errors(self, **settings):
        return codes(parse_project(project(settings=settings))[1])

    def test_settings_must_be_object(self):
        self.assertEqual(codes(parse_project(project(settings="fast"))[1]), [("invalid_value", None, "settings")])

    def test_width_and_height_must_be_positive_even(self):
        proj, errors = parse_project(project(settings={"width": 1280, "height": 720.0}))
        self.assertEqual(errors, [])
        self.assertEqual((proj.settings.width, proj.settings.height), (1280, 720))
        for bad in (1281, 0, -2, "1280", 1.5, True):
            with self.subTest(width=bad):
                self.assertEqual(self.settings_errors(width=bad), [("invalid_value", None, "settings.width")])
        self.assertEqual(self.settings_errors(height=481), [("invalid_value", None, "settings.height")])

    def test_fps_forms(self):
        for value, expected in ((30, "30"), (29.97, "29.97"), ("30000/1001", "30000/1001"), (" 25 ", "25")):
            with self.subTest(fps=value):
                proj, errors = parse_project(project(settings={"fps": value}))
                self.assertEqual(errors, [])
                self.assertEqual(proj.settings.fps, expected)
        for bad in (0, -1, "0", "abc", "1/0", True, [30]):
            with self.subTest(fps=bad):
                self.assertEqual(self.settings_errors(fps=bad), [("invalid_value", None, "settings.fps")])

    def test_crf_range(self):
        for good in (0, 51, 20.0):
            with self.subTest(crf=good):
                self.assertEqual(self.settings_errors(crf=good), [])
        for bad in (-1, 52, 20.5, "20"):
            with self.subTest(crf=bad):
                self.assertEqual(self.settings_errors(crf=bad), [("invalid_value", None, "settings.crf")])

    def test_preset_names(self):
        self.assertEqual(parse_project(project(settings={"preset": "veryslow"}))[0].settings.preset, "veryslow")
        for bad in ("placebo", "FAST", 3):
            with self.subTest(preset=bad):
                self.assertEqual(self.settings_errors(preset=bad), [("invalid_value", None, "settings.preset")])


class ResultHelperTests(unittest.TestCase):
    def test_error_dict_includes_detail_only_when_present(self):
        self.assertEqual(
            EditorError("out_of_range", "m", segment_id="k", field="end_frame").to_dict(),
            {"code": "out_of_range", "segment_id": "k", "field": "end_frame", "message": "m"},
        )
        self.assertEqual(EditorError("ffmpeg_failed", "m", detail="tail").to_dict()["detail"], "tail")

    def test_exit_codes(self):
        self.assertEqual(exit_code_for({"ok": True}), 0)
        self.assertEqual(exit_code_for(error_result([EditorError("out_of_range", "m")])), 1)
        self.assertEqual(
            exit_code_for(error_result([EditorError("out_of_range", "m"), EditorError("ffmpeg_failed", "m")])), 2,
        )
        for code in ("ffmpeg_not_found", "io_error"):
            self.assertEqual(exit_code_for(error_result([EditorError(code, "m")])), 2)

    def test_paths(self):
        base = os.path.abspath("base")
        self.assertEqual(resolve_path(base, "clips/a.mp4"), os.path.join(base, "clips", "a.mp4"))
        absolute = os.path.abspath("elsewhere.mp4")
        self.assertEqual(resolve_path(base, absolute), absolute)
        self.assertTrue(same_path(os.path.join(base, "x", "..", "a.mp4"), os.path.join(base, "a.mp4")))
        self.assertFalse(same_path(os.path.join(base, "a.mp4"), os.path.join(base, "b.mp4")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3：執行測試，確認失敗**

Run: `python -m unittest highlight_editor.tests.test_project -v`
Expected: FAIL / ERROR，`ModuleNotFoundError: No module named 'highlight_editor.project'`

- [ ] **Step 4：實作 `project.py`**

`highlight_editor/project.py`：

```python
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
```

- [ ] **Step 5：執行測試，確認通過**

Run: `python -m unittest highlight_editor.tests.test_project -v`
Expected: 全部 PASS（24 個測試）

- [ ] **Step 6：Commit**

```bash
git add highlight_editor/__init__.py highlight_editor/project.py highlight_editor/tests/__init__.py highlight_editor/tests/test_project.py
git commit -F - <<'EOF'
Add highlight_editor project model and validation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
EOF
```

---

### Task 2：ffprobe / ffmpeg 包裝（`media.py`）與測試工具

**Files:**
- Create: `highlight_editor/media.py`
- Create: `highlight_editor/tests/helpers.py`
- Test: `highlight_editor/tests/test_media.py`

**Interfaces:**
- Consumes：`highlight_editor.project.EditorError`
- Produces（`media.py`）：
  - 模組層級常數 `FFMPEG = "ffmpeg"`、`FFPROBE = "ffprobe"`、`STDERR_TAIL_LINES = 20`、`AUDIO_SAMPLE_RATE = 48000`。測試會用 `mock.patch.object` 替換前兩個，所以函式要在**呼叫時**才讀取它們。
  - `class MediaError(Exception)`：屬性 `code`、`message`、`detail`；方法 `.to_error(segment_id=None, field=None) -> EditorError`。
  - `MediaInfo(fps: Fraction, fps_text: str, frame_count: int, width: int, height: int, has_audio: bool, video_start: float)`
  - `run_tool(command: Sequence[str]) -> subprocess.CompletedProcess`：找不到執行檔時丟 `ffmpeg_not_found`，exit code 非 0 時丟 `ffmpeg_failed`，`detail` 為 stderr 的最後 20 行。
  - `parse_rate(text: Optional[str]) -> Optional[Fraction]`、`rate_text(rate: Fraction) -> str`，例如 `"30000/1001"`、`"25/1"`。
  - `probe(path: str) -> MediaInfo`：失敗時丟 `invalid_media`；找不到 ffprobe 時丟 `ffmpeg_not_found`。
  - `count_frames(path: str) -> int`
  - `build_video_command(clip, start_frame, end_frame, width, height, fps, crf, preset, output) -> List[str]`
  - `audio_window(start_frame: int, end_frame: int, fps: Fraction, video_start: float) -> Tuple[float, float]`
  - `build_audio_mux_command(video_path, clip, has_audio, audio_start, audio_end, duration, output) -> List[str]`
  - `write_concat_list(file_names: Sequence[str], list_path: str) -> None`
  - `build_concat_command(list_path: str, output: str) -> List[str]`
- Produces（`tests/helpers.py`，後續測試都會用）：`REPO_ROOT`、`FFMPEG_AVAILABLE`、`SKIP_REASON`、`MANIFEST_HEADER`、`make_clip`、`make_odd_clip`、`frame_count`、`stream_duration`、`video_props`、`audio_codec`、`frame_png`、`psnr`、`leftover_work_dirs`、`manifest_row`、`write_manifest`、`run_cli`

- [ ] **Step 1：建立測試工具 `helpers.py`**

`highlight_editor/tests/helpers.py`：

```python
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
```

- [ ] **Step 2：寫失敗的測試**

`highlight_editor/tests/test_media.py`：

```python
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
            "-c:v", "libx264", "-crf", "20", "-preset", "fast", "seg_0001_v.mov",
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
```

- [ ] **Step 3：執行測試，確認失敗**

Run: `python -m unittest highlight_editor.tests.test_media -v`
Expected: ERROR，`ModuleNotFoundError: No module named 'highlight_editor.media'`

- [ ] **Step 4：實作 `media.py`**

`highlight_editor/media.py`：

```python
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
    """第一次呼叫：只編碼視訊。trim 依解碼的影格計數，所以張數精準。"""
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
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset, output,
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
```

- [ ] **Step 5：執行測試，確認通過**

Run: `python -m unittest highlight_editor.tests.test_media -v`
Expected: 全部 PASS（18 個測試）。這台機器有 ffmpeg，所以 `ProbeTests` 也會執行，不會被略過。

- [ ] **Step 6：Commit**

```bash
git add highlight_editor/media.py highlight_editor/tests/helpers.py highlight_editor/tests/test_media.py
git commit -F - <<'EOF'
Add ffprobe/ffmpeg wrappers and test helpers for highlight_editor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
EOF
```

---

### Task 3：片段清單（`catalog.py`）

**Files:**
- Create: `highlight_editor/catalog.py`
- Test: `highlight_editor/tests/test_catalog.py`

**Interfaces:**
- Consumes：`media.probe`、`media.MediaError`、`project.EditorError`、`project.error_result`、`project.resolve_path`；測試工具 `make_clip`、`manifest_row`、`write_manifest`、`frame_count`、`MANIFEST_HEADER`
- Produces：`list_clips(manifests: Sequence[str], base_dir: str = ".") -> Dict[str, Any]`，回傳 `ListClipsResult` 或 `ErrorResult`（見 spec §5.2）。

- [ ] **Step 1：寫失敗的測試**

`highlight_editor/tests/test_catalog.py`：

```python
import tempfile
import unittest
from pathlib import Path

from highlight_editor.catalog import list_clips
from highlight_editor.tests.helpers import (
    FFMPEG_AVAILABLE,
    MANIFEST_HEADER,
    SKIP_REASON,
    frame_count,
    make_clip,
    manifest_row,
    write_manifest,
)

PROBE_FIELDS = ("fps", "frame_count", "width", "height", "has_audio")


class ListClipsWithoutMediaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_missing_manifest(self):
        result = list_clips(["nope.csv", "also_missing.csv"], base_dir=str(self.dir))
        self.assertFalse(result["ok"])
        self.assertEqual([(e["code"], e["field"]) for e in result["errors"]], [
            ("manifest_not_found", "manifest"), ("manifest_not_found", "manifest"),
        ])
        self.assertIn("nope.csv", result["errors"][0]["message"])

    def test_missing_clip_is_listed_with_null_probe_fields(self):
        write_manifest(self.dir / "manifest.csv", [manifest_row("clips/missing.mp4", is_official_highlight="False")])
        result = list_clips(["manifest.csv"], base_dir=str(self.dir))
        self.assertTrue(result["ok"])
        clip = result["clips"][0]
        self.assertFalse(clip["exists"])
        for key in PROBE_FIELDS:
            self.assertIsNone(clip[key], key)
        self.assertIs(clip["is_official_highlight"], False)
        self.assertEqual(clip["duration"], 20.0)

    def test_old_manifest_with_bom_and_empty_values(self):
        header = [name for name in MANIFEST_HEADER if name != "source_video"]
        row = manifest_row("clips/old.mp4", game_pk="", clip_duration_seconds="")
        write_manifest(self.dir / "old.csv", [row], header=header, bom=True)
        clip = list_clips(["old.csv"], base_dir=str(self.dir))["clips"][0]
        self.assertEqual(clip["clip"], "clips/old.mp4")
        self.assertEqual(clip["game_pk"], "")
        self.assertIsNone(clip["duration"])

    def test_multiple_manifests_keep_order(self):
        write_manifest(self.dir / "m1.csv", [manifest_row("a.mp4"), manifest_row("b.mp4")])
        write_manifest(self.dir / "m2.csv", [manifest_row("c.mp4")])
        result = list_clips(["m1.csv", "m2.csv"], base_dir=str(self.dir))
        self.assertEqual([(c["clip"], c["manifest"]) for c in result["clips"]], [
            ("a.mp4", "m1.csv"), ("b.mp4", "m1.csv"), ("c.mp4", "m2.csv"),
        ])


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class ListClipsWithMediaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_lists_rows_with_probe_fields(self):
        clip_rel = "clips/positive/event_0001_2bottom_Home_Run.mp4"
        clip_path = make_clip(self.dir / clip_rel, size="640x360", seconds=2)
        write_manifest(self.dir / "manifest.csv", [manifest_row(
            clip_rel, label="positive", event_type="Home Run", description="全壘打 測試",
            is_official_highlight="True", official_highlight_title="Official HR",
        )])
        result = list_clips(["manifest.csv"], base_dir=str(self.dir))
        self.assertTrue(result["ok"])
        self.assertEqual(result["clips"], [{
            "clip": clip_rel,
            "manifest": "manifest.csv",
            "label": "positive",
            "game_pk": "823734",
            "inning": "2",
            "half": "bottom",
            "event_type": "Home Run",
            "description": "全壘打 測試",
            "official_highlight_title": "Official HR",
            "is_official_highlight": True,
            "duration": 20.0,
            "exists": True,
            "fps": "30000/1001",
            "frame_count": frame_count(clip_path),
            "width": 640,
            "height": 360,
            "has_audio": True,
        }])

    def test_corrupt_clip_is_listed_without_probe_fields(self):
        bad = self.dir / "clips" / "bad.mp4"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"garbage")
        write_manifest(self.dir / "manifest.csv", [manifest_row("clips/bad.mp4")])
        clip = list_clips(["manifest.csv"], base_dir=str(self.dir))["clips"][0]
        self.assertTrue(clip["exists"])
        for key in PROBE_FIELDS:
            self.assertIsNone(clip[key], key)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2：執行測試，確認失敗**

Run: `python -m unittest highlight_editor.tests.test_catalog -v`
Expected: ERROR，`ModuleNotFoundError: No module named 'highlight_editor.catalog'`

- [ ] **Step 3：實作 `catalog.py`**

`highlight_editor/catalog.py`：

```python
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
```

- [ ] **Step 4：執行測試，確認通過**

Run: `python -m unittest highlight_editor.tests.test_catalog -v`
Expected: 全部 PASS（6 個測試）

- [ ] **Step 5：Commit**

```bash
git add highlight_editor/catalog.py highlight_editor/tests/test_catalog.py
git commit -F - <<'EOF'
Add manifest clip listing for highlight_editor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
EOF
```

---

### Task 4：驗證流程（`render.py` 的 `_prepare` 與 `validate_project`）

**Files:**
- Create: `highlight_editor/render.py`
- Test: `highlight_editor/tests/test_render.py`

**Interfaces:**
- Consumes：`project.parse_project`、`project.EditorError`、`project.error_result`、`project.resolve_path`、`project.same_path`、`project.exit_code_for`、`project.Project`、`project.Segment`、`media.probe`、`media.MediaError`、`media.MediaInfo`
- Produces：
  - `PreparedSegment(segment: Segment, path: str, info: MediaInfo, end_frame: int)`
  - `Prepared(project: Project, output_path: str, segments: List[PreparedSegment], width: int, height: int, fps_text: str, fps: Fraction)`
  - `_prepare(data: Any, base_dir: str, overwrite: bool) -> Tuple[Optional[Prepared], List[EditorError]]`。Task 5 也會用到。
  - `_settings_dict(prepared: Prepared) -> Dict[str, Any]`、`_seconds(frames: int, fps: Fraction) -> float`
  - `validate_project(data: Any, base_dir: str = ".") -> Dict[str, Any]`，回傳 `ValidateResult` 或 `ErrorResult`（spec §5.3）。
  - `ProgressCallback = Callable[[Dict[str, Any]], None]`

- [ ] **Step 1：寫失敗的測試**

`highlight_editor/tests/test_render.py`：

```python
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from highlight_editor import media
from highlight_editor.project import exit_code_for
from highlight_editor.render import validate_project
from highlight_editor.tests.helpers import (
    FFMPEG_AVAILABLE,
    SKIP_REASON,
    frame_count,
    make_clip,
    make_odd_clip,
)


def error_codes(result):
    return [(error["code"], error["segment_id"], error["field"]) for error in result["errors"]]


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class ValidateProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        cls.clip_a = make_clip(cls.dir / "clips" / "a.mp4", size="640x360", rate="30000/1001", seconds=2)
        cls.frames_a = frame_count(cls.clip_a)
        cls.clip_odd = make_odd_clip(cls.dir / "clips" / "odd.mkv", width=853, height=481, seconds=1)
        bad = cls.dir / "clips" / "bad.mp4"
        bad.write_bytes(b"not a video")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def validate(self, *segments, **fields):
        data = {"output": fields.pop("output", "out/reel.mp4"), "segments": list(segments), **fields}
        return validate_project(data, base_dir=str(self.dir))

    def test_resolves_end_frame_and_settings(self):
        result = self.validate({"id": "a", "clip": "clips/a.mp4", "start_frame": 6}, output="fresh/dir/reel.mp4")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"], "fresh/dir/reel.mp4")
        self.assertEqual(result["settings"], {
            "width": 640, "height": 360, "fps": "30000/1001", "crf": 20, "preset": "fast",
        })
        self.assertEqual(result["segments"], [{
            "id": "a", "clip": "clips/a.mp4", "start_frame": 6, "end_frame": self.frames_a,
            "fps": "30000/1001", "frame_count": self.frames_a,
        }])
        self.assertEqual(result["estimated_duration"], round((self.frames_a - 6) * 1001 / 30000, 3))
        self.assertFalse((self.dir / "fresh").exists(), "validate 不可建立任何目錄或檔案")

    def test_settings_override_first_segment(self):
        result = self.validate({"clip": "clips/a.mp4"}, settings={"width": 320, "height": 180, "fps": 25, "crf": 23})
        self.assertEqual(result["settings"], {"width": 320, "height": 180, "fps": "25", "crf": 23, "preset": "fast"})

    def test_odd_dimension_first_clip_rounds_to_even(self):
        result = self.validate({"clip": "clips/odd.mkv"}, {"clip": "clips/a.mp4"})
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["settings"]["width"], result["settings"]["height"]), (852, 480))

    def test_end_frame_equal_to_frame_count_is_valid(self):
        result = self.validate({"clip": "clips/a.mp4", "end_frame": self.frames_a})
        self.assertTrue(result["ok"], result)

    def test_end_frame_beyond_clip(self):
        result = self.validate({"clip": "clips/a.mp4", "end_frame": self.frames_a + 1})
        self.assertEqual(error_codes(result), [("out_of_range", "1", "end_frame")])
        self.assertIn(str(self.frames_a), result["errors"][0]["message"])

    def test_start_frame_at_frame_count_with_open_end(self):
        result = self.validate({"clip": "clips/a.mp4", "start_frame": self.frames_a})
        self.assertEqual(error_codes(result), [("out_of_range", "1", "start_frame")])

    def test_missing_clip(self):
        result = self.validate({"clip": "clips/nope.mp4"})
        self.assertEqual(error_codes(result), [("clip_not_found", "1", "clip")])
        self.assertEqual(exit_code_for(result), 1)

    def test_garbage_clip(self):
        result = self.validate({"clip": "clips/bad.mp4"})
        self.assertEqual(error_codes(result), [("invalid_media", "1", "clip")])

    def test_output_exists_unless_overwrite(self):
        existing = self.dir / "out" / "exists.mp4"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"old")
        self.addCleanup(existing.unlink)
        result = self.validate({"clip": "clips/a.mp4"}, output="out/exists.mp4")
        self.assertEqual(error_codes(result), [("output_exists", None, "output")])
        result = self.validate({"clip": "clips/a.mp4"}, output="out/exists.mp4", overwrite=True)
        self.assertTrue(result["ok"], result)

    def test_output_same_as_clip(self):
        result = self.validate({"id": "a", "clip": "clips/a.mp4"}, output="clips/a.mp4", overwrite=True)
        self.assertEqual(error_codes(result), [("invalid_value", "a", "output")])

    def test_collects_structure_and_probe_errors_together(self):
        result = self.validate(
            {"id": "bad", "clip": "clips/a.mp4", "start_frame": -1},
            {"id": "missing", "clip": "clips/nope.mp4"},
            {"id": "long", "clip": "clips/a.mp4", "end_frame": 10 ** 6},
            settings={"crf": 99},
        )
        self.assertEqual(error_codes(result), [
            ("invalid_value", None, "settings.crf"),
            ("invalid_value", "bad", "start_frame"),
            ("clip_not_found", "missing", "clip"),
            ("out_of_range", "long", "end_frame"),
        ])

    def test_probes_each_clip_once(self):
        with mock.patch.object(media, "probe", wraps=media.probe) as probe:
            result = self.validate({"clip": "clips/a.mp4"}, {"clip": "./clips/a.mp4", "end_frame": 10})
        self.assertTrue(result["ok"], result)
        self.assertEqual(probe.call_count, 1)

    def test_ffmpeg_not_found(self):
        with mock.patch.object(media, "FFPROBE", "ffprobe-not-installed-xyz"):
            result = self.validate({"clip": "clips/a.mp4"})
        self.assertEqual(error_codes(result), [("ffmpeg_not_found", None, None)])
        self.assertEqual(exit_code_for(result), 2)

    def test_top_level_not_object(self):
        result = validate_project(["not", "an", "object"], base_dir=str(self.dir))
        self.assertEqual(error_codes(result), [("invalid_json", None, None)])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2：執行測試，確認失敗**

Run: `python -m unittest highlight_editor.tests.test_render -v`
Expected: ERROR，`ModuleNotFoundError: No module named 'highlight_editor.render'`

- [ ] **Step 3：實作 `render.py`（驗證部分）**

`highlight_editor/render.py`：

```python
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
```

- [ ] **Step 4：執行測試，確認通過**

Run: `python -m unittest highlight_editor.tests.test_render -v`
Expected: 全部 PASS（14 個測試）

- [ ] **Step 5：Commit**

```bash
git add highlight_editor/render.py highlight_editor/tests/test_render.py
git commit -F - <<'EOF'
Add project validation with media probing for highlight_editor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
EOF
```

---

### Task 5：渲染（`render_project`）與對外 API

**Files:**
- Modify: `highlight_editor/render.py`（新增 import 與函式，不改 Task 4 已有的程式碼）
- Modify: `highlight_editor/__init__.py`
- Test: `highlight_editor/tests/test_render.py`（新增 `RenderProjectTests`）

**Interfaces:**
- Consumes：Task 4 的 `_prepare`、`_settings_dict`、`_seconds`、`Prepared`、`ProgressCallback`；`media.run_tool`、`media.probe`、`media.build_video_command`、`media.audio_window`、`media.build_audio_mux_command`、`media.write_concat_list`、`media.build_concat_command`、`media.MediaError`
- Produces：
  - `render_project(data: Any, base_dir: str = ".", overwrite: bool = False, on_progress: Optional[ProgressCallback] = None) -> Dict[str, Any]`，回傳 `RenderResult` 或 `ErrorResult`（spec §5.4）。進度事件依 spec §5.5。
  - 套件匯出 `highlight_editor.list_clips`、`highlight_editor.validate_project`、`highlight_editor.render_project`、`highlight_editor.exit_code_for`。

- [ ] **Step 1：寫失敗的測試**

在 `highlight_editor/tests/test_render.py` 修改 import，並在 `if __name__ == "__main__":` 之前加入新的測試類別。

把檔案開頭的 import 區塊換成：

```python
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import highlight_editor
from highlight_editor import media
from highlight_editor.project import exit_code_for
from highlight_editor.render import render_project, validate_project
from highlight_editor.tests.helpers import (
    FFMPEG_AVAILABLE,
    SKIP_REASON,
    audio_codec,
    frame_count,
    frame_png,
    leftover_work_dirs,
    make_clip,
    make_odd_clip,
    psnr,
    stream_duration,
    video_props,
)

ONE_FRAME_2997 = 1001 / 30000
```

在 `if __name__ == "__main__":` 之前加入：

```python
class PackageExportTests(unittest.TestCase):
    def test_public_api(self):
        self.assertIs(highlight_editor.render_project, render_project)
        self.assertIs(highlight_editor.validate_project, validate_project)
        self.assertTrue(callable(highlight_editor.list_clips))
        self.assertIs(highlight_editor.exit_code_for, exit_code_for)


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class RenderProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        clips = cls.dir / "clips"
        cls.clip_a = make_clip(clips / "a.mp4", size="640x360", rate="30000/1001", seconds=3)
        cls.clip_b = make_clip(clips / "b_noaudio.mp4", size="320x240", rate="25", seconds=3, audio=False)
        cls.clip_c = make_clip(clips / "空白 與 中文" / "c.mp4", size="640x360", rate="30000/1001", seconds=2)
        cls.clip_short = make_clip(
            clips / "short_audio.mp4", size="640x360", rate="30000/1001", seconds=3, audio_seconds=1,
        )
        cls.clip_odd = make_odd_clip(clips / "odd.mkv", width=853, height=481, seconds=1)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def render(self, *segments, output=None, force=False, **fields):
        """force 對應 render_project(overwrite=...)；fields 是專案 JSON 的欄位（例如 overwrite=True）。"""
        output = output or f"out/{self._testMethodName}.mp4"
        events = []
        data = {"output": output, "segments": list(segments), **fields}
        result = render_project(data, base_dir=str(self.dir), overwrite=force, on_progress=events.append)
        return result, events, self.dir / output

    def assert_av_aligned(self, path):
        difference = abs(stream_duration(path, "a:0") - stream_duration(path, "v:0"))
        self.assertLess(difference, ONE_FRAME_2997, f"音訊與視訊長度差 {difference:.4f} 秒")

    def test_same_fps_segments_are_frame_exact(self):
        result, events, out = self.render(
            {"id": "x", "clip": "clips/a.mp4", "start_frame": 15, "end_frame": 45},
            {"id": "y", "clip": "clips/空白 與 中文/c.mp4", "start_frame": 10, "end_frame": 30},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame_count"], 50)
        self.assertEqual(frame_count(out), 50)
        self.assertEqual(result["segments"], [
            {"id": "x", "clip": "clips/a.mp4", "start_frame": 15, "end_frame": 45,
             "output_start_frame": 0, "output_end_frame": 30,
             "output_start": 0.0, "output_end": round(30 * ONE_FRAME_2997, 3)},
            {"id": "y", "clip": "clips/空白 與 中文/c.mp4", "start_frame": 10, "end_frame": 30,
             "output_start_frame": 30, "output_end_frame": 50,
             "output_start": round(30 * ONE_FRAME_2997, 3), "output_end": round(50 * ONE_FRAME_2997, 3)},
        ])
        self.assertEqual(result["duration"], round(50 * ONE_FRAME_2997, 3))
        self.assertEqual(result["settings"], {
            "width": 640, "height": 360, "fps": "30000/1001", "crf": 20, "preset": "fast",
        })
        self.assertEqual(video_props(out), (640, 360, "30000/1001"))
        self.assertEqual(audio_codec(out), "aac")
        self.assert_av_aligned(out)
        self.assertEqual([event["event"] for event in events], [
            "segment_start", "segment_done", "segment_start", "segment_done", "concat_start", "done",
        ])
        self.assertEqual(events[0], {"event": "segment_start", "index": 1, "total": 2, "segment_id": "x"})
        self.assertEqual(events[3], {"event": "segment_done", "index": 2, "total": 2, "segment_id": "y"})
        self.assertEqual(events[-1], {"event": "done", "output": f"out/{self._testMethodName}.mp4"})
        self.assertEqual(leftover_work_dirs(out.parent), [])

    def test_first_frame_matches_start_frame(self):
        result, _, out = self.render({"clip": "clips/a.mp4", "start_frame": 45, "end_frame": 60})
        self.assertTrue(result["ok"], result)
        pngs = self.dir / "png" / self._testMethodName
        out_first = frame_png(out, 0, pngs / "out0.png")
        source_45 = frame_png(self.clip_a, 45, pngs / "src45.png")
        source_46 = frame_png(self.clip_a, 46, pngs / "src46.png")
        self.assertGreater(psnr(out_first, source_45), psnr(out_first, source_46) + 10)

    def test_mixed_sizes_fps_and_missing_audio(self):
        result, _, out = self.render(
            {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 30},
            {"clip": "clips/b_noaudio.mp4", "start_frame": 0, "end_frame": 25},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(video_props(out), (640, 360, "30000/1001"))
        second = result["segments"][1]
        self.assertIn(second["output_end_frame"] - second["output_start_frame"], (29, 30, 31))
        self.assertEqual(frame_count(out), result["frame_count"])
        self.assertEqual(audio_codec(out), "aac")
        self.assert_av_aligned(out)

    def test_same_clip_twice(self):
        result, _, out = self.render(
            {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 10},
            {"clip": "clips/a.mp4", "start_frame": 50, "end_frame": 70},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 30)

    def test_single_segment_simple_edit(self):
        result, _, out = self.render({"id": "only", "clip": "clips/a.mp4", "start_frame": 30, "end_frame": 60})
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 30)
        self.assertEqual(
            (result["segments"][0]["output_start_frame"], result["segments"][0]["output_end_frame"]), (0, 30),
        )

    def test_segment_audio_matches_video_length(self):
        captured = {}
        real_run_tool = media.run_tool

        def spy(command):
            if "concat" in command:
                list_path = Path(command[command.index("-i") + 1])
                for segment_file in sorted(list_path.parent.glob("seg_*.mov")):
                    captured[segment_file.name] = (
                        stream_duration(segment_file, "v:0"), stream_duration(segment_file, "a:0"),
                    )
            return real_run_tool(command)

        with mock.patch.object(media, "run_tool", side_effect=spy):
            result, _, out = self.render(
                {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 20},
                {"clip": "clips/b_noaudio.mp4", "start_frame": 0, "end_frame": 25},
                {"clip": "clips/short_audio.mp4", "start_frame": 0, "end_frame": 80},
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(sorted(captured), ["seg_0001.mov", "seg_0002.mov", "seg_0003.mov"])
        for name, (video_seconds, audio_seconds) in captured.items():
            self.assertAlmostEqual(video_seconds, audio_seconds, delta=0.001, msg=name)
        self.assert_av_aligned(out)

    def test_odd_dimension_first_clip(self):
        result, _, out = self.render(
            {"clip": "clips/odd.mkv", "start_frame": 0, "end_frame": 20},
            {"clip": "clips/a.mp4", "start_frame": 0, "end_frame": 10},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["settings"]["width"], result["settings"]["height"]), (852, 480))
        self.assertEqual(video_props(out)[:2], (852, 480))
        self.assertEqual(frame_count(out), 30)

    def test_output_directory_is_created(self):
        result, _, out = self.render(
            {"clip": "clips/a.mp4", "end_frame": 10}, output="out/new/nested/dir/reel.mp4",
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(out.is_file())
        self.assertEqual(leftover_work_dirs(out.parent), [])

    def test_refuses_existing_output_unless_overwrite(self):
        out = self.dir / "out" / "existing.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"old")
        segment = {"clip": "clips/a.mp4", "end_frame": 10}
        result, events, _ = self.render(segment, output="out/existing.mp4")
        self.assertEqual(result["errors"][0]["code"], "output_exists")
        self.assertEqual(events, [])
        self.assertEqual(out.read_bytes(), b"old")
        result, _, _ = self.render(segment, output="out/existing.mp4", force=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 10)
        result, _, _ = self.render({"clip": "clips/a.mp4", "end_frame": 12}, output="out/existing.mp4", overwrite=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(frame_count(out), 12)

    def test_io_error_when_output_parent_is_a_file(self):
        blocker = self.dir / "out" / "blocker"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_bytes(b"")
        result, events, _ = self.render({"clip": "clips/a.mp4", "end_frame": 10}, output="out/blocker/reel.mp4")
        self.assertEqual([(e["code"], e["field"]) for e in result["errors"]], [("io_error", "output")])
        self.assertEqual(exit_code_for(result), 2)
        self.assertEqual(events, [])

    def test_ffmpeg_failure_leaves_no_output(self):
        real_run_tool = media.run_tool

        def failing(command):
            if command[-1].endswith("seg_0002_v.mov"):
                raise media.MediaError("ffmpeg_failed", "模擬失敗", detail="boom")
            return real_run_tool(command)

        with mock.patch.object(media, "run_tool", side_effect=failing):
            result, events, out = self.render(
                {"id": "first", "clip": "clips/a.mp4", "end_frame": 10},
                {"id": "second", "clip": "clips/a.mp4", "start_frame": 10, "end_frame": 20},
            )
        self.assertEqual(result, {"ok": False, "errors": [{
            "code": "ffmpeg_failed", "segment_id": "second", "field": None, "message": "模擬失敗", "detail": "boom",
        }]})
        self.assertEqual(exit_code_for(result), 2)
        self.assertFalse(out.exists())
        self.assertEqual(leftover_work_dirs(out.parent), [])
        self.assertEqual([event["event"] for event in events], ["segment_start", "segment_done", "segment_start"])

    def test_missing_ffmpeg_during_render(self):
        with mock.patch.object(media, "FFMPEG", "ffmpeg-not-installed-xyz"):
            result, _, out = self.render({"clip": "clips/a.mp4", "end_frame": 10})
        self.assertEqual([(e["code"], e["segment_id"]) for e in result["errors"]], [("ffmpeg_not_found", "1")])
        self.assertEqual(exit_code_for(result), 2)
        self.assertFalse(out.exists())
        self.assertEqual(leftover_work_dirs(out.parent), [])
```

- [ ] **Step 2：執行測試，確認失敗**

Run: `python -m unittest highlight_editor.tests.test_render -v`
Expected: ERROR，`ImportError: cannot import name 'render_project' from 'highlight_editor.render'`

- [ ] **Step 3：實作 `render_project`**

在 `highlight_editor/render.py` 的 import 區塊，把 `import os` 換成：

```python
import contextlib
import os
import shutil
import tempfile
```

在檔案最後加入：

```python
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
```

- [ ] **Step 4：更新套件匯出**

把 `highlight_editor/__init__.py` 整個換成：

```python
"""精華剪輯後台：從片段選取影格範圍，並以 ffmpeg 合併成一支影片。

用法見 highlight_editor/README.md；資料格式見 highlight_editor/openapi.yaml。
"""

from .catalog import list_clips
from .project import exit_code_for
from .render import render_project, validate_project

__all__ = ["list_clips", "validate_project", "render_project", "exit_code_for"]
```

- [ ] **Step 5：執行測試，確認通過**

Run: `python -m unittest highlight_editor.tests.test_render -v`
Expected: 全部 PASS（`ValidateProjectTests` 14 個、`PackageExportTests` 1 個、`RenderProjectTests` 12 個）

再跑一次全部測試，確認先前的 task 沒有被影響：

Run: `python -m unittest discover -s highlight_editor/tests -t .`
Expected: `OK`

- [ ] **Step 6：Commit**

```bash
git add highlight_editor/render.py highlight_editor/__init__.py highlight_editor/tests/test_render.py
git commit -F - <<'EOF'
Add two-stage frame-accurate render to highlight_editor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
EOF
```

---

### Task 6：CLI（`__main__.py`）

**Files:**
- Create: `highlight_editor/__main__.py`
- Test: `highlight_editor/tests/test_cli.py`

**Interfaces:**
- Consumes：`list_clips`、`validate_project`、`render_project`、`EditorError`、`error_result`、`exit_code_for`；測試工具 `run_cli`、`make_clip`、`manifest_row`、`write_manifest`、`frame_count`
- Produces：
  - `main(argv: Optional[Sequence[str]] = None) -> int`
  - `build_parser() -> argparse.ArgumentParser`
  - `load_project_json(source: str) -> Tuple[Any, Optional[EditorError]]`
  - 指令：`python -m highlight_editor list --manifest <csv>... [--base-dir DIR]`、`validate <project|-> [--base-dir DIR]`、`render <project|-> [--base-dir DIR] [--overwrite]`。專案檔路徑本身以目前目錄為準，`--base-dir` 只影響專案「內容」裡的路徑。

- [ ] **Step 1：寫失敗的測試**

`highlight_editor/tests/test_cli.py`：

```python
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from highlight_editor import media
from highlight_editor.tests.helpers import (
    FFMPEG_AVAILABLE,
    SKIP_REASON,
    frame_count,
    make_clip,
    manifest_row,
    run_cli,
    write_manifest,
)


def error_codes(result):
    return [(error["code"], error["segment_id"], error["field"]) for error in result["errors"]]


class CliInputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_invalid_json_file(self):
        (self.dir / "p.json").write_text("{not json", encoding="utf-8")
        code, out, _ = run_cli(["validate", "p.json"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(error_codes(out), [("invalid_json", None, None)])
        self.assertIn("第 1 行", out["errors"][0]["message"])

    def test_missing_project_file(self):
        code, out, _ = run_cli(["validate", "missing.json"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(out["errors"][0]["code"], "invalid_json")
        self.assertIn("無法讀取專案檔", out["errors"][0]["message"])

    def test_top_level_must_be_object(self):
        (self.dir / "p.json").write_text("[1]", encoding="utf-8")
        code, out, _ = run_cli(["render", "p.json"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(error_codes(out), [("invalid_json", None, None)])

    def test_validation_errors_are_reported_together(self):
        (self.dir / "p.json").write_text(json.dumps({"output": "x.mov", "segments": []}), encoding="utf-8")
        code, out, _ = run_cli(["validate", "p.json"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(error_codes(out), [("invalid_value", None, "output"), ("invalid_value", None, "segments")])

    def test_missing_manifest(self):
        code, out, _ = run_cli(["list", "--manifest", "nope.csv"], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(error_codes(out), [("manifest_not_found", None, "manifest")])


@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class CliMediaTests(unittest.TestCase):
    clip_rel = "clips/中文 片段.mp4"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        make_clip(cls.dir / cls.clip_rel, size="320x240", seconds=1)
        write_manifest(cls.dir / "manifest.csv", [manifest_row(
            cls.clip_rel, label="positive", event_type="Home Run",
            description="全壘打（測試）", is_official_highlight="True",
        )])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def write_project(self, name, segments, bom=False, **fields):
        data = {"output": f"out/{name}.mp4", "segments": segments, **fields}
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig" if bom else "utf-8")
        return path.name

    def test_list_outputs_utf8_json(self):
        code, out, _ = run_cli(["list", "--manifest", "manifest.csv"], cwd=self.dir)
        self.assertEqual(code, 0, out)
        clip = out["clips"][0]
        self.assertEqual(clip["clip"], self.clip_rel)
        self.assertEqual(clip["description"], "全壘打（測試）")
        self.assertTrue(clip["exists"])

    def test_base_dir_option(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        code, out, _ = run_cli(["list", "--manifest", "manifest.csv", "--base-dir", str(self.dir)], cwd=other.name)
        self.assertEqual(code, 0, out)
        self.assertTrue(out["clips"][0]["exists"])

    def test_validate_project_saved_with_bom(self):
        name = self.write_project("bom", [{"clip": self.clip_rel, "end_frame": 10}], bom=True)
        code, out, _ = run_cli(["validate", name], cwd=self.dir)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["segments"][0]["end_frame"], 10)

    def test_validate_from_stdin(self):
        text = json.dumps({"output": "out/stdin.mp4", "segments": [{"clip": self.clip_rel}]}, ensure_ascii=False)
        code, out, _ = run_cli(["validate", "-"], cwd=self.dir, stdin_text=text)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["segments"][0]["clip"], self.clip_rel)

    def test_render_reports_progress_on_stderr(self):
        name = self.write_project("render", [{"id": "hr", "clip": self.clip_rel, "start_frame": 5, "end_frame": 20}])
        code, out, events = run_cli(["render", name], cwd=self.dir)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["frame_count"], 15)
        self.assertEqual([event["event"] for event in events], ["segment_start", "segment_done", "concat_start", "done"])
        self.assertEqual(frame_count(self.dir / "out" / "render.mp4"), 15)

    def test_render_overwrite_flag(self):
        name = self.write_project("again", [{"clip": self.clip_rel, "end_frame": 5}])
        self.assertEqual(run_cli(["render", name], cwd=self.dir)[0], 0)
        code, out, events = run_cli(["render", name], cwd=self.dir)
        self.assertEqual(code, 1)
        self.assertEqual(out["errors"][0]["code"], "output_exists")
        self.assertEqual(events, [])
        code, out, _ = run_cli(["render", name, "--overwrite"], cwd=self.dir)
        self.assertEqual(code, 0, out)

    def test_missing_ffmpeg_exits_with_2(self):
        from highlight_editor.__main__ import main

        project_path = self.dir / "nf.json"
        project_path.write_text(json.dumps({"output": "out/nf.mp4", "segments": [{"clip": self.clip_rel}]}), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(media, "FFPROBE", "ffprobe-not-installed-xyz"), \
                mock.patch("sys.stdin", io.StringIO("")), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["validate", str(project_path), "--base-dir", str(self.dir)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["errors"][0]["code"], "ffmpeg_not_found")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2：執行測試，確認失敗**

Run: `python -m unittest highlight_editor.tests.test_cli -v`
Expected: FAIL / ERROR。子程序會回報 `No module named highlight_editor.__main__`，`run_cli` 因 stdout 不是 JSON 而丟 `AssertionError: CLI 輸出不是 JSON`。

- [ ] **Step 3：實作 `__main__.py`**

`highlight_editor/__main__.py`：

```python
"""CLI：python -m highlight_editor <list|validate|render>。

stdout 一律是單一 JSON 物件；render 的進度以 JSON Lines 輸出到 stderr。
"""

import argparse
import json
import sys
from typing import Any, Dict, Optional, Sequence, Tuple

from .catalog import list_clips
from .project import EditorError, error_result, exit_code_for
from .render import render_project, validate_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m highlight_editor",
        description="精華剪輯後台：列出片段、驗證專案、渲染合併影片",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="列出 manifest 中可選的片段")
    list_parser.add_argument("--manifest", nargs="+", required=True, help="manifest.csv 路徑，可給多個")

    validate_parser = commands.add_parser("validate", help="驗證專案 JSON，不編碼")
    validate_parser.add_argument("project", help="專案 JSON 路徑；- 代表從 stdin 讀取")

    render_parser = commands.add_parser("render", help="依專案 JSON 渲染合併影片")
    render_parser.add_argument("project", help="專案 JSON 路徑；- 代表從 stdin 讀取")
    render_parser.add_argument("--overwrite", action="store_true", help="允許覆寫已存在的輸出檔")

    for subparser in (list_parser, validate_parser, render_parser):
        subparser.add_argument(
            "--base-dir", default=".", help="專案內容與 manifest 中相對路徑的基準目錄（預設為目前目錄）",
        )
    return parser


def load_project_json(source: str) -> Tuple[Any, Optional[EditorError]]:
    """讀取專案 JSON（檔案或 stdin），接受 UTF-8 BOM。"""
    try:
        if source == "-":
            text = sys.stdin.read()
        else:
            with open(source, encoding="utf-8-sig") as handle:
                text = handle.read()
    except OSError as exc:
        return None, EditorError("invalid_json", f"無法讀取專案檔：{source}（{exc.strerror or exc}）")
    except UnicodeDecodeError:
        return None, EditorError("invalid_json", f"專案檔不是 UTF-8 編碼：{source}")
    try:
        return json.loads(text.lstrip("\ufeff")), None
    except json.JSONDecodeError as exc:
        return None, EditorError(
            "invalid_json", f"專案檔不是合法的 JSON：第 {exc.lineno} 行第 {exc.colno} 欄，{exc.msg}",
        )


def _configure_stdio() -> None:
    """Windows 主控台預設不是 UTF-8；統一改成 UTF-8，讓 UI 端可以直接解析。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def _print_progress(event: Dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "list":
        result = list_clips(args.manifest, base_dir=args.base_dir)
    else:
        data, error = load_project_json(args.project)
        if error is not None:
            result = error_result([error])
        elif args.command == "validate":
            result = validate_project(data, base_dir=args.base_dir)
        else:
            result = render_project(
                data, base_dir=args.base_dir, overwrite=args.overwrite, on_progress=_print_progress,
            )
    print(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return exit_code_for(result)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4：執行測試，確認通過**

Run: `python -m unittest highlight_editor.tests.test_cli -v`
Expected: 全部 PASS（12 個測試）

另外手動看一次 help，確認是繁體中文：

Run: `python -m highlight_editor --help`
Expected: 顯示 `精華剪輯後台：列出片段、驗證專案、渲染合併影片`，以及 `list`、`validate`、`render` 三個指令。

- [ ] **Step 5：Commit**

```bash
git add highlight_editor/__main__.py highlight_editor/tests/test_cli.py
git commit -F - <<'EOF'
Add JSON CLI for highlight_editor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
EOF
```

---

### Task 7：合約測試（實際輸出必須符合 `openapi.yaml`）

**Files:**
- Create: `highlight_editor/tests/schema_check.py`
- Test: `highlight_editor/tests/test_contract.py`

**Interfaces:**
- Consumes：`highlight_editor/openapi.yaml`（既有，不修改）、`list_clips`、`validate_project`、`render_project`、`media`；測試工具
- Produces：
  - `schema_check.yaml`：模組，無法 import PyYAML 時為 None。
  - `schema_check.load_document() -> Dict[str, Any]`
  - `schema_check.SchemaChecker(document)`，方法有：
    - `.check(value, schema: Union[str, Dict]) -> List[str]`：字串代表 `components/schemas` 的名稱。
    - `.unresolved_refs() -> List[str]`
    - `.undeclared_required() -> List[str]`

- [ ] **Step 1：寫驗證器 `schema_check.py`**

`highlight_editor/tests/schema_check.py`：

```python
"""openapi.yaml 用到的 schema 規則的簡易驗證器（只給測試用，不新增依賴）。

支援：$ref、oneOf、allOf、nullable、type、enum、required、properties、items、
minimum／maximum（含 3.0 的布林 exclusiveMinimum）、multipleOf、pattern、minLength、minItems。
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    import yaml
except ImportError:  # pragma: no cover - 沒有 PyYAML 時測試會 skip
    yaml = None

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.yaml"
REF_PREFIX = "#/components/schemas/"


def load_document() -> Dict[str, Any]:
    with OPENAPI_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ValueError(f"不支援的 type：{expected}")


class SchemaChecker:
    def __init__(self, document: Dict[str, Any]):
        self.document = document
        self.schemas = document["components"]["schemas"]

    def resolve(self, ref: str) -> Dict[str, Any]:
        if not ref.startswith(REF_PREFIX) or ref[len(REF_PREFIX):] not in self.schemas:
            raise KeyError(ref)
        return self.schemas[ref[len(REF_PREFIX):]]

    def unresolved_refs(self) -> List[str]:
        problems: List[str] = []

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    targets = [value] if key == "$ref" else list(value.values()) if key == "mapping" else []
                    for target in targets:
                        try:
                            self.resolve(target)
                        except KeyError:
                            problems.append(f"{path}.{key}: {target}")
                    if key not in ("$ref", "mapping"):
                        walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        walk(self.document, "$")
        return problems

    def undeclared_required(self) -> List[str]:
        return [
            f"{name}.{field}"
            for name, schema in self.schemas.items()
            for field in schema.get("required", [])
            if field not in schema.get("properties", {})
        ]

    def check(self, value: Any, schema: Union[str, Dict[str, Any]], path: str = "$") -> List[str]:
        if isinstance(schema, str):
            schema = self.schemas[schema]
        if "$ref" in schema:
            return self.check(value, self.resolve(schema["$ref"]), path)
        if value is None:
            return [] if schema.get("nullable") else [f"{path}: 不可為 null"]
        errors: List[str] = []
        if "oneOf" in schema:
            matches = sum(1 for option in schema["oneOf"] if not self.check(value, option, path))
            if matches != 1:
                errors.append(f"{path}: 符合 {matches} 個 oneOf 分支（應為 1）")
        for option in schema.get("allOf", []):
            errors += self.check(value, option, path)
        expected = schema.get("type")
        if expected and not _type_ok(value, expected):
            return errors + [f"{path}: 應為 {expected}，實際為 {type(value).__name__}"]
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} 不在 enum 中")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema:
                if schema.get("exclusiveMinimum") is True and not value > schema["minimum"]:
                    errors.append(f"{path}: {value} 必須 > {schema['minimum']}")
                elif value < schema["minimum"]:
                    errors.append(f"{path}: {value} 必須 ≥ {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: {value} 必須 ≤ {schema['maximum']}")
            if "multipleOf" in schema and value % schema["multipleOf"]:
                errors.append(f"{path}: {value} 不是 {schema['multipleOf']} 的倍數")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                errors.append(f"{path}: 長度小於 {schema['minLength']}")
            if "pattern" in schema and not re.search(schema["pattern"], value):
                errors.append(f"{path}: {value!r} 不符合 {schema['pattern']}")
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                errors.append(f"{path}: 項目少於 {schema['minItems']}")
            for index, item in enumerate(value):
                errors += self.check(item, schema.get("items", {}), f"{path}[{index}]")
        if isinstance(value, dict):
            for field in schema.get("required", []):
                if field not in value:
                    errors.append(f"{path}: 缺少必填欄位 {field}")
            for field, field_schema in schema.get("properties", {}).items():
                if field in value:
                    errors += self.check(value[field], field_schema, f"{path}.{field}")
        return errors
```

- [ ] **Step 2：寫失敗的測試**

`highlight_editor/tests/test_contract.py`：

```python
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from highlight_editor import list_clips, media, render_project, validate_project
from highlight_editor.tests import schema_check
from highlight_editor.tests.helpers import FFMPEG_AVAILABLE, SKIP_REASON, make_clip, manifest_row, write_manifest

NO_YAML = "需要 PyYAML 才能讀取 openapi.yaml"


@unittest.skipIf(schema_check.yaml is None, NO_YAML)
class OpenApiDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = schema_check.load_document()
        cls.checker = schema_check.SchemaChecker(cls.document)

    def test_document_shape(self):
        self.assertEqual(self.document["openapi"], "3.0.3")
        self.assertEqual(self.document["paths"], {})
        for key in ("title", "version", "description"):
            self.assertTrue(self.document["info"].get(key), key)

    def test_refs_resolve(self):
        self.assertEqual(self.checker.unresolved_refs(), [])

    def test_required_fields_are_declared(self):
        self.assertEqual(self.checker.undeclared_required(), [])

    def test_examples_match_their_schema(self):
        examples = {name: schema["example"] for name, schema in self.checker.schemas.items() if "example" in schema}
        self.assertGreaterEqual(len(examples), 9)
        for name, example in examples.items():
            with self.subTest(schema=name):
                self.assertEqual(self.checker.check(example, name), [])

    def test_checker_rejects_bad_values(self):
        project = self.checker.schemas["Project"]["example"]
        cases = [
            ("Project", {**project, "segments": [{"clip": "a.mp4", "start_frame": -1}]}),
            ("Project", {**project, "segments": [{"clip": "a.mp4", "start_frame": 1.5}]}),
            ("Project", {**project, "settings": {"width": 1281}}),
            ("Project", {**project, "settings": {"fps": 0}}),
            ("Project", {**project, "output": "out/x.mov"}),
            ("Project", {**project, "segments": []}),
            ("ListClipsResponse", {"ok": False, "clips": []}),
            ("ErrorResult", {"ok": False, "errors": [
                {"code": "boom", "segment_id": None, "field": None, "message": "x"}]}),
            ("ProgressEvent", {"event": "paused"}),
        ]
        for name, value in cases:
            with self.subTest(schema=name, value=value):
                self.assertNotEqual(self.checker.check(value, name), [])


@unittest.skipIf(schema_check.yaml is None, NO_YAML)
@unittest.skipUnless(FFMPEG_AVAILABLE, SKIP_REASON)
class RealOutputContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = schema_check.SchemaChecker(schema_check.load_document())
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        make_clip(cls.dir / "clips" / "a.mp4", size="320x240", seconds=1)
        make_clip(cls.dir / "clips" / "b.mp4", size="320x240", rate="25", seconds=1, audio=False)
        write_manifest(cls.dir / "manifest.csv", [
            manifest_row("clips/a.mp4", label="positive", is_official_highlight="True"),
            manifest_row("clips/missing.mp4"),
        ])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def assert_conforms(self, value, schema_name):
        self.assertEqual(self.checker.check(value, schema_name), [], value)

    def test_list_outputs(self):
        self.assert_conforms(list_clips(["manifest.csv"], base_dir=str(self.dir)), "ListClipsResponse")
        self.assert_conforms(list_clips(["nope.csv"], base_dir=str(self.dir)), "ListClipsResponse")

    def test_validate_outputs(self):
        ok = validate_project({"output": "out/v.mp4", "segments": [{"clip": "clips/a.mp4"}]}, base_dir=str(self.dir))
        self.assertTrue(ok["ok"], ok)
        self.assert_conforms(ok, "ValidateResponse")
        failed = validate_project({"output": "out/v.mp4", "segments": [{"clip": "clips/nope.mp4"}]}, base_dir=str(self.dir))
        self.assert_conforms(failed, "ValidateResponse")

    def test_render_outputs_and_progress_events(self):
        events = []
        result = render_project(
            {"output": "out/r.mp4", "segments": [
                {"id": "a", "clip": "clips/a.mp4", "end_frame": 10},
                {"id": "b", "clip": "clips/b.mp4", "end_frame": 10},
            ]},
            base_dir=str(self.dir), on_progress=events.append,
        )
        self.assertTrue(result["ok"], result)
        self.assert_conforms(result, "RenderResponse")
        self.assertEqual(len(events), 6)
        for event in events:
            self.assert_conforms(event, "ProgressEvent")

    def test_failure_outputs(self):
        real_run_tool = media.run_tool

        def failing(command):
            if command[-1].endswith("_v.mov"):
                raise media.MediaError("ffmpeg_failed", "模擬失敗", detail="boom")
            return real_run_tool(command)

        with mock.patch.object(media, "run_tool", side_effect=failing):
            result = render_project({"output": "out/f.mp4", "segments": [{"clip": "clips/a.mp4"}]}, base_dir=str(self.dir))
        self.assertEqual(result["errors"][0]["code"], "ffmpeg_failed")
        self.assert_conforms(result, "RenderResponse")
        with mock.patch.object(media, "FFPROBE", "ffprobe-not-installed-xyz"):
            result = validate_project({"output": "out/f.mp4", "segments": [{"clip": "clips/a.mp4"}]}, base_dir=str(self.dir))
        self.assertEqual(result["errors"][0]["code"], "ffmpeg_not_found")
        self.assert_conforms(result, "ValidateResponse")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3：執行測試，確認結果**

Run: `python -m unittest highlight_editor.tests.test_contract -v`
Expected: 全部 PASS（9 個測試）。這個 task 驗證的是 Task 1–6 已完成的程式，所以應該直接通過，不會先失敗。

如果某個 `RealOutputContractTests` 失敗，代表程式輸出與 `openapi.yaml` 不一致。這時**不要**修改 `openapi.yaml`，而是比對 spec §5，找出是哪一邊偏離。若是程式偏離就修程式，若是 spec 與 `openapi.yaml` 本身矛盾，就停下來回報。

確認這個測試真的有效：暫時把 `render.py` 的 `_render_result` 中 `"frame_count": cursor` 改成 `"frame_count": str(cursor)`，再跑一次。Expected：`test_render_outputs_and_progress_events` FAIL，訊息包含 `$.frame_count: 應為 integer`。確認後**改回原樣**，並執行 `git diff --stat highlight_editor/render.py`，Expected：沒有輸出。

- [ ] **Step 4：執行全部測試**

Run: `python -m unittest discover -s highlight_editor/tests -t .`
Expected: `OK`

- [ ] **Step 5：Commit**

```bash
git add highlight_editor/tests/schema_check.py highlight_editor/tests/test_contract.py
git commit -F - <<'EOF'
Add contract tests keeping highlight_editor outputs in sync with openapi.yaml

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
EOF
```

---

### Task 8：給 UI 同事的 README、範例專案與最終驗證

**Files:**
- Create: `highlight_editor/README.md`
- Create: `highlight_editor/example_project.json`

**Interfaces:**
- Consumes：Task 1–7 的所有功能
- Produces：UI 同事不必讀程式碼就能串接的文件

- [ ] **Step 1：寫範例專案**

`highlight_editor/example_project.json`：

```json
{
  "version": 1,
  "output": "out/reels/823734_top_plays.mp4",
  "overwrite": false,
  "settings": { "width": null, "height": null, "fps": null, "crf": 20, "preset": "fast" },
  "segments": [
    {
      "id": "hr",
      "clip": "sport_hightlight/dataset/full_game/positive/event_0015_2bottom_Home_Run.mp4",
      "start_frame": 120,
      "end_frame": 480
    },
    {
      "id": "k",
      "clip": "sport_hightlight/dataset/full_game/positive/event_0003_1top_Strikeout.mp4",
      "start_frame": 180
    }
  ]
}
```

- [ ] **Step 2：寫 README**

`highlight_editor/README.md`：

````markdown
# highlight_editor：精華剪輯後台

從 `make_clip.py` 切好的片段中選取影格範圍，以 ffmpeg 精準裁切、統一規格後，依序合併成一支 mp4。

- **精華剪輯**：選取多段後依序合併。
- **簡易剪輯**：只有一段的專案，例如裁切一個片段後單獨輸出。

資料格式的完整定義在 [`openapi.yaml`](openapi.yaml)（OpenAPI 3.0.3），可以用 Swagger Editor 開啟。這份文件只描述資料格式，**沒有 HTTP 端點**。

## 需求

- Python 3.8 以上，只用標準庫。
- `ffmpeg` 與 `ffprobe` 在 `PATH` 上：`ffmpeg -version`。
- 在 repo 根目錄執行。`manifest.csv` 裡的 `clip_path` 是相對於 repo 根目錄。

## 兩種呼叫方式

**CLI**：stdout 一律是一個 JSON 物件，以 `ok` 判斷成功或失敗。

```text
python -m highlight_editor list --manifest sport_hightlight/dataset/full_game/manifest.csv
python -m highlight_editor validate highlight_editor/example_project.json
python -m highlight_editor render highlight_editor/example_project.json
```

- 專案檔路徑可以用 `-`，代表從 stdin 讀取 JSON。
- 三個指令都接受 `--base-dir DIR`，作為專案內容與 manifest 中相對路徑的基準，預設是目前目錄。
- `render` 可以加 `--overwrite`，允許覆寫已存在的輸出檔。

**Python**：回傳的 dict 與 CLI stdout 結構相同。

```python
from highlight_editor import list_clips, validate_project, render_project

clips = list_clips(["sport_hightlight/dataset/full_game/manifest.csv"])
result = render_project(project_dict, on_progress=lambda event: print(event))
```

## 專案 JSON

```json
{
  "output": "out/reels/823734_top_plays.mp4",
  "segments": [
    { "id": "hr", "clip": "sport_hightlight/dataset/full_game/positive/event_0015_2bottom_Home_Run.mp4",
      "start_frame": 120, "end_frame": 480 },
    { "id": "k", "clip": "sport_hightlight/dataset/full_game/positive/event_0003_1top_Strikeout.mp4",
      "start_frame": 180 }
  ]
}
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| `output` | 是 | 輸出路徑，副檔名必須是 `.mp4`，不可與任何片段相同 |
| `segments` | 是 | 至少 1 段；陣列順序就是合併順序；同一個片段可以重複出現 |
| `segments[].clip` | 是 | 片段路徑，直接使用 `list` 結果中的 `clip` |
| `segments[].id` | 否 | UI 自己的識別碼，必須唯一；結果與錯誤會原樣帶回。省略時依位置填 `"1"`、`"2"`… |
| `segments[].start_frame` | 否 | 起始影格（包含），預設 0 |
| `segments[].end_frame` | 否 | 結束影格（不包含）；省略或 `null` 代表到最後一格 |
| `overwrite` | 否 | 預設 `false`；輸出檔已存在時必須為 `true` 才會覆寫 |
| `settings.width` / `height` / `fps` | 否 | 預設取第一段；寬高必須是偶數；fps 可以是 `30`、`29.97` 或 `"30000/1001"` |
| `settings.crf` / `preset` | 否 | x264 參數，預設 `20` / `"fast"` |

不認得的欄位會被忽略，UI 可以夾帶自己的資料。

### 影格怎麼算

影格以 **0 起算的半開區間 `[start_frame, end_frame)`** 表示，用該片段自己的 fps 計。

- 一個 600 格的片段，整段是 `0`–`600`。
- 長度等於 `end_frame − start_frame`。
- 前後兩段可以用同一個數字接起來，例如 `0`–`300` 加上 `300`–`600`，不會重複也不會漏格。

可選範圍來自 `list` 結果的 `frame_count`，fps 是同一筆的 `fps`。

## 輸出

### `list`

每個片段除了 manifest 的欄位（`label`、`event_type`、`description`…），還會附上：

- `exists`：檔案是否存在。
- `fps`、`frame_count`、`width`、`height`、`has_audio`：檔案不存在時都是 `null`。

### `validate`

檢查專案，補上實際的 `end_frame` 和輸出規格（`settings`），並給出 `estimated_duration`（秒）。**不編碼，也不建立任何檔案。** 適合在 UI 上即時檢查。

### `render`

結果中每段都有它在輸出影片中的位置，可以直接用來在時間軸上畫章節標記：

```json
{ "id": "hr", "start_frame": 120, "end_frame": 480,
  "output_start_frame": 0, "output_end_frame": 360, "output_start": 0.0, "output_end": 12.012 }
```

- `output_*_frame` 以輸出 fps 計，同樣是半開區間，前後段首尾相接。
- 片段 fps 與輸出 fps 相同時，該段的輸出影格數剛好等於 `end_frame − start_frame`。
- 秒數取到小數第 3 位。

### 進度

`render` 執行時，stderr 每行一個 JSON 事件。Python API 則以 `on_progress(event)` 回呼傳入。

```json
{"event": "segment_start", "index": 1, "total": 2, "segment_id": "hr"}
{"event": "segment_done",  "index": 1, "total": 2, "segment_id": "hr"}
{"event": "concat_start"}
{"event": "done", "output": "out/reels/823734_top_plays.mp4"}
```

進度百分比可以用 `index / total` 計算。

## 錯誤

```json
{ "ok": false, "errors": [
  { "code": "out_of_range", "segment_id": "k", "field": "end_frame", "message": "end_frame（700）超過片段影格數 600" } ] }
```

驗證錯誤會**一次全部回報**。`message` 是給人看的繁體中文；UI 的判斷邏輯請用 `code`。

| `code` | 何時 | exit code |
|---|---|---|
| `invalid_json` | 專案檔讀不到、不是合法 JSON，或頂層不是物件 | 1 |
| `missing_field` | 缺少必填欄位 | 1 |
| `invalid_value` | 型別或值不合法（負數、奇數寬度、重複 id、副檔名不是 .mp4、output 與片段相同…） | 1 |
| `out_of_range` | 影格範圍不符合 `0 ≤ start_frame < end_frame ≤ frame_count` | 1 |
| `clip_not_found` | 片段檔案不存在 | 1 |
| `invalid_media` | 檔案無法解析，或沒有視訊 | 1 |
| `output_exists` | 輸出檔已存在且沒有允許覆寫 | 1 |
| `manifest_not_found` | `list` 指定的 manifest 不存在 | 1 |
| `ffmpeg_not_found` | 找不到 ffmpeg 或 ffprobe | 2 |
| `ffmpeg_failed` | ffmpeg 執行失敗，`detail` 附上 stderr 的最後 20 行 | 2 |
| `io_error` | 建立目錄、寫入檔案失敗 | 2 |

成功時 exit code 為 0。命令列參數錯誤（例如少了必填參數）由 argparse 處理：exit code 2，stderr 印出用法，stdout 沒有 JSON。

## 運作方式

1. **驗證並探測**：每個片段用 ffprobe 取得 fps、影格數、尺寸、有無音軌。
2. **逐段處理**：
   - 先用 `trim` 依影格裁切並統一規格，只編碼視訊，再讀出實際影格數 n。
   - 接著把音訊截成剛好 `n / fps` 秒；片段沒有音軌時補靜音。
   - 中間檔是 .mov（H.264＋PCM）。
3. **串接**：用 concat demuxer 合併，視訊直接複製，音訊只編碼一次 AAC。
4. **完成**：暫存目錄建在輸出檔旁邊，名稱以 `.highlight_editor_` 開頭，結束後一律刪除。失敗時不會產生或覆蓋輸出檔。

## 已知限制

- 音訊切點以 `影格 / fps` 換算，假設片段是固定影格率。`make_clip.py` 的輸出符合這個條件。
- `list` 會對每個存在的片段各跑一次 ffprobe，片段很多時需要數秒。
- 段落之間沒有轉場。

## 測試

```text
python -m unittest discover -s highlight_editor/tests -t .
```

測試會用 ffmpeg 產生合成片段，不需要 LFS 影片或網路。沒有 ffmpeg 時會略過需要影片的測試；沒有 PyYAML 時會略過合約測試。
````

- [ ] **Step 3：最終驗證**

逐一執行，並記錄實際輸出：

1. Run: `python -m unittest discover -s highlight_editor/tests -t . -v`
   Expected：最後一行是 `OK`，而且**沒有** `skipped`，因為這台機器有 ffmpeg 和 PyYAML。記下測試總數。
2. Run: `python -m compileall -q highlight_editor`
   Expected：沒有輸出，exit code 0。
3. Run: `git diff main --stat`
   Expected：只列出 `docs/superpowers/` 與 `highlight_editor/` 底下的檔案。
4. Run: `git status --short`
   Expected：只有 Step 1、2 新增的兩個檔案，沒有測試留下的暫存檔或 `.highlight_editor_*` 目錄。
5. Run: `python -m highlight_editor validate highlight_editor/example_project.json`
   Expected：exit code 1，stdout 的 JSON 有兩個 `clip_not_found`（segment `hr` 與 `k`）。這個 checkout 沒有 full_game 的片段 mp4，所以這是正確結果。這一步確認範例檔格式正確、錯誤訊息是繁體中文。

- [ ] **Step 4：Commit**

```bash
git add highlight_editor/README.md highlight_editor/example_project.json
git commit -F - <<'EOF'
Add highlight_editor README and example project for UI integration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PJpkjtDsnnFN51giYMUAz7
EOF
```
