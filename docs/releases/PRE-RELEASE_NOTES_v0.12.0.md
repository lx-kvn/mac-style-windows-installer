# v0.12.0 — Pre-release Notes

Baseline: `v0.11.0` (2026-08-03) → `v0.12.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

> **Code signing**: This project has applied to the [SignPath Foundation](https://signpath.io/solutions/open-source-community) open-source code signing program to provide trusted, signed installers. Signing is not yet active on this release — this note will be updated once the integration is live.
> **簽章聲明**：本專案已申請加入 [SignPath Foundation](https://signpath.io/solutions/open-source-community) 開源簽章方案，用以提供受信任的已簽章安裝檔。這個版本尚未套用簽章，整合完成後會更新這則說明。

---

## English

This release replaces the old single "run without admin rights" checkbox with a proper three-way install-location choice, makes locked-file recovery during install/uninstall fully interactive instead of a dead end, fixes upgrade detection across mismatched privilege modes (including a real cross-UAC elevation path), and — the bulk of this round — rebuilds how the installer releases a file locked by `explorer.exe`, tracing a real bug from "looks unkillable" all the way down to a third-party antivirus process-protection rule.

### New Features

**Three-way install location (`custom_install_dir`)**

Packaging used to offer a single "no admin required" checkbox: checked = entire app installs to `%LOCALAPPDATA%\Programs`, unchecked = `Program Files`. `config.html` now offers Program Files / user directory / custom path, with an independent "this path needs admin rights" override for a custom path that happens to live somewhere system-level. Also fixed: the CLI's `--no-admin-install` flag was silently never wired through to the final `installer_config.json` — packaging always defaulted to `Program Files` regardless of the flag.

**Interactive recovery when a file is locked during install**

If a file being copied is locked (sharing/lock violation) and Restart Manager can identify the culprit, the installer now shows an interactive "close this program / cancel" dialog and retries automatically instead of only displaying an error and giving up. The "which files land in `%LOCALAPPDATA%`" packaging field became a checkable, collapsible nested file tree (folder checkboxes cascade to their contents) instead of a plain comma-separated text box; a new `list-files` CLI subcommand lets you inspect an `app_dir`'s contents before packaging.

**Fallback "skip detection, force continue" option**

Both install and uninstall process-running/lock detection now have a last-resort bypass, for the rare case where detection itself gets stuck (e.g. a zombie process Task Manager can't even see, that `taskkill` can't touch either) — the uninstaller also gained a "close the app and continue" flow mirroring the installer's.

**Layered file-lock release: close the window first, force-restart the shell only if that isn't enough**

New `explorer_lock_release.py`. When `explorer.exe` holds a lock, the installer used to just `taskkill` the whole shell process — killing the desktop/taskbar and racing against Windows' `AutoRestartShell` auto-revival. Now it first closes only the specific File Explorer *window* browsing the locked path (via `Shell.Application` COM, `.Quit()`) without touching the shell process at all; only if that isn't enough does it fall back to temporarily disabling `AutoRestartShell`, terminating `explorer.exe`, and restoring both afterward (`try/finally`, regardless of the retry's outcome).

### Bug Fixes

- **Cross-hive upgrade detection**: `check_existing_install()` only queried the hive matching this run's `no_admin_install` setting, so an old version installed under a different privilege mode (e.g. previously in `Program Files`/`HKLM`, now repackaged with a user-directory install) was invisible — the "update or not" prompt was silently skipped and the two installs coexisted. Now checks both hives.
- **Cross-UAC elevation for the old uninstaller**: `run_upgrade_uninstall()` used to call the old version's `uninstall.exe` via `subprocess.run()` (`CreateProcess`), which never triggers Windows' manifest-based UAC prompt (only `ShellExecute` does) — if the old install needed admin rights but the new installer runs unprivileged, the old uninstaller silently failed to write/delete under `Program Files`/`HKLM` without raising any error, reporting success while leaving the old version half-removed. Fixed via `ShellExecuteExW` + `"runas"` when elevation is actually required.
- **`taskkill.exe` failing to terminate `explorer.exe` even from an elevated process**: reproduced live — `taskkill` doesn't enable `SeDebugPrivilege` by default, so it gets `ERROR_ACCESS_DENIED` against `explorer.exe` even when the caller itself is running as Administrator (Task Manager succeeds because it enables this privilege itself). Replaced with direct Win32 API calls (`AdjustTokenPrivileges` + `OpenProcess`/`TerminateProcess`).
- **ctypes 64-bit handle truncation**: the Win32 calls above had no `restype`/`argtypes` declared, so `GetCurrentProcess()`'s pointer-sized return value could be mis-marshalled on 64-bit Windows, causing `OpenProcessToken` to fail outright with no Python exception. Fixed by declaring proper `HANDLE`-sized prototypes.
- `close_running_main_exe()` now checks `taskkill`'s actual `returncode` instead of assuming success whenever the call didn't raise.
- Removed a leftover "restart `explorer.exe` after closing it" step in both install and uninstall — reproduced that this call itself pops an unwanted browsing window (proof the shell had already self-restored by the time it ran), not a helpful safety net.
- `restart_explorer_on_update` is now always on internally instead of a packaging-time checkbox — the interactive confirmation before actually closing anything already provides the safety, so the toggle was just extra surface area to understand with no real protective value.

### Known limitation (documented, not a bug in this project)

Even with both fixes above, a third-party antivirus/endpoint-protection product can still legitimately block `TerminateProcess` against `explorer.exe` at the kernel level (`OpenProcess` succeeds, `TerminateProcess` alone returns access-denied) — reproduced live against Huorong Security's "critical process protection" rule for Explorer. This is expected, correct behavior for that class of software (it's exactly the kind of action ransomware attempts), and this project will not try to bypass it. The lock-violation message now hints at checking antivirus settings, and the whole release flow now logs to `%TEMP%\mswi_explorer_lock_debug.log` for anyone chasing a similar case.

### Documentation

- `規格文件.md`: four new sections (§8.31–§8.34) covering the three-way install location, the interactive lock-release/force-continue flow, cross-hive upgrade + UAC elevation, and the full `explorer_lock_release.py` story including the antivirus edge case.
- `使用說明書.md`: updated the packaging field table (install location, nested file tree), the install/uninstall behavior sections, and added a troubleshooting entry for "stuck on 'file in use' even after closing it."
- `CONTEXT.md`: new section naming the `explorer_lock_release.py` design (window-close vs. shell-process-kill distinction).

### Testing

405 tests (up from 292), all passing.

---

## 繁體中文

發布基準：`v0.11.0`（2026-08-03）→ `v0.12.0`。這輪把原本單一的「免系統管理員權限」核取方塊換成正式的安裝位置三選一，讓安裝/解除安裝遇到檔案鎖定時可以互動式處理、不再是死路一條，修掉了跨權限模式的升級偵測（含一條真實的跨 UAC 提權路徑）——這輪的重點是整個重新設計了「檔案被 explorer.exe 鎖住時怎麼釋放」這件事，一路從「看起來砍不掉」追查到「其實是第三方防毒軟體的行程保護規則」。

### 新增功能

**安裝位置三選一（`custom_install_dir`）**

打包時原本只有一個「免系統管理員權限」核取方塊：勾選＝整個應用程式裝到 `%LOCALAPPDATA%\Programs`，不勾＝裝到 `Program Files`。`config.html` 現在提供 Program Files／使用者目錄／自訂路徑三選一，並附一個獨立的「這個路徑需要系統管理員權限」覆寫勾選，給選了自訂路徑、但那個路徑本身在系統層級的情境用。同一輪也修掉一個真實 bug：CLI 的 `--no-admin-install` 旗標原本從未真正接上最終的 `installer_config.json`——不管有沒有帶這個旗標，打包出來的安裝檔一律固定裝到 `Program Files`。

**安裝過程檔案被鎖住時的互動式復原**

複製檔案途中如果遇到 sharing/lock violation，且 Restart Manager 能查到是誰鎖住的，現在會跳出「關閉此程式／取消」的互動彈窗並自動重試，不再只顯示一段錯誤訊息就此打住。「哪些檔案要落地到 `%LOCALAPPDATA%`」這個打包欄位從純文字逗號輸入，改成可勾選、可摺疊的巢狀檔案分支圖（勾資料夾會連動勾選底下所有檔案）；新增 CLI 的 `list-files` 子命令，方便打包前先查詢 `app_dir` 底下有哪些檔案。

**保底「略過偵測，強制繼續」選項**

安裝跟解除安裝的主程式執行中/檔案鎖定偵測，都新增了最後一道保底繞過選項，因應偵測本身卡死的邊緣情況（例如工作管理員都看不到、`taskkill` 也拿它沒轍的殭屍行程）——解除安裝端也新增了對等的「關閉應用程式並繼續」流程。

**分層式檔案鎖定釋放：先關瀏覽視窗，不夠才強制重啟殼層**

新增 `explorer_lock_release.py`。`explorer.exe` 鎖住檔案時，安裝程式原本會直接 `taskkill` 整個殼層行程——砍掉桌面/工作列，還要跟 Windows 的 `AutoRestartShell` 自動復活機制搶時間。現在會先只關閉正在瀏覽鎖定路徑的那個檔案總管**視窗**（透過 `Shell.Application` COM 呼叫 `.Quit()`），完全不動殼層行程本身；只有這樣還不夠時，才會退回「暫時停用 `AutoRestartShell` → 終止 `explorer.exe` → 事後都補做復原（`try/finally`，不管這次重試結果如何）」這條路。

### 錯誤修正

- **跨 hive 的升級偵測**：`check_existing_install()` 原本只查這次 `no_admin_install` 設定算出來的單一 hive，舊版本如果是用不同權限模式裝的（例如舊版本裝在 `Program Files`／`HKLM`，這次改用使用者目錄重新打包），完全查不到，「是否要更新」的提示被悄悄跳過，新舊兩份安裝並存。已修正：兩邊 hive 都查。
- **跨 UAC 呼叫舊版解除安裝程式**：`run_upgrade_uninstall()` 原本用 `subprocess.run()`（`CreateProcess`）呼叫舊版 `uninstall.exe`，這個呼叫方式不會觸發 Windows 的 manifest 自動提權（只有 `ShellExecute` 才會）——如果舊版本需要管理員權限、但這次新安裝檔免權限執行，舊版 `uninstall.exe` 會在寫入 `Program Files`／刪除 `HKLM` 機碼時默默失敗，卻不拋出任何例外，看起來清乾淨了、實際上沒有。已修正：真的需要提權時改用 `ShellExecuteExW` + `"runas"`。
- **`taskkill.exe` 就算在提權行程底下也砍不掉 `explorer.exe`**：實測重現——`taskkill` 預設不會啟用 `SeDebugPrivilege`，對 `explorer.exe` 會回報「存取被拒」，即使呼叫端本身已經是系統管理員身分（工作管理員能砍掉正是因為它自己有啟用這個權限）。已修正：改成直接呼叫 Windows API（`AdjustTokenPrivileges` + `OpenProcess`/`TerminateProcess`）。
- **ctypes 在 64-bit Windows 下的 handle 截斷問題**：上述 API 呼叫原本完全沒宣告 `restype`/`argtypes`，`GetCurrentProcess()` 這類回傳指標大小數值的函式，回傳值可能被錯誤解讀，導致 `OpenProcessToken` 無條件失敗、卻不拋任何 Python 例外。已修正：明確宣告對應 `HANDLE` 大小的型別。
- `close_running_main_exe()` 改成檢查 `taskkill` 真正的 `returncode`，不再是「呼叫沒拋例外就一律當成功」。
- 拿掉安裝/解除安裝端「結束 `explorer.exe` 後主動重啟它」的殘留步驟——實測重現這個呼叫本身會跳出一個非預期的瀏覽視窗（證明呼叫當下殼層其實已經自己復原了），不是真正有用的保險。
- `restart_explorer_on_update` 改成永遠內建開啟，不再是打包時的核取方塊——真正動手關閉任何程式之前，本來就一定會先跳互動確認畫面把關，讓開發者關掉這個偵測反而只是徒增要理解的設定項，沒有實質防護效果。

### 已知限制（如實記錄，不是這個專案的 bug）

即使上述兩個修正都到位，第三方防毒/端點防護軟體仍然可以合理地在核心層攔截針對 `explorer.exe` 的 `TerminateProcess`（`OpenProcess` 成功、但 `TerminateProcess` 本身回報存取被拒）——實測重現案例是火絨安全的「關鍵進程保護」規則。這是這類軟體合理、正確的行為（因為這正是勒索軟體的常見手法），這個專案不會、也不應該嘗試繞過。鎖定訊息裡補上了提示使用者去檢查防毒軟體設定的文字，整個流程也新增了落地到 `%TEMP%\mswi_explorer_lock_debug.log` 的除錯紀錄，方便日後遇到類似情況的人追查。

### 文件

- `規格文件.md`：新增四個小節（§8.31–§8.34），涵蓋安裝位置三選一、互動式鎖定釋放與保底繼續、跨 hive 升級偵測與跨 UAC、以及 `explorer_lock_release.py` 完整故事（含防毒軟體攔截的邊界情況）。
- `使用說明書.md`：更新打包欄位表格（安裝位置、巢狀檔案樹）、安裝/解除安裝行為說明，新增一則「按了關閉此程式仍卡在檔案使用中」的疑難排解項目。
- `CONTEXT.md`：新增 `explorer_lock_release.py` 的設計概念名稱（關窗 vs. 關殼層行程的區別）。

### 測試

405 個測試（從 292 個增加），全數通過。

---

## Full commit list / 完整變更（commit）

```
f144a3f feat: 安裝/解除安裝流程一輪功能強化與修復
66b2fb9 refactor: 新增 install_scope/self_delete/system_entries/packaging_settings 共用深模組
f84b0d0 fix: --no-admin-install 命令列旗標從未真正生效過
13567b4 fix: 修正 GitHub Actions 建置流程在英文語系 runner 上的中文編碼與缺少 pywin32 問題
（加上這次「chore: 發布 v0.12.0」commit：跨 no_admin_install 模式的升級偵測與
跨 UAC 呼叫（_is_current_process_elevated/_run_uninstall_exe_elevated）；新增
explorer_lock_release.py 分層式檔案鎖定釋放（關窗優先、SeDebugPrivilege+
TerminateProcess 取代 taskkill.exe、ctypes 64-bit handle 截斷修正、防毒軟體
攔截的已知限制與除錯紀錄）；installer_core.py/uninstall.py 整合；
規格文件.md/使用說明書.md/CONTEXT.md 文件更新；對應測試新增與調整）
```
