# Highlight Builder 進度與 highlight_editor 串接說明

給負責 `highlight_editor`（剪輯後台）的同事。說明網頁目前做到哪裡、`docs/api/highlight-editor.openapi.yaml` 在這裡扮演什麼角色，以及接上去需要做的事。

- 分支：`feature/highlight-builder-web`（尚未 commit / 開 PR）
- 更新：2026-10-11（highlight_editor 已合併進來並串接完成；以下第 3、4 節保留當初的串接說明）

## 1. 目前進度

| 項目 | 狀態 | 位置 |
|---|---|---|
| 首頁（視覺方向 01） | 完成 | `web/index.html` |
| 工具頁：選影片、比賽、篩選事件、片段設定（US-1～US-5） | 完成 | `web/app.html`、`web/app.js` |
| 產生並檢查指令（US-6） | 完成，指令由後端產生 | `POST /api/v1/jobs/preview` |
| 在頁面上執行並看進度（US-7） | 完成；含停止、重試、重新整理後重新連線 | `POST /api/v1/jobs`、`GET /api/v1/jobs/{id}/events` |
| 微調片段（US-8）：裁切、刪除、排序 | 完成；編輯只存在頁面狀態，輸出時轉成 `Project` | `web/app.js` |
| 播放片段 | 完成；影片檔存在時可播放、可拖時間軸 | `GET /media/<path>` |
| 輸出最終影片 | **已串接 highlight_editor**：`list` 取得實際 fps、`validate` 檢查、`render` 背景執行並即時顯示進度，完成後直接播放 | `highlight_builder/editor.py`、`POST /api/v1/renders` |
| `make_clip.py` 篩選參數、`--progress-json` | 完成 | `sport_hightlight/make_clip.py` |
| ground truth CSV 新增 `batter`、`pitcher` 欄位 | 完成 | `sport_hightlight/mlb_highlight_groundtruth.py` |
| 單元測試 | 33 個，全數通過 | `tests/` |

### 執行方式

```bash
python3 -m highlight_builder.server      # http://localhost:8787 ，工具頁在 /app
python3 -m unittest discover -s tests -t .
```

- 伺服器只用 Python 標準函式庫。工具腳本會自動用專案的 `.venv` 執行（需要 `requests`、`opencv-python`、`numpy`）。
- **示範模式**：沒有 ffmpeg、或 `video/` 只有 Git LFS 佔位檔時自動啟用。每次執行前會重新檢查，裝好 ffmpeg、`git lfs pull` 完成後不用重啟。
- 整場影片 `video/eltaMax10_Reds_Brewers_0913.mp4` 不在 repo 裡；實際執行目前要選半局影片（`_bottom2nd`、`_top6th`），頁面會依檔名自動帶入局數。

## 2. 架構

```
瀏覽器（web/app.js）
  │  HTTP：docs/api/highlight-builder.openapi.yaml
  ▼
本機伺服器（highlight_builder/server.py，localhost:8787）
  │  子程序：python -m highlight_builder run -
  ▼
highlight_builder CLI（highlight_builder/runner.py）
  │  子程序，一個步驟一個
  ▼
mlb_highlight_groundtruth.py → make_clip.py → mask_flags.py
```

瀏覽器不能直接啟動 Python，所以中間一定要有本機伺服器。伺服器不 import 工具，一律用子程序呼叫 CLI。

## 3. highlight-editor.openapi.yaml 目前的角色

> **更新**：`highlight_editor` 已從 chyan26/eltaProjects#2 合併進來，網頁的「輸出最終影片」會透過 `highlight_builder/editor.py` 以子程序呼叫 `list`／`validate`／`render`，端點見 `docs/api/highlight-builder.openapi.yaml`（`list_edit_clips`、`validate_edit_project`、`create_render`、`get_render`、`list_render_events`、`cancel_render`）。輸出檔只允許寫在 `out/` 底下。以下是串接前的說明。

當初（串接前）repo 裡還沒有這個套件，這份規格用在兩個地方：

1. **子程序慣例**：`highlight_builder` 的 CLI 照這份規格的慣例設計——stdout 只有一個 JSON 結果、stderr 每行一個 JSON 進度事件、exit code `0` 成功／`1` 輸入或驗證錯誤／`2` 工具或檔案系統錯誤、以 `ok` 判斷成功。所以伺服器呼叫兩邊的方式會一樣。
2. **輸出資料格式**：按下「輸出最終影片」時，頁面依規格組出 `Project`（片段依清單順序成為 `segments`，裁切轉成 `start_frame`／`end_frame`），並顯示 `python -m highlight_editor render - --base-dir .`，但不會執行。

規格本身 `paths: {}`、沒有 HTTP 端點，因此要放在我們的伺服器後面，用子程序呼叫，不能讓瀏覽器直接打。

## 4. 串接方式

### 4.1 套件放哪裡

把套件放在 repo 根目錄 `highlight_editor/`，讓下面這行在 repo 根目錄可以執行：

```bash
.venv/bin/python -m highlight_editor list --manifest sport_hightlight/dataset/full_game/manifest.csv
```

伺服器的工作目錄就是 repo 根目錄，`--base-dir .` 即可。

### 4.2 後端要新增的端點（提案）

命名照 `highlight-builder.openapi.yaml` 的慣例（Discord 規格風格，`snake_case` operationId）。

| 方法 | 路徑 | operationId | 對應 highlight_editor |
|---|---|---|---|
| `POST` | `/api/v1/edit/clips` | `list_edit_clips` | `list --manifest <csv> ...` |
| `POST` | `/api/v1/edit/validate` | `validate_edit_project` | `validate -`（stdin 傳 `Project`） |
| `POST` | `/api/v1/renders` | `create_render` | `render -`，以子程序在背景執行 |
| `GET` | `/api/v1/renders/{render_id}` | `get_render` | 讀取狀態 |
| `GET` | `/api/v1/renders/{render_id}/events` | `list_render_events` | server-sent events，轉送 `ProgressEvent` |
| `POST` | `/api/v1/renders/{render_id}/cancel` | `cancel_render` | 終止子程序 |

`render` 的執行、取消與 SSE 推送，可以直接沿用 `highlight_builder/server.py` 的 `JobManager` 與 `stream()`；做法和現在跑 `python -m highlight_builder run -` 一樣，只是換成 `python -m highlight_editor render -`。

### 4.3 資料怎麼接

- **manifest 從哪來**：執行完成後 `JobResponse.outputs.manifest` 就是 `make_clip.py` 寫出的 `manifest.csv` 路徑；`outputs.clips[].path` 與 manifest 的 `clip_path` 相同，都相對於 repo 根目錄，可以直接放進 `Segment.clip`。
- **manifest 多了欄位**：新版 manifest 多了 `batter`、`pitcher` 兩欄。規格寫明「不認得的欄位會被忽略」，請確認 `list` 確實忽略，或者願意的話一併帶回。
- **秒數轉影格**：頁面的裁切是以秒為單位。目前在 `web/app.js` 的 `buildProject()` 用 29.97 fps（`30000/1001`）估算。接上後改成：先呼叫 `list_edit_clips` 拿每段的 `fps` 與 `frame_count`，再用實際 fps 換算，並把裁切上限限制在 `frame_count`。
- **輸出路徑**：目前預設 `out/reels/<game_pk>_<事件>_reel.mp4`。`out/` 已在 `.gitignore`。
- **進度**：`segment_start`／`segment_done` 換算成 `progress: {done: index, total}`，`concat_start` 顯示「正在合併」，`done` 之後以 stdout 的 `RenderResult` 為準。
- **完成後**：`RenderResult.output` 可以直接用 `GET /media/<output>` 播放，但目前 `/media/` 只允許 `sport_hightlight/dataset/` 與 `video/`，要把 `out/reels/` 加進 `highlight_builder/server.py` 的 `MEDIA_DIRS`。`RenderResult.segments[].output_start` 可以拿來在播放器時間軸畫章節。

### 4.4 錯誤代碼對照

highlight_editor 用字串代碼，Highlight Builder API 用數字代碼。伺服器轉換時保留原本的字串，方便除錯：

| highlight_editor `code` | exit | 對應 HTTP / 代碼 | 頁面顯示 |
|---|---|---|---|
| `invalid_json`、`missing_field`、`invalid_value`、`out_of_range` | 1 | 400 · `50035` | 指出哪段、哪個欄位有問題 |
| `clip_not_found`、`manifest_not_found` | 1 | 404 · `40401` | 找不到片段或 manifest |
| `invalid_media` | 1 | 400 · `50035` | 檔案不是可讀的影片 |
| `output_exists` | 1 | 409 · 新代碼 `40901` | 詢問是否覆寫，再以 `overwrite: true` 重送 |
| `ffmpeg_not_found` | 2 | `50002` | 沒有安裝 ffmpeg |
| `ffmpeg_failed`、`io_error` | 2 | `50001` | 顯示 `message`，`detail` 放在「顯示詳細資訊」 |

`Error.segment_id` 直接對到頁面上的片段（`Segment.id` 用清單順序 `"1"`、`"2"`…），可以在那段片段上標示錯誤。

### 4.5 前端要改的地方

- `web/app.js` 的 `buildProject()`：改用 `list` 拿到的實際 fps。
- `web/app.js` 的 `exportVideo()`：從「顯示 JSON」改成先打 `validate_edit_project`、再打 `create_render`，並用 SSE 顯示進度（可沿用現有 `watch()` 的做法）。
- 裁切、刪除、排序目前只存在頁面狀態裡，輸出時才會轉成 `Project`，不需要另外的「儲存」API。

## 5. 想請你確認的事

1. `render` 的 stderr 是否保證每行都是 JSON？目前 runner 會略過不是 `{` 開頭的行。
2. `list` 對不存在的片段會回 `exists: false`。示範模式產生的路徑都不存在，頁面可以據此提示，請確認這個行為會保留。
3. 同一個 `clip` 重複出現在 `segments` 是否已支援（規格寫可以）？之後可能做「重播慢動作」。
4. 是否需要 `--json-progress` 之類的開關，或預設就輸出 JSON Lines？
5. 預估什麼時候可以有第一版？在那之前，US-8 維持示範流程。

## 6. 相關檔案

- `docs/api/highlight-builder.openapi.yaml`：網頁後端 API（OpenAPI 3.1，已驗證）
- `docs/api/highlight-editor.openapi.yaml`：剪輯後台資料格式（原 `Discord OpenAPI.yaml`，依內容改名）
- `highlight_builder/server.py`：HTTP 伺服器、`JobManager`、SSE、`/media/`
- `highlight_builder/runner.py`：子程序執行、進度事件、示範模式
- `highlight_builder/job.py`：請求驗證、參數轉換（拒絕未知工具、未知參數、超出專案的路徑）
- `web/app.js`：前端狀態、`buildProject()`、`exportVideo()`
