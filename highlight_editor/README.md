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
   - 先用 `trim` 依影格裁切並統一規格，只編碼視訊，再讀出實際影格數 n。中間檔不使用 B-frame，避免很短的片段串接後影像時間戳錯亂。
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
