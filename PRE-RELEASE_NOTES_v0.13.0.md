# v0.13.0 — Pre-release Notes

Baseline: `v0.12.0` (2026-08-05) → `v0.13.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

> **Code signing**: This project has applied to the [SignPath Foundation](https://signpath.io/solutions/open-source-community) open-source code signing program to provide trusted, signed installers. Signing is not yet active on this release — this note will be updated once the integration is live.
> **簽章聲明**：本專案已申請加入 [SignPath Foundation](https://signpath.io/solutions/open-source-community) 開源簽章方案，用以提供受信任的已簽章安裝檔。這個版本尚未套用簽章，整合完成後會更新這則說明。

---

## English

This release adds six previously-backlogged packaging capabilities in one pass — versioned dependency checks, full Restart Manager integration, pre-install system restore points, BITS-based downloads, Windows service registration, and Task Scheduler task registration — all implemented test-first. It also stamps the packaging tool's own GUI/CLI executables with proper Win32 VERSIONINFO resources, and adds a separate, manually-triggered CI workflow that actually installs/uninstalls a packaged app on a real Windows runner to verify these system-level effects, independent of the existing mocked unit tests.

### New Features

**Versioned dependency checks**

Dependency checkers (`vcredist_x64`, `.NET Desktop Runtime`, and custom registry-based checks) can now require a *minimum version*, not just presence. A new `_generic_registry_version_check()` reads the installed version from the registry (including enumerating subkeys for runtimes like .NET Desktop Runtime that expose versions as subkey names) and compares it against a configured `min_version`. Fully backward compatible — checkers with no `min_version` configured keep their original existence-only behavior.

**Full Restart Manager integration**

New `RestartManagerSession` class wraps the complete Restart Manager API (`RmStartSession`/`RmRegisterResources`/`RmGetList`/`RmShutdown`/`RmRestart`/`RmEndSession`), not just process discovery. `explorer_lock_release.py` now tries a Restart-Manager-driven shutdown/restart cycle as an additional layer between "close the Explorer window" and the existing force-kill fallback, giving Windows a chance to gracefully close and relaunch the locking application itself.

**System restore point before install**

New `create_restore_point_before_install` option calls `SRSetRestorePointW` (via `srclient.dll`) right before installation begins, using the two-phase `BEGIN_SYSTEM_CHANGE`/`END_SYSTEM_CHANGE` protocol. Failure only logs a warning — it never blocks installation.

**BITS-based dependency downloads**

`install_dependency()` now tries Background Intelligent Transfer Service (`BackgroundCopyManager.1` via `win32com`) first for downloading dependency installers, with progress reporting, falling back to the original `urllib` download if BITS is unavailable or fails.

**Windows service support**

New `windows_service` packaging option registers a `sc.exe`-managed Windows service pointing at a file inside the installed app, with a configurable start type. The service name is recorded in `install_manifest.json` and removed automatically on uninstall.

**Task Scheduler support**

New `scheduled_task` packaging option registers a `schtasks.exe`-managed scheduled task with a configurable trigger. Same manifest-tracked, auto-removed-on-uninstall pattern as the service support above.

**Win32 VERSIONINFO on the packaging tool's own executables**

`build_config_tool.py` now accepts `--publisher`, embedding CompanyName/LegalCopyright into the compiled GUI/CLI exes' VERSIONINFO resource, so File Explorer's Properties → Details tab is no longer blank.

### CI / Testing infrastructure

**New: `test-packaging-options.yml` — real-system verification workflow**

A new, independent, `workflow_dispatch`-only GitHub Actions workflow (never triggered by pushes or `v*` tags, so it cannot interfere with the release pipeline) that actually packages a `Setup.exe` covering most packaging fields, silently installs it on a `windows-latest` runner, verifies via PowerShell that the registry/service/scheduled task/PATH/file-association effects really happened, silently uninstalls, and verifies cleanup — closing a real gap, since the existing unit tests mock out `subprocess`/`winreg`/PyInstaller entirely. Paired with a new `/test-packaging-options` Claude Code skill that triggers the workflow and reports results. Excludes `dependencies`/`signing` from its matrix (too slow/unsafe for CI); system restore point verification is a documented known-limitation on GitHub's Windows Server-based runners.

### Bug Fixes

- **Pre/post-install script marker files not detectable from outside the install**: caught by the first real run of the new CI workflow above — not a bug in `pre_install_script`/`post_install_script` execution itself, but in how the test workflow's own scripts located their output (`%~dp0`, which `get_resource_path()` resolves to PyInstaller's transient extraction directory, not the install directory). Fixed the test workflow to write to `%TEMP%` instead.

### Documentation

- `MSIX輸出規劃.md`: continued research/decision record for a possible future MSIX packaging output — CI build ordering for downstream projects consuming this tool, signing-must-precede-embedding dependency, and progress-reporting feasibility notes. Still explicitly unimplemented.

### Testing

514 tests (up from 405), all passing.

---

## 繁體中文

發布基準：`v0.12.0`（2026-08-05）→ `v0.13.0`。這輪一次補齊六項先前列在待辦清單的打包功能——相依元件版本檢查、Restart Manager 全套整合、安裝前系統還原點、BITS 下載、Windows 服務註冊、排程工作註冊——全部依 TDD（先寫測試再寫實作）完成。同時幫打包工具自己的 GUI/CLI 執行檔補上正式的 Win32 VERSIONINFO 資源，並新增一個獨立、手動觸發的 CI workflow，在真實 Windows runner 上實際安裝/解除安裝一份打包出來的應用程式，驗證這些系統層級效果真的發生，補上既有 mock 單元測試測不到的部分。

### 新增功能

**相依元件版本檢查**

相依元件檢查（`vcredist_x64`、.NET Desktop Runtime、自訂登錄表檢查）現在可以要求「最低版本」，不只是「有沒有安裝」。新的 `_generic_registry_version_check()` 從登錄表讀出已安裝版本（含列舉子機碼，因應 .NET Desktop Runtime 這類把版本號放在子機碼名稱裡的 runtime），跟設定的 `min_version` 比較。完全向下相容——沒設定 `min_version` 的檢查項維持原本「只看有沒有裝」的行為。

**Restart Manager 全套整合**

新增 `RestartManagerSession` 類別，包裝完整的 Restart Manager API（`RmStartSession`/`RmRegisterResources`/`RmGetList`/`RmShutdown`/`RmRestart`/`RmEndSession`），不再只是查詢鎖定行程。`explorer_lock_release.py` 現在會在「關閉檔案總管視窗」跟既有的強制終止兩層之間，多插入一層「透過 Restart Manager 讓 Windows 自己優雅關閉並重啟鎖定程式」的嘗試。

**安裝前系統還原點**

新增 `create_restore_point_before_install` 選項，在安裝開始前呼叫 `SRSetRestorePointW`（透過 `srclient.dll`），使用兩階段 `BEGIN_SYSTEM_CHANGE`/`END_SYSTEM_CHANGE` 協定。失敗只記錄警告，不會擋下安裝。

**BITS 下載相依元件**

`install_dependency()` 現在會優先透過 Background Intelligent Transfer Service（`win32com` 呼叫 `BackgroundCopyManager.1`）下載相依元件安裝檔並回報進度，BITS 無法使用或失敗時退回原本的 `urllib` 下載方式。

**Windows 服務安裝支援**

新增 `windows_service` 打包選項，透過 `sc.exe` 註冊一個指向安裝目錄內某檔案的 Windows 服務，啟動類型可設定。服務名稱會記錄在 `install_manifest.json`，解除安裝時自動移除。

**排程工作（Task Scheduler）支援**

新增 `scheduled_task` 打包選項，透過 `schtasks.exe` 註冊排程工作，觸發條件可設定。跟上面的服務支援一樣走 manifest 追蹤、解除安裝自動清除的模式。

**打包工具自己的執行檔補上 Win32 VERSIONINFO**

`build_config_tool.py` 現在接受 `--publisher`，把 CompanyName/LegalCopyright 寫進編譯出來的 GUI/CLI exe 的 VERSIONINFO 資源，檔案總管「內容 → 詳細資料」頁籤不再是空白。

### CI / 測試基礎建設

**新增：`test-packaging-options.yml` —— 真實系統驗證 workflow**

新增一個獨立、只用 `workflow_dispatch` 手動觸發的 GitHub Actions workflow（不會因為 push 或打 `v*` tag 被觸發，不會干擾正式發布流程），實際打包一份涵蓋大部分打包欄位的 `Setup.exe`，靜默安裝到 `windows-latest` runner 上，用 PowerShell 驗證登錄表/服務/排程工作/PATH/檔案關聯等系統層級效果真的發生，再靜默解除安裝並驗證清乾淨——補上既有單元測試全部 mock 掉 `subprocess`/`winreg`/PyInstaller 測不到的一塊。搭配新增的 `/test-packaging-options` Claude Code skill 負責觸發跟彙整結果。矩陣刻意不含 `dependencies`/`signing`（CI 上太慢/不安全）；系統還原點驗證在 GitHub 的 Windows Server 系 runner 上是已知的驗證限制，如實記錄。

### 錯誤修正

- **前後置腳本的標記檔案從外部驗證不到**：由上述新 CI workflow 第一次真正執行時抓到——不是 `pre_install_script`/`post_install_script` 本身執行有問題，而是測試 workflow 自己的腳本找錯了輸出位置（`%~dp0`，被 `get_resource_path()` 解析成 PyInstaller 的暫存解壓目錄，不是安裝目錄）。已修正測試 workflow 改寫到 `%TEMP%`。

### 文件

- `MSIX輸出規劃.md`：持續累積未來可能的 MSIX 打包輸出格式研究/決策紀錄——下游專案的 CI 建置順序、簽章必須先於嵌入的依賴關係、進度回報可行性補充。仍明確標註「尚未實作」。

### 測試

514 個測試（從 405 個增加），全數通過。

---

## Full commit list / 完整變更（commit）

```
a2b6bb6 fix: 前後置腳本標記檔案改寫到 %TEMP%，不要用 %~dp0
3d6b6dc ci: 新增測試所有打包選項的獨立 workflow
edbd5d5 feat: 新增 Windows 服務/排程工作/系統還原點/BITS/相依元件版本檢查/Restart Manager 全套整合
4a72528 docs: 補上 MSIX 進度回報的可行性與 Python 綁定保留條件
82c778f docs: 補上 MSIX 規劃的 CI 建置順序與下游專案分工細節
1643e87 docs: MSIX 輸出規劃研究記錄 + 待辦清單新增 + Release Notes 補上 SignPath 簽章聲明
d15bf89 feat: 打包出來的 exe 帶上 Win32 VERSIONINFO 資源
```
