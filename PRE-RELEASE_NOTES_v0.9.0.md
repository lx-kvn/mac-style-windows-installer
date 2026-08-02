# v0.9.0 — Pre-release Notes

Baseline: `v0.8.0` (2026-08-02) → `v0.9.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

---

## English

This release adds the ability to install specific files (typically a standalone CLI tool) into a user-writable location instead of the main install directory, so running that tool afterward doesn't require administrator privileges.

### New Features

**`local_appdata_files`: install selected files to `%LOCALAPPDATA%` instead of the main install directory**

The main install directory is often under `Program Files`, which requires administrator privileges to write to — fine for the install/uninstall action itself, but unnecessary friction if the packaged app also ships a standalone command-line tool that gets *run* far more often than it gets installed. This was a real problem hit while packaging a different project with this tool.

A new optional field, `local_appdata_files` (GUI: a text field in `config.html`; CLI: `--local-appdata-files`, comma-separated; JSON: an array), lets you list files — relative to `app_dir` — that should instead be installed to `%LOCALAPPDATA%\Programs\<folder_name>` (the current user's own directory, no elevation needed to write to it). Everything not listed keeps the original behavior and goes into the main install directory. Only the install/uninstall action itself still requires administrator privileges (it writes registry entries, PATH, etc.) — what changes is that running the relocated tool afterward doesn't.

If `path_target_exe` (the file whose directory gets added to PATH — see the CLI section below) is also listed in `local_appdata_files`, PATH automatically points at the `%LOCALAPPDATA%` location instead — no need to configure it twice.

Rollback (on install failure) and uninstall both correctly clean up files in the alternate location and remove that directory once empty, without touching the shared `%LOCALAPPDATA%\Programs\` parent folder (which may hold other applications).

**Known limitation**: if the installer runs elevated under a *different* administrator account (e.g. a UAC prompt where you switch accounts), the resolved `%LOCALAPPDATA%` belongs to that elevated account, not the original user — most UAC flows reuse the same account's elevated token, so this edge case is uncommon but not handled. `doc_icon`/`uninstall.exe`/`installer_config.json` are not eligible for relocation and always stay in the main install directory.

### Documentation

Three follow-up documentation passes after v0.8.0 shipped: a note on how to invoke the CLI tool from CMD using its fixed alias (`mswi-cli`) once installed, and a record of two packaging pitfalls discovered while actually running the `/released` release process end-to-end (no code changes).

### Testing

190 tests (up from 171), all passing — 19 new tests cover path resolution, PATH integration, rollback, uninstall, and CLI/GUI-shared validation for `local_appdata_files`.

---

## 繁體中文

發布基準：`v0.8.0`（2026-08-02）→ `v0.9.0`。本輪新增可以把某幾支檔案（典型情境是獨立的 CLI 工具）改裝到使用者自己可寫入的目錄，而不是主安裝目錄，讓使用者事後執行這支工具不需要系統管理員權限。

### 新增功能

**`local_appdata_files`：指定檔案改裝到 `%LOCALAPPDATA%`，不是主安裝目錄**

主安裝目錄常常在 `Program Files` 底下，寫入需要系統管理員權限——對「安裝/解除安裝」這個動作本身沒問題，但如果打包的應用程式還附了一支獨立的命令列工具，這支工具被「執行」的頻率遠高於「安裝」，也裝在同一個需要提權的目錄底下就是不必要的摩擦。這是在另一個專案用這套工具打包時實測踩到的真實問題。

新增選填欄位 `local_appdata_files`（GUI：`config.html` 新增一個文字欄位；CLI：`--local-appdata-files`，逗號分隔；JSON：陣列），可以列出相對於 `app_dir` 的檔案路徑，這些檔案安裝時會改裝到 `%LOCALAPPDATA%\Programs\<folder_name>`（使用者自己的目錄，寫入不需要系統管理員權限）。沒列出的檔案維持原行為，裝進主安裝目錄。只有「安裝/解除安裝」這個動作本身仍然需要系統管理員權限（要寫登錄表、PATH 等系統層級項目）——改變的是「裝完之後、使用者事後執行這支工具」不需要每次都提權。

如果「加入 PATH 的執行檔」（`path_target_exe`，見下方 CLI 章節）剛好也列在 `local_appdata_files` 裡，PATH 會自動指向這個 `%LOCALAPPDATA%` 位置，不需要重複設定。

安裝失敗時的回滾、以及解除安裝，都會正確清除別位目錄裡的檔案，清空後移除該目錄，不會動到 `%LOCALAPPDATA%\Programs\` 這個共用父目錄（可能還裝著其他應用程式）。

**已知限制**：如果安裝程式是用跟目前登入使用者不同的系統管理員帳號提權執行（例如切換帳號的 UAC 提示），解析出來的 `%LOCALAPPDATA%` 會是那個提權帳號的——多數情境下 UAC 沿用同一個帳號的提權權杖，這個邊界案例不常見，但目前沒有特別處理。`doc_icon`/`uninstall.exe`/`installer_config.json` 不支援改裝到別位目錄，固定留在主安裝目錄。

### 文件

v0.8.0 發布後補了三次文件：安裝後如何用固定別名（`mswi-cli`）在 CMD 呼叫 CLI 工具的說明，以及一份實際跑完整個 `/released` 發布流程才發現的兩個打包陷阱的紀錄（不涉及程式碼變更）。

### 測試

190 個測試（從 171 個增加），全數通過——19 個新測試涵蓋 `local_appdata_files` 的路徑解析、PATH 整合、回滾、解除安裝，以及 CLI/GUI 共用的驗證邏輯。

---

## Full commit list / 完整變更（commit）

```
17f289c docs: 補充安裝後在 CMD 用固定別名 mswi-cli 呼叫的說明
365e418 chore: 補充 v0.8.0 Release Notes 的發布流程改善記錄
a668a0d docs: 補充 /released 實測發現的兩個陷阱與 PATH 固定別名設計
（本次發布另納入尚未提交的變更：新增 local_appdata_files 功能，
涵蓋 installer_core.py、uninstall.py、builder.py、packaging_core.py、
gui_config.py、builder_cli.py、ui/config.html 與對應測試 — 詳見 commit 內文）
```
