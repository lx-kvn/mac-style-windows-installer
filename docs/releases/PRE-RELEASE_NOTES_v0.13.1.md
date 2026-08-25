# v0.13.1 — Pre-release Notes

Baseline: `v0.13.0` (2026-08-11) → `v0.13.1`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

> **Code signing**: This project has applied to the [SignPath Foundation](https://signpath.io/solutions/open-source-community) open-source code signing program to provide trusted, signed installers. Signing is not yet active on this release — this note will be updated once the integration is live.
> **簽章聲明**：本專案已申請加入 [SignPath Foundation](https://signpath.io/solutions/open-source-community) 開源簽章方案，用以提供受信任的已簽章安裝檔。這個版本尚未套用簽章，整合完成後會更新這則說明。

---

## English

This is a bug-fix-only release following a full architecture/security audit of the install/uninstall pipeline added in v0.13.0 (Windows service/scheduled task/BITS/restore point/dependency downloads). Every fix here was written test-first (a failing test reproducing the real defect, confirmed red, then the minimal fix). It also carries forward one small GUI gap fix that landed on `main` just before the audit. Architecture-level improvements identified during the same audit (config-schema single source of truth, an explicit js_api contract check, an install-journal abstraction for rollback) are intentionally **not** included in this release — they're committed separately and held for the next one, per an accompanying ADR.

### New Features

**GUI form fields for four previously CLI/JSON-only options**

`create_restore_point_before_install`, `dependencies_min_version`, `windows_service`, and `scheduled_task` were already wired through the backend in v0.13.0 but had no corresponding fields in `ui/config.html` — GUI users had no way to set them. Added the form fields, bilingual (EN/zh-TW) labels, and the `submitForm()` data wiring.

### Bug Fixes

**Security**

- **Unquoted service path (CWE-428)**: the executable path passed to `sc create ... binPath=` wasn't quote-wrapped, so Service Control Manager could resolve a space-containing path (e.g. `C:\Program Files\MyApp\app.exe`) to an attacker-plantable prefix (`C:\Program.exe`) at service start. Fixed by embedding literal quote characters into the value itself.
- **Elevated-execution trust chain via a user-writable registry key**: the upgrade-uninstall path's cross-hive lookup (added to fix a legitimate detection bug) could let an elevated installer process execute an attacker-planted `uninstall.exe` referenced from an HKCU key an unprivileged user can write. Now refuses to run an HKCU-sourced uninstaller when the current process is elevated.
- **Missing integrity/transport verification for downloaded dependencies**: `custom_dependencies` download URLs are now enforced to be `https://`-only, and an optional `sha256` field lets `install_dependency()` verify the downloaded installer before executing it.

**Correctness**

- Fixed the `BITS` job-state constants (`BG_JOB_STATE`), which didn't match the real Microsoft enum, and added a timeout so a stuck BITS job can't hang the installer forever.
- A partially-written uninstall-registry key (interrupted mid-`SetValueEx` sequence) is now cleaned up instead of left as an orphaned registry entry.
- `file_assoc.py` now consistently resolves the correct hive (HKCU vs HKLM) via `InstallScope` under `no_admin_install`, instead of hardcoding HKLM.
- `system_entries.remove_registry_entry()` now falls back across both hives, matching the existing `check_existing_install()` pattern.
- Restart Manager's graceful-release path now defers `restart()` instead of immediately relaunching a process that was just force-released — avoiding restarting something the user may not have wanted restarted yet.
- Rollback now covers Windows service/scheduled task creation, and no longer deletes files that pre-existed the failed install attempt (a user's own pre-existing file in the chosen install folder).
- `check_existing_install()` no longer treats a missing `DisplayVersion` value as "not installed."
- Windows service/scheduled task creation failures are now surfaced to the user as warnings instead of only being logged silently.
- Version comparison now accounts for pre-release suffixes, so a release build is correctly treated as newer than its own pre-release.
- The old uninstall.exe's exit code is no longer ignored during upgrade-uninstall.
- `self_delete.py` now falls back to an 8.3 short path (and logs it) when the install path contains characters the system's current ANSI codepage can't represent, instead of silently never scheduling self-deletion.
- `VERSIONINFO` resource generation no longer produces invalid syntax for fields containing quotes or backslashes.
- `restore_point.py` now calls `CoInitializeSecurity` before `SRSetRestorePointW`; `restart_manager.py`'s ctypes function signatures now have explicit `restype`/`argtypes`.
- `build_config_tool.py`'s `tasklist` check no longer uses `shell=True`.
- `builder.py`'s temp-artifact cleanup now runs in a `finally` block, so a failed build no longer leaks temp files.
- `uninstall.py` now aggregates and reports partial-failure warnings instead of always reporting success.
- `tests/test_shared_module_packaging.py`'s import-coverage check now follows imports transitively, not just the entry points' direct imports.

### Testing

590 tests, all passing.

---

## 繁體中文

發布基準：`v0.13.0`（2026-08-11）→ `v0.13.1`。這是一個純錯誤修正的版本，針對 v0.13.0 新增的安裝/解除安裝流程（Windows 服務/排程工作/BITS/系統還原點/相依元件下載）做了一輪完整的架構/安全性稽核後的修正。每一項修正都先寫失敗測試（重現真實缺陷、確認紅燈），再動手做最小修正。同時一併帶上稽核前就已經在 `main` 上、補齊 GUI 表單欄位的一個小修正。同一輪稽核發現的架構層級改善項目（config schema 單一真實來源、明確的 js_api 契約檢查、rollback 用的 install journal 抽象）**刻意不包含**在這個版本裡——已經另外 commit，依附帶的 ADR 說明留到下一次發布。

### 新增功能

**補上四個原本只有 CLI/JSON 能設定的 GUI 表單欄位**

`create_restore_point_before_install`、`dependencies_min_version`、`windows_service`、`scheduled_task` 這四項在 v0.13.0 後端（`gui_config.py`/`builder.py`/`installer_core.py`）雖然已經接上，但 `ui/config.html` 表單完全沒有對應欄位，GUI 使用者實際上無法設定。這裡補上表單欄位、中英雙語文字，以及 `submitForm()` 送出資料的串接。

### 錯誤修正

**安全性**

- **sc.exe 服務路徑未加引號（CWE-428）**：傳給 `sc create ... binPath=` 的執行檔路徑沒有用引號包起來，Service Control Manager 解析含空白的路徑（例如 `C:\Program Files\MyApp\app.exe`）時，服務啟動時可能被誘導執行攻擊者可以預先放置的前綴（`C:\Program.exe`）。修正方式：把字面上的引號字元包進傳給 `binPath=` 的值本身。
- **透過使用者可寫入登錄表機碼形成的提權執行鏈**：更新覆蓋安裝流程的跨 hive 查詢（原本是為了修正一個合理的偵測 bug 而加入）可能讓一個已提權的安裝程式行程，執行一支攻擊者透過非提權使用者可寫入的 HKCU 機碼安插的 `uninstall.exe`。現在當目前行程已提權、且來源是 HKCU 時會拒絕執行。
- **下載的相依元件缺少完整性/傳輸層驗證**：`custom_dependencies` 的下載連結現在強制要求 `https://`，並新增選填的 `sha256` 欄位，讓 `install_dependency()` 在執行下載回來的安裝檔之前先驗證雜湊值。

**正確性**

- 修正 `BITS` 工作狀態常數（`BG_JOB_STATE`）跟微軟實際列舉值不符的問題，並補上逾時保護，避免卡住的 BITS 工作讓安裝程式永遠等下去。
- 解除安裝登錄表機碼寫到一半（`SetValueEx` 序列中途）失敗時，現在會清乾淨，不再留下孤兒登錄表項目。
- `file_assoc.py` 現在透過 `InstallScope` 一致地解析正確的 hive（HKCU 或 HKLM），不再於 `no_admin_install` 模式下仍寫死 HKLM。
- `system_entries.remove_registry_entry()` 補上跨 hive fallback，跟既有的 `check_existing_install()` 邏輯一致。
- Restart Manager 優雅釋放路徑現在會延後呼叫 `restart()`，不會在強制釋放行程後立刻重新啟動它——避免重啟一個使用者可能還沒準備好要重啟的程式。
- Rollback 現在涵蓋 Windows 服務/排程工作的建立，也不再誤刪安裝失敗前，使用者選擇的安裝資料夾裡本來就存在的檔案。
- `check_existing_install()` 不再把缺少 `DisplayVersion` 值誤判成「沒有安裝過」。
- Windows 服務/排程工作建立失敗時，現在會回報成使用者看得到的警告，不再只靜默寫進 log。
- 版本比較補上 pre-release 後綴的判斷，確保正式版正確地被視為比自己的 pre-release 版本新。
- 更新覆蓋安裝流程不再忽略舊版 `uninstall.exe` 的結束碼。
- `self_delete.py` 遇到安裝路徑含有系統目前編碼無法表示的字元時，現在會改用 8.3 短路徑名稱重試並記錄，不再靜默放棄自我刪除排程。
- `VERSIONINFO` 資源產生器遇到含引號/反斜線的欄位時，不再產生語法錯誤的程式碼。
- `restore_point.py` 在呼叫 `SRSetRestorePointW` 前補上 `CoInitializeSecurity`；`restart_manager.py` 的 ctypes 函式簽章補上明確的 `restype`/`argtypes`。
- `build_config_tool.py` 的 `tasklist` 檢查不再使用 `shell=True`。
- `builder.py` 的暫存產物清理改用 `finally` 區塊，編譯失敗時不再留下暫存檔案。
- `uninstall.py` 現在會彙整並回報部分失敗的警告，不再一律回報成功。
- `tests/test_shared_module_packaging.py` 的 import 涵蓋檢查改成遞迴追蹤，不再只看進入點檔案最上層的直接 import。

### 測試

590 個測試，全數通過。

---

## Full commit list / 完整變更（commit）

```
b98ba3b fix: 修正架構/安全性稽核發現的錯誤與漏洞
1639f24 feat: GUI 表單補上系統還原點/服務/排程工作/相依元件版本欄位
```
