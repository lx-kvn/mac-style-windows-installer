# v0.7.0 — Pre-release Notes

Baseline: `v0.6.0` (2026-07-21) → `v0.7.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

---

## English

This release focuses on the **upgrade/overwrite install flow** (reliability and UX) and a series of **file association** cleanup fixes, plus one round of architecture consolidation.

### New Features

**Upgrade install: three-state version comparison prompt**

When an existing installation of the same app is detected, instead of one orange warning dialog covering both "update available" and "same-or-older" cases, the prompt now has three distinct states based on version comparison:

- **Local copy is older** (the version being installed is newer) → blue "update" icon, message reads "Do you want to update to version x.x.x?"
- **Same version** → unchanged, keeps the original style and wording.
- **Local copy is newer** (the version being installed is older, i.e. a downgrade) → a muted, desaturated red warning icon, explicitly telling the user the version to be installed is older, and letting them decide whether to proceed.

**Packaging option: temporarily close File Explorer during upgrade installs**

New `restart_explorer_on_update` option (a checkbox in the Builder Tool's `config.html`). Some applications register a Windows File Explorer shell extension DLL (e.g. a context-menu handler). As long as `explorer.exe` is alive, it keeps that DLL loaded in memory, so overwriting/deleting it during an upgrade install fails — **this has nothing to do with administrator privileges**; it's simply another process holding a file handle. When this option is enabled, the unattended upgrade flow temporarily kills `explorer.exe` before deleting files and restarts it afterward (whether the deletion succeeded or not); a manual, interactive uninstall (double-clicking the uninstaller) is unaffected. Disabled by default, since it causes a brief screen flicker and closes all open File Explorer windows.

### Improvements

**Old-version removal is now deferred until the user actually triggers the install**

Previously, clicking "Update & Overwrite" on the upgrade-detection dialog would **immediately** and silently delete the old version's files, even though the user hadn't yet dragged the icon into the folder. This is now deferred until the user actually drags the icon and installation begins — if the user clicks the confirm button but then changes their mind and closes the window before dragging, the old version is left completely untouched.

**A recovery safety net for upgrade installs**

Before deleting the old version, the entire old install directory is backed up to a system temp folder. If the user cancels partway through (closes the window), or the new version's install fails, the backup is automatically restored — avoiding a situation where the user ends up with neither the old nor the new version installed. (Known limitation: this recovery only restores *files*; registry entries removed during uninstall — the uninstall registry key, file associations, PATH entry, shortcuts — are not covered.)

**Architecture consolidation**

- File-association registry logic, previously duplicated (and easy to let drift out of sync) between `installer_core.py` and `uninstall.py`, is now consolidated into a shared `file_assoc.py` (`register()`/`unregister()`), used by both call sites.
- `window_drag.py` (window-drag logic, shared by both the Builder Tool and the installer) and `disk_space.py` (disk-space checking, pure functions) were split out as standalone shared modules.
- `gui_config.py`'s form-validation logic was extracted into a pure function, `validate_and_build_pack_data()`, directly unit-testable without spinning up a background thread.
- Added `CONTEXT.md`, a domain glossary documenting concepts established this round (file association, per-user association override, the registry seam).

### Bug Fixes

- **Registry write failures were silently swallowed.** The functions writing file associations, the uninstall registry entry, and the PATH entry used to report failures only via `print()` — but the installer is compiled with `--noconsole`, so that output went nowhere, and the install would report "success" even though the registry write actually failed. These now let the exception propagate to the existing rollback handling.
- **File associations blocked by Windows' `UserChoice` mechanism.** If a file extension had previously had its default app manually or automatically chosen, Windows 8+ remembers that choice and Explorer ignores any newly-written `HKLM` association entirely. Install/uninstall now symmetrically clear `UserChoice`, the `HKCU\Software\Classes\<ext>` override, and `FileExts\<ext>\OpenWithProgids`/`OpenWithList` (which feed the "Choose an app" dialog's suggestion list — a different registry path from the one above, previously missed).
- **File-association checkbox checked with no extension filled in.** Added form validation to prevent silently compiling a broken installer.
- **Upgrade-install backup folder could end up nested inside the very directory it was backing up.** `_backup_existing_install()` used to compute its temp path via `os.environ.get("TEMP", ".")`; under certain elevated-execution contexts the `TEMP` environment variable can be present but empty rather than entirely absent, which resolved to a relative path — landing the backup folder inside the install directory itself, turning `shutil.copytree()` into a copy of a directory into its own subdirectory. Fixed by switching to `tempfile.gettempdir()`, plus an added safeguard that refuses to back up if the computed path still ends up nested under the install path.
- **The "temporarily close Explorer" option worked inconsistently.** The upgrade flow invokes the **old** version's `uninstall.exe`, and whether it closes Explorer used to depend solely on that old version's own (possibly stale) `install_manifest.json` — not the setting the user just chose when repackaging the new version. This made behavior flicker between install attempts depending on whichever manifest happened to be left on disk. Fixed by adding a `--restart-explorer` command-line flag: the new version now explicitly passes its own setting to the old `uninstall.exe`, overriding whatever the old manifest says.

### Known Limitations

- If the currently-installed old version's `uninstall.exe` predates this round of fixes, the `--restart-explorer` flag is meaningless to it (the old binary simply doesn't recognize the argument and ignores it) — the first migration away from such a version may still require the user to manually deal with a locked file. Once the new version is installed, subsequent updates (new version calling new version) will benefit from the fix.
- The upgrade-install recovery mechanism restores files only, not registry entries (see Improvements above).
- Still no code signing, still no multi-language UI (see `規格文件.md` §9 Known Limitations and §10 Backlog).

### Testing

The test count has grown substantially since `v0.6.0`, now at **116 tests**, all passing — covering version comparison, the upgrade backup/restore flow, `_kill_explorer`/`_restart_explorer`/`_should_restart_explorer`, file-association registry operations, disk-space checks, and packaging-form validation. All tests use a fake registry (`tests/_fakes.py`) and temp directories; nothing touches real system state.

---

## 繁體中文

發布基準：`v0.6.0`（2026-07-21）→ `v0.7.0`。本輪聚焦在**覆蓋安裝／更新流程**的體驗與可靠度，以及**檔案關聯**功能的一系列殘留清除修復，並完成一次架構收斂。

### 新增功能

**覆蓋安裝：三態版本比對提示**

偵測到已安裝過同名應用程式時，不再只用同一種橘色警示彈窗講「有更新 / 一樣新或更舊」，改成依版本比對結果分成三種明確樣式：

- **本機是舊版**（這次要裝的版本比較新）→ 藍色更新圖示，文字改成「您是否要更新至 x.x.x 版本？」。
- **版本一致** → 維持原本的樣式與文字。
- **本機版本比較新**（這次要裝的版本比較舊）→ 換成收斂過飽和度／明度的警示紅圖示，明確告知使用者「要安裝的版本比較舊」，讓使用者自己決定是否仍要繼續安裝。

**打包選項：更新覆蓋安裝時可暫時關閉檔案總管**

新增 `restart_explorer_on_update` 選項（打包工具 `config.html` 新增勾選框）。有些應用程式會註冊 Windows 檔案總管殼層擴充功能（Shell Extension DLL，例如右鍵選單擴充），只要 `explorer.exe` 還活著就會把這支 DLL 常駐鎖住，更新覆蓋安裝時覆寫/刪除會失敗——**這跟系統管理員權限完全無關**，是另一個處理程序真的持有檔案控制代碼。勾選這個選項後，無人值守的更新覆蓋流程會在刪除檔案前暫時關閉 `explorer.exe`，刪除完畢後（不論成功與否）自動重啟；一般使用者手動雙擊解除安裝不受影響。預設不啟用，因為會讓使用者畫面短暫閃爍、所有檔案總管視窗關閉。

### 改善

**移除舊版本的時機延後到使用者實際觸發安裝之後**

原本使用者在覆蓋安裝彈窗按下「更新覆蓋安裝」確認鈕，就會**立刻**靜默刪除舊版本檔案，使用者當下都還沒把圖示拖進資料夾。現在延後到使用者實際拖曳圖示、真正觸發安裝之後才執行——如果使用者按完確認鈕、還沒拖曳圖示前反悔關閉視窗，舊版本完全不受影響。

**更新覆蓋安裝的復原安全網**

刪除舊版本前會先把舊安裝資料夾整份備份到系統暫存資料夾；如果使用者在流程跑到一半取消（關閉視窗），或這次新版本安裝失敗，都會自動把備份搬回原位，避免使用者「新版本沒裝上、舊版本也被刪了」兩頭落空。（已知限制：這個復原機制只還原「檔案」，解除安裝時一併移除的登錄表項目——解除安裝登錄、檔案關聯、PATH、捷徑——不在復原範圍內。）

**架構收斂**

- 檔案關聯的登錄表操作從 `installer_core.py`／`uninstall.py` 各自維護一份，收斂成共用的 `file_assoc.py`（`register()`/`unregister()`），兩邊呼叫同一份實作，不再是靠命名慣例對齊的兩份清單。
- 拆出 `window_drag.py`（視窗拖曳邏輯，打包工具與安裝端共用）、`disk_space.py`（磁碟空間檢查，純函式）兩個共用模組。
- `gui_config.py` 的表單驗證邏輯抽成純函式 `validate_and_build_pack_data()`，不需要啟動背景執行緒就能直接單元測試。
- 新增 `CONTEXT.md` 領域詞彙表，記錄「檔案關聯」「使用者關聯覆寫」「registry seam」等這輪確立的概念名稱。

### 錯誤修正

- **登錄表寫入失敗被靜默吞掉**：檔案關聯、解除安裝登錄、PATH 寫入這幾個函式原本失敗時只用 `print()` 回報，但安裝檔是 `--noconsole` 編譯、訊息完全沒有地方顯示，導致登錄表實際上沒寫成功、安裝卻回報「成功」。現在讓例外往外拋，交給既有的回滾機制處理。
- **檔案關聯被 Windows `UserChoice` 機制擋住**：副檔名如果先前被手動或系統選過預設開啟程式，Windows 8+ 會記住這個選擇，Explorer 之後完全無視新寫入的 `HKLM` 關聯。安裝／解除安裝時現在會對稱清除 `UserChoice`、`HKCU\Software\Classes\<ext>` 覆寫，以及 `FileExts\<ext>\OpenWithProgids`／`OpenWithList`（餵給「選取應用程式以開啟」對話框建議清單用，是不同機碼路徑，先前遺漏）。
- **勾選檔案關聯但未填副檔名**：新增表單驗證，避免靜默放行編譯出一個殘缺的安裝檔。
- **更新覆蓋安裝的備份資料夾可能建到自己底下**：`_backup_existing_install()` 原本用 `os.environ.get("TEMP", ".")` 算暫存路徑，某些提權執行的情境下 `TEMP` 環境變數會是空字串而非完全不存在，導致算出相對路徑、備份資料夾被建到安裝目錄自己底下，`shutil.copytree()` 變成對自己複製。改用 `tempfile.gettempdir()`，並加一道保險：算出的路徑如果仍落在安裝目錄底下就直接拒絕備份。
- **「暫時關閉檔案總管」選項時好時壞**：更新覆蓋安裝呼叫的是**舊版本**的 `uninstall.exe`，它是否關閉檔案總管原本只看它自己那份（可能過期的）`install_manifest.json`，跟使用者這次重新打包的新設定是兩回事，導致行為隨每次安裝嘗試留下的 manifest 版本不同而不穩定。新增 `--restart-explorer` 命令列旗標，由新版本明確把這次的設定傳給舊版 `uninstall.exe`，覆蓋掉可能過期的設定。

### 已知限制

- 如果目前安裝的舊版本本身是用**更早、還沒有本輪修復**的 `uninstall.exe`，`--restart-explorer` 這個旗標對它沒有意義（舊版 exe 不認得這個參數，會被忽略）——這種情況下第一次遷移仍可能需要使用者手動處理一次被鎖定的檔案；等新版本安裝完成、往後再次更新時（新版本呼叫新版本）才會是這個修復真正生效的時候。
- 更新覆蓋安裝的復原機制只還原檔案，不涵蓋登錄表項目（見上方「改善」章節）。
- 仍未有數位簽章、多語言介面（詳見 `規格文件.md` §9 已知限制、§10 代辦清單）。

### 測試

測試數自 `v0.6.0` 起大幅增加，目前共 **116 個測試**，全數通過，涵蓋版本比對、更新覆蓋備份/復原流程、`_kill_explorer`/`_restart_explorer`/`_should_restart_explorer`、檔案關聯登錄表操作、磁碟空間檢查、打包設定驗證等。全程使用假登錄表（`tests/_fakes.py`）與暫存目錄，不會動到真實系統狀態。

---

## Full commit list / 完整變更（commit）

```
47d4d14 新增打包選項：更新覆蓋安裝時可暫時關閉檔案總管釋放被鎖定的殼層擴充功能 DLL
bc8c027 覆蓋安裝改成三態版本比對提示，並把移除舊版本延後到拖曳觸發安裝之後
61e7421 Update requirements for InstallerBuilder.exe permissions
cf9586e 架構重構：收斂檔案關聯登錄表邏輯，拆出共用深模組，補齊測試
1a24650 清除檔案關聯的 OpenWithProgids/OpenWithList 殘留，並將 .claude/ 排除出版控
8e7d43c 修正檔案關聯被 Windows UserChoice 機制擋住的問題，並補齊專案測試
4ca0949 修正檔案關聯等登錄表寫入失敗被靜默吞掉的問題
```
