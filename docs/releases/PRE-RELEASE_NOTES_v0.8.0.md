# v0.8.0 — Pre-release Notes

Baseline: `v0.7.0` (2026-08-02) → `v0.8.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

---

## English

This release adds an interactive "close the running app" prompt during install, splits the Builder Tool into GUI and CLI editions sharing one core, and fixes the race-condition-during-update bug reported right before this round started.

### New Features

**Interactive prompt when the target app is already running**

Previously, if the installer detected that `main_exe` was already running, the install would simply fail with an error message telling the user to close it and retry manually. Now the installer shows a dialog with two buttons — "Close the app and continue installing" / "Cancel" — and can force-close the running process (`taskkill /f`) on the user's behalf before retrying the install automatically.

**Builder Tool: GUI and CLI editions, one shared core**

The packaging tool (`gui_config.py`/`InstallerBuilder.exe`) now ships in two forms sharing the exact same validation and build logic:

- A new `packaging_core.py` holds the pywebview-independent core (environment checks, workspace preparation, form validation) extracted verbatim from `gui_config.py`.
- A new `builder_cli.py` is a pure command-line entry point: `init` scaffolds a template JSON config, `pack` builds an installer from that JSON (with individual fields overridable via CLI flags).
- `build_config_tool.py` (the tool that builds the Builder Tool itself) gained a non-interactive `--cli` mode that compiles both the GUI and CLI editions in one call, and a `build_one_exe()` function shared between the interactive Tkinter GUI and `--cli` mode.
- A single-source-of-truth `VERSION` file was added at the repo root.
- Two documentation files now exist side by side: `使用說明書.md` (GUI usage, unchanged) and the new `CLI_USAGE.md` (CLI usage).

**`/released` skill**

A local (not version-controlled) Claude Code skill that automates the release process: checks the working tree is clean, proposes a version bump from Conventional Commits history (with user confirmation), runs the full test suite, builds both Builder Tool editions, packages them into one installer via the Builder Tool's own CLI, drafts bilingual Pre-release Notes, and commits — stopping short of tagging or pushing.

### Bug Fixes

**Update/overwrite install could report success while some files were still mid-copy**

The old uninstaller (`uninstall.py`) used a fire-and-forget self-delete trick (spawn a detached helper process that waits, then deletes itself) that ran concurrently with the new installer's synchronous file copy during an upgrade — occasionally the self-delete helper would still be touching files in the target directory while the new install was writing to it, corrupting the result while the installer still reported "success". Fixed by adding a `--upgrade` flag: when the old uninstaller is invoked as part of an upgrade flow, it now skips scheduling its own self-delete entirely and lets the new installer's own cleanup take over, eliminating the race. (Known limitation: an old, pre-fix `uninstall.exe` won't recognize `--upgrade` and will still exhibit the race on the first migration away from it.)

**`build_one_exe()` wiped the entire `dist/` folder before every build**

Discovered while actually running the new `--cli` dual-build mode for this release: the stale-artifact cleanup step used to `shutil.rmtree("dist")` wholesale before each build, so compiling the CLI edition right after the GUI edition deleted the GUI exe that had just been built. Fixed to only remove the specific target's own leftover artifacts (`dist/{name}.exe`, `build/{name}/`, `{name}.spec`), leaving other already-built outputs in `dist/` untouched.

### Release Process Improvements

Found while actually running the full `/released` flow end-to-end for the first time (no code changes, process/documentation only):

- `build_config_tool.py --cli` requires `--icon` to be passed explicitly to embed a custom icon in the GUI/CLI exes — easy to forget since the flag is optional. `/released` now always passes it.
- Packaging the two Builder Tool exes into one installer (`builder_cli.py pack`) requires the staging `--app-dir` to live outside the repo's own `dist/` folder, since `builder.py`'s own build process clears `dist/` too. `/released` now stages in a temp directory instead.
- The GUI/CLI exe files installed onto a user's machine are now renamed to fixed, version-free names (`mswi-gui.exe` / `mswi-cli.exe`) before being packaged, distinct from the versioned build artifact names in `dist/`. This means the CLI command a user types after installing (`mswi-cli ...`) stays the same across every future version upgrade, instead of changing every release.

### Testing

170 → 172 tests (net +2 after removing two files whose content was migrated into `tests/test_packaging_core.py`, and adding `tests/test_builder_cli.py`, `tests/test_build_config_tool.py`, plus a regression test proving two sequential `build_one_exe()` calls no longer clobber each other's output), all passing.

---

## 繁體中文

發布基準：`v0.7.0`（2026-08-02）→ `v0.8.0`。本輪新增安裝時「偵測到主程式執行中」的互動關閉對話框、把打包工具拆成 GUI／CLI 兩種共用核心的介面，並修正上一輪結束前回報的更新覆蓋安裝競態問題。

### 新增功能

**偵測到主程式執行中時改成互動式對話框**

原本安裝程式偵測到 `main_exe` 正在執行時，會直接安裝失敗、要求使用者自己關閉程式後手動重試。現在改成跳出「關閉程式並繼續安裝」／「取消」兩個按鈕的對話框，可以直接幫使用者強制關閉正在執行的程式（`taskkill /f`），再自動重試安裝，不需要使用者自己切換視窗操作。

**打包工具：GUI／CLI 雙介面，核心共用**

打包工具（`gui_config.py`/`InstallerBuilder.exe`）現在有兩種介面形式，共用完全相同的驗證與編譯邏輯：

- 新增 `packaging_core.py`，把不依賴 pywebview 的核心邏輯（環境檢查、工作目錄準備、表單驗證）從 `gui_config.py` 原封不動搬過去。
- 新增 `builder_cli.py`，純命令列進入點：`init` 產生範本 JSON 設定檔，`pack` 依這份 JSON 打包安裝檔（個別欄位可用 CLI flag 覆蓋）。
- `build_config_tool.py`（打包這個工具自己用的建置腳本）新增非互動的 `--cli` 模式，一次呼叫就能同時編出 GUI 跟 CLI 兩個版本；互動的 Tkinter GUI 跟 `--cli` 模式現在共用同一個 `build_one_exe()` 函式。
- 新增 repo 根目錄的 `VERSION` 檔案，作為版本號的單一真實來源。
- 使用說明文件變成兩份並存：`使用說明書.md`（GUI 用法，維持原樣）跟新增的 `CLI_USAGE.md`（CLI 用法）。

**`/released` skill**

新增一個本機（不進版控）的 Claude Code skill，自動化整個發布流程：確認工作目錄乾淨、依 Conventional Commits 歷史推算版本號（並請使用者確認）、跑完整測試套件、編譯打包工具的 GUI／CLI 兩個版本、用打包工具自己的 CLI 把兩者打包成一份安裝檔、產生雙語 Pre-release Notes 草稿、commit——不含打 tag 跟 push。

### 錯誤修正

**更新覆蓋安裝有時回報成功，但檔案其實沒有 100% 複製完整**

舊版 `uninstall.py` 用「拋出一個獨立的背景程序，等待後自我刪除」的技巧來清掉自己（fire-and-forget self-delete），這個背景程序偶爾會跟更新流程裡新安裝程式的同步檔案複製動作同時對同一個目錄動作，導致複製結果被破壞、但安裝程式仍回報「成功」。修正方式是新增 `--upgrade` 旗標：當舊版解除安裝程式是被更新流程呼叫時，直接跳過排程自我刪除這一步，交給新安裝程式自己的清理邏輯處理，徹底消除這個競態。（已知限制：如果目前安裝的是修正前的舊版 `uninstall.exe`，它不認得 `--upgrade`，第一次從這種舊版遷移時仍可能遇到這個競態。）

**`build_one_exe()` 每次建置前會清空整個 `dist/` 資料夾**

這次實際執行新的 `--cli` 雙 exe 建置模式時發現：舊產物清理邏輯原本是整個 `shutil.rmtree("dist")`，導致編完 GUI 版緊接著編 CLI 版時，把剛編好的 GUI exe 一起刪掉。修正成只清除這次要編的目標自己殘留的產物（`dist/{name}.exe`、`build/{name}/`、`{name}.spec`），不動 `dist/` 底下其他已經編好的產出。

### 發布流程改善

第一次完整實際跑過 `/released` 流程才發現的問題（不涉及程式碼變更，純流程／文件調整）：

- `build_config_tool.py --cli` 要明確帶 `--icon` 才會套用自訂圖示到 GUI/CLI exe，這個旗標是選填，很容易忘記帶。`/released` 現在固定會帶上。
- 把兩顆 Builder Tool exe 打包成一份安裝檔（`builder_cli.py pack`）時，暫存用的 `--app-dir` 不能放在 repo 自己的 `dist/` 底下，因為 `builder.py` 自己的建置流程也會清空 `dist/`。`/released` 現在改成放到系統暫存目錄。
- 安裝到使用者電腦上的 GUI/CLI 執行檔，打包前會先改名成不帶版本號的固定名稱（`mswi-gui.exe`／`mswi-cli.exe`），跟 `dist/` 底下含版本號的建置產物檔名區分開來。這樣使用者裝完後在命令列打的指令（`mswi-cli ...`）往後每次升版都維持不變，不會每次都要跟著改。

### 測試

170 → 172 個測試（拆分後刪除兩個檔案、內容搬進 `tests/test_packaging_core.py`，新增 `tests/test_builder_cli.py`、`tests/test_build_config_tool.py`，並補上一個驗證「連續建置兩顆 exe 不會互相刪掉對方產物」的迴歸測試），全數通過。

---

## Full commit list / 完整變更（commit）

```
5469a2b fix: 修正更新覆蓋安裝回報成功但檔案沒有複製完整的競態問題
7a962c0 feat: 加入 PATH 時可另外指定跟主程式分開的執行檔
488168d feat: 新增打包工具語言切換、安裝介面語言自動偵測與多語言 EULA
38b8d03 docs: 新增 Commit 訊息規範文件
（本次發布另納入尚未提交的變更：process_running 互動對話框、
packaging_core.py/builder_cli.py 拆分、/released skill、
build_one_exe() dist 清理範圍修正 — 詳見 commit 內文）
```
