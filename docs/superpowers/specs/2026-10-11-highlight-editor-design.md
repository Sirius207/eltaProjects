# 精華剪輯後台（highlight_editor）設計

日期：2026-10-11
狀態：待審閱

## 1. 背景與目標

`sport_hightlight/make_clip.py` 已經能依 ground truth 把轉播切成一段段事件短片，並寫出 `manifest.csv`。這次要加的是**後台（control）**：使用者在 UI 上選取片段、為每段設定起訖影格，後台用 ffmpeg 把選取的段落依序合併成一支影片。UI 由另一位同事開發，本設計只涵蓋後台，以及雙方之間的 JSON 契約。

- **精華剪輯**：選取多段，各自裁切後依序合併。
- **簡易剪輯**：只有一段的專案，例如裁切一個片段後單獨輸出。

成功標準：UI 送出「選取清單＋每段 start/end 影格」，後台輸出一支可播放的 mp4，切點精準到影格，並以 JSON 回報結果（包括合併後的時間軸）或錯誤。

## 2. 已確認的決定

| 項目 | 決定 |
|---|---|
| UI 串接方式 | 核心函式庫＋JSON 契約＋CLI。UI 可以 import Python，也可以用子程序呼叫 CLI 並讀取 stdout 的 JSON |
| start/end 單位 | 影格數，以該片段自己的 fps 計 |
| 影格定義 | 0 起算的半開區間 `[start_frame, end_frame)`；長度為 `end_frame − start_frame` |
| 裁切範圍 | 只能在片段內（`0 ≤ start_frame < end_frame ≤ frame_count`），不延伸到原始轉播影片 |
| 規格不一致 | 輸出解析度與 fps 預設跟第一段相同，其他段等比例縮放並補黑邊；可在 JSON 覆寫 |
| 轉場 | 不加，直接硬切接 |
| 渲染方式 | 兩階段：先逐段正規化成 .mov 中間檔（H.264＋PCM；每段兩次 ffmpeg 呼叫，讓音訊精準對齊視訊），再用 concat demuxer 串接，音訊只編碼一次 AAC |
| 程式位置 | 新目錄 `highlight_editor/`，不修改任何既有檔案 |
| 給 UI 的格式文件 | `highlight_editor/openapi.yaml`（OpenAPI 3.0.3）。只描述資料格式（`components/schemas`），沒有 HTTP 端點，`paths` 為空 |

## 3. 不在範圍內

- 延伸到片段以外的原始轉播影片。
- 轉場、字幕、片頭片尾、配樂。
- HTTP 服務。需要時可以在本套件外包一層，契約不變。
- UI 本身。
- 修改 `sport_hightlight/`、`target_mask/`、`README.md`、`CLAUDE.md`。
- 平行渲染、中間檔快取。
- 可變影格率（VFR）來源的精確處理。`make_clip.py` 輸出的是固定影格率，見 §8 的限制。

## 4. 架構

只用 Python 標準庫，加上系統 `PATH` 上的 `ffmpeg` 與 `ffprobe`，不新增 Python 依賴。

```text
highlight_editor/
├── __init__.py          # 對外 API：list_clips / validate_project / render_project
├── __main__.py          # CLI：python -m highlight_editor <list|validate|render>
├── project.py           # 專案 JSON 的資料模型與結構驗證（純函式，不呼叫 ffmpeg）
├── media.py             # ffprobe 探測、ffmpeg 指令組裝與執行
├── render.py            # 驗證＋探測＋兩階段渲染、進度回呼、結果組裝
├── catalog.py           # 讀 manifest.csv，列出可選片段
├── README.md            # 給 UI 同事的契約說明（繁體中文）
├── openapi.yaml         # §5 的資料格式，以 OpenAPI 3.0.3 schema 表示（給 UI 同事用 Swagger 檢視）
├── example_project.json
└── tests/
    ├── test_project.py  # 純邏輯
    ├── test_media.py    # 指令組裝（不執行 ffmpeg）
    ├── test_render.py   # 整合測試：用 lavfi 產生合成片段；沒有 ffmpeg 時略過
    └── test_contract.py # 實際輸出必須符合 openapi.yaml；沒有 PyYAML 時略過
```

各單元的職責：

| 單元 | 做什麼 | 依賴 |
|---|---|---|
| `project.py` | 把專案 dict 解析成 dataclass（`Project`、`Segment`、`OutputSettings`），收集所有結構與型別錯誤 | 無 |
| `media.py` | `probe(path) -> MediaInfo`；組出正規化與串接的 ffmpeg 參數列；執行 ffmpeg 並把失敗包裝成錯誤 | ffmpeg、ffprobe |
| `catalog.py` | 讀一或多個 manifest，合併成片段清單，並對存在的檔案做探測 | `media.py` |
| `render.py` | `validate_project`、`render_project` 的流程 | 以上全部 |
| `__main__.py` | 參數解析、讀 JSON（檔案或 stdin）、輸出 JSON、設定 exit code | `__init__` |

`list_clips`、`validate_project`、`render_project` 一律回傳與 CLI stdout 相同結構的 dict（見 §5）。驗證或渲染失敗不丟例外，只在程式錯誤時才會有例外。

## 5. JSON 契約

所有路徑都可以是相對或絕對路徑。相對路徑以 `base_dir` 為基準：CLI 用 `--base-dir`，預設是目前目錄；API 用 `base_dir` 參數。`manifest.csv` 的 `clip_path` 是相對於 repo 根目錄，所以在 repo 根目錄執行時可以直接沿用。回傳結果中的路徑會照輸入原樣帶回。

### 5.1 專案檔（UI → 後台）

```json
{
  "version": 1,
  "output": "out/reels/823734_top_plays.mp4",
  "overwrite": false,
  "settings": { "width": null, "height": null, "fps": null, "crf": 20, "preset": "fast" },
  "segments": [
    { "id": "hr", "clip": "sport_hightlight/dataset/full_game/positive/event_0015_2bottom_Home_Run.mp4",
      "start_frame": 120, "end_frame": 480 },
    { "id": "k", "clip": "sport_hightlight/dataset/full_game/positive/event_0003_1top_Strikeout.mp4",
      "start_frame": 180 }
  ]
}
```

| 欄位 | 必填 | 規則 |
|---|---|---|
| `version` | 否 | 預設 1；其他值會報錯 |
| `output` | 是 | 非空字串，副檔名必須是 `.mp4`（不分大小寫），不可與任何 `clip` 指向同一個檔案。`render` 時若上層目錄不存在會自動建立；`validate` 不建立任何東西 |
| `overwrite` | 否 | 布林，預設 `false`。輸出檔已存在且不是 `true` 就拒絕。CLI 的 `--overwrite` 會強制為 `true` |
| `settings.width` / `height` | 否 | `null`（預設）表示取第一段；有指定時必須是正偶數。從第一段取得的奇數值會向下修成偶數 |
| `settings.fps` | 否 | `null`（預設）表示取第一段的 fps。可以是正數（`30`、`29.97`），或能用 `fractions.Fraction` 解析的字串（`"30"`、`"29.97"`、`"30000/1001"`）；必須大於 0，布林值會被拒絕 |
| `settings.crf` | 否 | 0–51 的整數，預設 20 |
| `settings.preset` | 否 | x264 preset 名稱（`ultrafast` … `veryslow`），預設 `"fast"` |
| `segments` | 是 | 至少 1 段。陣列順序就是合併順序，同一個 `clip` 可以重複出現 |
| `segments[].id` | 否 | 字串，必須唯一。省略時依位置填 `"1"`、`"2"`…；如果跟使用者自填的 id 衝突就報錯 |
| `segments[].clip` | 是 | 片段路徑 |
| `segments[].start_frame` | 否 | 預設 0。必須是 ≥ 0 的整數；`120.0` 這種整數值的浮點數可以接受，`120.5` 會報錯 |
| `segments[].end_frame` | 否 | `null` 或省略表示片段的 `frame_count`。必須是整數，且 `start_frame < end_frame ≤ frame_count` |

未知的欄位會忽略，不報錯，讓 UI 可以夾帶自己的資料。

### 5.2 `list`：列出可選片段

`python -m highlight_editor list --manifest <csv> [<csv> ...] [--base-dir DIR]`

```json
{
  "ok": true,
  "clips": [
    {
      "clip": "sport_hightlight/dataset/full_game/positive/event_0015_2bottom_Home_Run.mp4",
      "manifest": "sport_hightlight/dataset/full_game/manifest.csv",
      "label": "positive", "game_pk": "823734", "inning": "2", "half": "bottom",
      "event_type": "Home Run", "description": "...",
      "is_official_highlight": true, "official_highlight_title": "...",
      "duration": 20.0,
      "exists": true, "fps": "30000/1001", "frame_count": 600, "width": 1280, "height": 720, "has_audio": true
    }
  ]
}
```

- 順序依 manifest 的列順序；給多個 manifest 時依參數順序串接。
- manifest 以 UTF-8 讀取，可接受 BOM。缺少的欄位（例如舊 bottom2 manifest 沒有 `game_pk` 的值）回傳空字串。
- `duration` 來自 manifest 的 `clip_duration_seconds`，無法解析時是 `null`。
- `exists: false` 時，`fps`、`frame_count`、`width`、`height`、`has_audio` 都是 `null`。

### 5.3 `validate`：檢查專案（不編碼）

`python -m highlight_editor validate <project.json | -> [--base-dir DIR]`，其中 `-` 代表從 stdin 讀取。

```json
{
  "ok": true,
  "output": "out/reels/823734_top_plays.mp4",
  "settings": { "width": 1280, "height": 720, "fps": "30000/1001", "crf": 20, "preset": "fast" },
  "estimated_duration": 26.026,
  "segments": [
    { "id": "hr", "clip": "...", "start_frame": 120, "end_frame": 480, "fps": "30000/1001", "frame_count": 600 },
    { "id": "k",  "clip": "...", "start_frame": 180, "end_frame": 600, "fps": "30000/1001", "frame_count": 600 }
  ]
}
```

- `end_frame` 會補上實際值。
- `settings` 是決定後的輸出規格。`fps` 一律以字串表示：指定 `30` 時是 `"30"`，指定 `29.97` 時是 `"29.97"`。
- `estimated_duration` 是各段 `(end_frame − start_frame) / 片段 fps` 的總和，單位秒，取到小數第 3 位。

### 5.4 `render`：渲染

`python -m highlight_editor render <project.json | -> [--base-dir DIR] [--overwrite]`

```json
{
  "ok": true,
  "output": "out/reels/823734_top_plays.mp4",
  "settings": { "width": 1280, "height": 720, "fps": "30000/1001", "crf": 20, "preset": "fast" },
  "duration": 26.026,
  "frame_count": 780,
  "segments": [
    { "id": "hr", "clip": "...", "start_frame": 120, "end_frame": 480,
      "output_start_frame": 0,   "output_end_frame": 360, "output_start": 0.0,    "output_end": 12.012 },
    { "id": "k",  "clip": "...", "start_frame": 180, "end_frame": 600,
      "output_start_frame": 360, "output_end_frame": 780, "output_start": 12.012, "output_end": 26.026 }
  ]
}
```

- `output_*_frame` 以輸出 fps 計，同樣是半開區間，前後段首尾相接。數值來自每個中間檔實際的 `nb_frames` 累加，不是預估。
- `output_start` / `output_end` 是 `output_*_frame / 輸出 fps`，單位秒，取到小數第 3 位。
- `duration = frame_count / 輸出 fps`。
- 片段 fps 與輸出 fps 相同時，該段的輸出影格數**剛好等於** `end_frame − start_frame`。不同時會重新取樣，影格數以實際編碼結果為準；例如 25 fps 的 50 格（2.0 秒）轉成 29.97 fps 約為 59–60 格。
- 秒數一律用 Python `round(x, 3)` 取到小數第 3 位。

### 5.5 進度（只有 `render`）

CLI 執行時，每個事件在 **stderr** 輸出一行 JSON，stdout 只放最後的結果。Python API 則改用 `on_progress(event: dict)` 回呼。

```json
{"event": "segment_start", "index": 1, "total": 2, "segment_id": "hr"}
{"event": "segment_done",  "index": 1, "total": 2, "segment_id": "hr"}
{"event": "concat_start"}
{"event": "done", "output": "out/reels/823734_top_plays.mp4"}
```

`index` 從 1 起算。

### 5.6 錯誤

```json
{
  "ok": false,
  "errors": [
    { "code": "out_of_range", "segment_id": "k", "field": "end_frame",
      "message": "end_frame（700）超過片段影格數 600" }
  ]
}
```

| `code` | 何時 | exit code |
|---|---|---|
| `invalid_json` | 專案檔不是合法的 JSON，或頂層不是物件 | 1 |
| `missing_field` | 缺少必填欄位 | 1 |
| `invalid_value` | 型別或值不合法（例如負數、奇數寬度、不支援的 preset、重複 id、副檔名不是 .mp4） | 1 |
| `out_of_range` | 影格範圍不符合 `0 ≤ start_frame < end_frame ≤ frame_count` | 1 |
| `clip_not_found` | 片段檔案不存在 | 1 |
| `invalid_media` | ffprobe 讀不到檔案，或檔案沒有視訊串流 | 1 |
| `output_exists` | 輸出檔已存在，而且沒有允許覆寫 | 1 |
| `manifest_not_found` | `list` 指定的 manifest 不存在 | 1 |
| `ffmpeg_not_found` | `PATH` 上找不到 ffmpeg 或 ffprobe | 2 |
| `ffmpeg_failed` | ffmpeg 執行失敗；多一個 `detail` 欄位，內容是 stderr 的最後 20 行 | 2 |
| `io_error` | 建立目錄、搬移檔案等檔案系統錯誤 | 2 |

- `segment_id` 和 `field` 不適用時是 `null`。`message` 一律用繁體中文。
- 驗證會**一次收集所有錯誤**後回傳，不在第一個錯誤就停下。結構錯誤和探測錯誤都會收集。只有結構錯誤讓某段無法探測時，才略過該段的探測。
- 成功時 exit code 為 0。

CLI 的 stdout 一律是 UTF-8、`ensure_ascii=False` 的單一 JSON 物件。Windows 主控台預設不是 UTF-8，所以啟動時要把 stdout、stderr 重設成 UTF-8。

## 6. 渲染流程

1. **結構驗證**（`project.py`，純函式，不碰檔案系統）：§5.1 的所有規則。
2. **檔案檢查與探測**（`render.py` 呼叫 `media.py`）：片段是否存在、輸出檔是否已存在並允許覆寫，然後對每個不重複的片段跑一次 ffprobe。
   - fps：使用視訊串流的 `avg_frame_rate`；如果是 `0/0` 或缺少，改用 `r_frame_rate`。保留成分數（`fractions.Fraction`）。
   - frame_count：使用 `nb_frames`；如果沒有或是 0，改用 `-count_frames` 解碼計數。
   - 同時取得寬、高、視訊的 `start_time`（缺少時當作 0），以及有沒有音訊串流。
   - 接著檢查影格範圍，補上 `end_frame`。
3. **決定輸出規格**：寬、高、fps 有指定就用指定值，否則取第一段。
4. **逐段正規化**：先建立輸出檔的上層目錄（不存在時），再在裡面建立暫存目錄 `tempfile.mkdtemp(prefix=".highlight_editor_")`。第 i 段分兩次呼叫 ffmpeg，最後產生 `seg_{i:04d}.mov`：

   **4a. 視訊**：輸出 `seg_{i:04d}_v.mov`，再讀它的 `nb_frames`，得到這一段實際的輸出影格數 `n`。

   ```text
   ffmpeg -hide_banner -loglevel error -y -i <clip> -map 0:v:0 -an
     -vf trim=start_frame=S:end_frame=E,setpts=PTS-STARTPTS,
         scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2,
         setsar=1,fps=FPS,format=yuv420p
     -c:v libx264 -crf CRF -preset PRESET -bf 0 seg_0001_v.mov
   ```

   **4b. 音訊與封裝**：視訊直接複製，音訊切成剛好 `D = n / 輸出fps` 秒。

   ```text
   # 片段有音軌
   ffmpeg -hide_banner -loglevel error -y -i seg_0001_v.mov -i <clip>
     -map 0:v:0 -map 1:a:0 -c:v copy
     -af atrim=start=A0:end=A1,asetpts=PTS-STARTPTS,aresample=48000,apad
     -ac 2 -t D -c:a pcm_s16le seg_0001.mov
   # 片段沒有音軌：第二個輸入改成 -f lavfi -i anullsrc=r=48000:cl=stereo，不加 -af
   ```

   說明：
   - `A0 = 視訊 start_time + S / 片段fps`，`A1 = 視訊 start_time + E / 片段fps`。`atrim` 依時間戳切，而第 S 格的時間戳是視訊起點加上 `S / fps`，所以要加上 `start_time`。
   - 視訊的 `trim` 依實際解碼的影格計數，所以張數精準。片段 fps 與輸出 fps 不同時，重新取樣後的張數無法事先準確預測，所以先編碼再讀 `n`。
   - 中間檔使用 `-bf 0`（不用 B-frame）。實測只有 1–3 格的片段在 concat 串接後，影像時間戳會互相重疊，最終影像軌比音訊短（例如 63 格只有 2.033 秒）；關掉 B-frame 後時間戳單調，影像與音訊等長。
   - 音訊先用 `apad` 補長，再以輸出端 `-t D` 精準截斷。PCM 可以做到取樣點精準，所以每段的音訊與視訊長度完全一致，段與段之間不會累積偏移。單一指令配 `-shortest` 的做法實測無法精準截斷（音訊 2.000 秒對上視訊 1.969 秒），因此不採用。
   - 不用 `-ss` 輸入端快轉，而是從片段開頭解碼。片段只有約 20 秒，成本很低，換來的是影格計數絕對準確。
   - `seg_{i:04d}_v.mov` 用完即刪。
   - 每段開始與完成時各發一次進度事件。
5. **串接**：在暫存目錄寫 `list.txt`，每行是 `file 'seg_0001.mov'`。因為只用自己產生的檔名，所以不需要跳脫；相對路徑以清單檔所在目錄為準。接著執行：

   ```text
   ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i list.txt
     -c:v copy -c:a aac -b:a 192k -movflags +faststart <暫存目錄>/output.mp4
   ```

   視訊直接複製，音訊只在這一步編碼一次 AAC，避免每段 AAC 接縫產生空隙。
6. **完成**：用 `os.replace` 把暫存的 `output.mp4` 換成正式輸出。暫存目錄與輸出檔在同一個資料夾，所以這是原子操作。各中間檔的 `nb_frames` 用來計算 §5.4 的時間軸。
7. **清理**：不論成功或失敗，都在 `finally` 刪除暫存目錄。失敗時不會產生、也不會覆蓋正式輸出檔。

`validate` 只做 1–3。

## 7. 測試

使用 stdlib `unittest`，在 repo 根目錄執行：

```text
python -m unittest discover -s highlight_editor/tests -t .
```

**純邏輯（`test_project.py`、`test_media.py`）**

- 影格邊界：`start = end`、`start > end`、負數、`end > frame_count`、`end = frame_count`、`null` 與省略的預設值、`120.0` 可接受、`120.5` 拒絕、布林值拒絕。
- id：預設填值、重複 id、自填 id 與預設值衝突。
- 錯誤一次全部收集：同一個專案裡有多個錯誤時全部回報。
- settings：預設值、奇數寬高、從第一段取得時的偶數修正、fps 數字與分數字串、crf 範圍、preset 名稱。
- 輸出副檔名、`version`。
- ffmpeg 參數列組裝：有音軌與無音軌、`A0`/`A1` 的計算（含非 0 的 `start_time`）。
- 時間軸累加。

**整合（`test_render.py`；找不到 ffmpeg 時 `skipTest`）**

測試會在暫存目錄用 lavfi 產生合成片段：1280×720 @ 30000/1001 有音軌、640×480 @ 25 無音軌，以及路徑含空白與中文的片段。再加上一份假 manifest，然後驗證：

- 同 fps 的多段渲染：總影格數 = 各段 `end − start` 的總和；`output_*_frame` 首尾相接；輸出規格跟第一段相同；有 AAC 音軌；音訊長度與視訊長度差距在一格以內。
- 切點正確：輸出第 0 格與原片第 `start_frame` 格的 PSNR，明顯高於與 `start_frame + 1` 格的 PSNR。
- 混合解析度與 fps，加上沒有音軌的片段：可以完成，規格統一，有靜音音軌。
- 同一個片段重複出現。
- 只有一段的專案（簡易剪輯）。
- 每段中間檔的音訊長度等於視訊長度（`n / 輸出fps`），誤差在 1 毫秒以內。
- 輸出已存在時拒絕，有 `overwrite` 時可以覆寫；`output` 與某個 `clip` 相同時回傳 `invalid_value`。
- 內容是亂碼的假 .mp4：回傳 `invalid_media`，不執行任何編碼。
- 用 mock 讓第 2 段的 ffmpeg 失敗：回傳 `ffmpeg_failed`，`segment_id` 是第 2 段的 id，沒有輸出檔，暫存目錄已刪除。
- 用 mock 模擬找不到 ffmpeg：回傳 `ffmpeg_not_found`，CLI exit code 為 2。
- CLI：`list`、`validate`、`render` 的 stdout 是可解析的 JSON，exit code 正確，`render` 的 stderr 每行都是 JSON；從 stdin 讀取專案（`-`）也可以。

**合約（`test_contract.py`；無法 import `yaml` 時 `skipTest`）**

- `openapi.yaml` 的每個 `$ref` 都能解析，每個 `example` 都符合自己的 schema。
- 整合測試實際產生的 `list`、`validate`、`render` 輸出、錯誤結果與進度事件，都必須符合 `openapi.yaml` 中對應的 schema（`ListClipsResponse`、`ValidateResponse`、`RenderResponse`、`ProgressEvent`）。驗證器是測試內附的簡易版，只涵蓋 `openapi.yaml` 用到的規則（type、nullable、enum、required、properties、items、oneOf、minimum/maximum、multipleOf、pattern、minLength、minItems），不新增依賴。
- 目的：程式改了輸出格式卻忘記更新 `openapi.yaml` 時，測試會失敗。

這些測試不需要 LFS 影片或網路。

## 8. 已知限制

- 音訊切點以 `影格 / fps` 換算，前提是片段為固定影格率（CFR）。`make_clip.py` 的輸出是 CFR；VFR 來源的音訊可能有小幅偏差，但視訊影格仍然精準。
- `list` 對每個存在的片段各跑一次 ffprobe，72 個片段約需數秒，依序執行。
- 片段之間沒有轉場；每段音訊會在接縫處直接切換。

## 9. 驗收標準

1. §7 的所有測試在這台機器上（ffmpeg 8.0.1）通過。
2. 用合成片段跑 `render`，產生的 mp4 可以播放，影格數與 JSON 回報一致。
3. `git diff main --stat` 只包含 `highlight_editor/` 與這份 spec，不動到任何既有檔案。
4. `highlight_editor/README.md` 讓 UI 同事不讀程式碼就能串接：契約、三個指令、錯誤代碼、範例，並指向 `openapi.yaml`。
5. `highlight_editor/openapi.yaml` 與 §5 一致，並通過 `test_contract.py`。
