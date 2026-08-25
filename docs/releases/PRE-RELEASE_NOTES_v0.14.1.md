# v0.14.1 — Pre-release Notes

Baseline: `v0.14.0` (2026-08-25) → `v0.14.1`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

> **Code signing**: This project has applied to the [SignPath Foundation](https://signpath.io/solutions/open-source-community) open-source code signing program to provide trusted, signed installers. Signing is not yet active on this release — this note will be updated once the integration is live.
> **簽章聲明**：本專案已申請加入 [SignPath Foundation](https://signpath.io/solutions/open-source-community) 開源簽章方案，用以提供受信任的已簽章安裝檔。這個版本尚未套用簽章，整合完成後會更新這則說明。

---

## English

This release is a maintenance/bug-fix release: two real user-facing detection fixes, plus a batch of internal architecture cleanup from a codebase review. No new packaging config fields, no breaking changes.

### Bug Fixes

**.NET Desktop Runtime false-negative detection**

`_check_dotnet_desktop()` previously only trusted a registry key (`InstalledVersions\...`) that is only written by the official MSI-based installer. Installs via winget, the Visual Studio Installer, or `dotnet-install.ps1` never write that key — even when `dotnet --list-runtimes` correctly lists the installed version, the installer would report it as missing and prompt the user to auto-install a redundant copy. Now falls back to scanning `%ProgramFiles%\dotnet\shared\Microsoft.WindowsDesktop.App\` directly (the same approach `dotnet --list-runtimes` itself uses) when the registry key is absent.

**`cryptography` import made lazy**

`install_encryption.py` imported the `cryptography` package at module top level, which broke this project's own design rule that the packaging tool itself must be able to open even when the target build environment is missing optional dependencies. Fixed by deferring the import; also filled in `cryptography` in every CI workflow's and doc's `pip install` list where it had been missing.

### Improvements

**Architecture cleanup from a codebase review (`/improve-codebase-architecture`)**

Continuing the deep-module extraction started in v0.14.0, this round pulled four more concerns out of `installer_core.py` (still the single most frequently-touched, largest file in the repo):

- New `progress_report.py` and two additions to `system_entries.py` (`cleanup_empty_dirs()`/`kill_process_by_name()`) collapse three primitives that `installer_core.py` and `uninstall.py` each carried a near-byte-identical duplicate of. Consolidating them surfaced a real latent bug: the module-level `window` global was never explicitly initialized — it only ever worked because the old duplicated code each caught the resulting `NameError` inside its own bare `try/except`. Both modules now explicitly default `window = None`.
- New `dependency_install.py` and `version_compare.py`: `dependency_defs.py`'s docstring had claimed "checker/URL/silent-install-args logic is defined here" for a while, but that file only ever held an inert metadata dict — the real registry-detection, download/verify/install protocol lived scattered across `installer_core.py`. This is the exact gap that made the .NET detection bug above harder to track down. The new module's interface takes explicit parameters instead of reading `InstallerAPI` instance state.
- New `upgrade.py` (`UpgradeCoordinator`): the upgrade-in-place subsystem (detect an existing install, back it up, silently invoke the old `uninstall.exe`, elevate across UAC when needed, restore on failure) used to be 8 loosely-related `InstallerAPI` methods reaching into shared instance attributes. Collapsed into one coordinator object with a narrow interface; `InstallerAPI` now keeps only thin delegating methods.
- `run_uninstall_exe_elevated()` gained an optional `shell32=`/`kernel32=` injection point, matching the `registry=` seam pattern already used elsewhere — tests can now substitute a fake elevated-process adapter instead of monkeypatching the process-global `ctypes.windll`.

### Documentation

Backfilled the `v0.13.1` release notes onto the `main` branch (previously only existed on the release tag).

### Testing

651 tests, all passing.

---

## 繁體中文

發布基準：`v0.14.0`（2026-08-25）→ `v0.14.1`。這個版本是維護/修正版：兩個真的會影響使用者的偵測 bug 修正，外加一批架構稽核帶來的內部程式碼改善。沒有新增打包設定欄位，沒有破壞性變更。

### 錯誤修正

**.NET Desktop Runtime 誤判成沒裝**

`_check_dotnet_desktop()` 原本只信一把登錄表機碼（`InstalledVersions\...`），但那把機碼只有透過官方 MSI 版安裝程式裝的才會寫入——透過 winget、Visual Studio Installer、或 `dotnet-install.ps1` 裝的完全不會寫這把機碼，即使 `dotnet --list-runtimes` 能正常列出已安裝版本，安裝程式還是會判定沒裝、要求使用者自動安裝一份多餘的重複版本。現在登錄表查不到時會改掃 `%ProgramFiles%\dotnet\shared\Microsoft.WindowsDesktop.App\`（跟 `dotnet --list-runtimes` 本身判斷方式一致）當備援。

**`cryptography` 改成延遲 import**

`install_encryption.py` 原本在檔案最上層直接 import `cryptography`，違反這個專案「打包工具本身要能開起來，不管建置環境有沒有裝齊選填的相依套件」的既有設計原則。已改成延遲 import，也順手把所有 CI workflow 跟文件裡漏列 `cryptography` 的 `pip install` 清單補齊。

### 改善

**架構稽核帶來的內部程式碼改善（`/improve-codebase-architecture`）**

接續 v0.14.0 開始的深模組拆分，這輪又從 `installer_core.py`（依然是全 repo 異動最頻繁、體積最大的檔案）拆出四個關注點：

- 新增 `progress_report.py`，`system_entries.py` 補上 `cleanup_empty_dirs()`/`kill_process_by_name()` 兩個函式，收掉 `installer_core.py`/`uninstall.py` 各自一份逐位元組幾乎相同的三個原語。收斂過程中意外挖出一個真實 bug：`window` 這個模組層級全域變數原本從未被顯式初始化，能正常運作純粹是因為原本重複的程式碼各自把讀取未賦值全域變數的 `NameError` 吞在自己的 `try/except` 裡——兩個模組現在都顯式給 `window = None`。
- 新增 `dependency_install.py`/`version_compare.py`：`dependency_defs.py` 的說明文字一直宣稱「checker/URL/靜默安裝參數本體定義在這」，但那個檔案其實只有一個沒有行為的 metadata dict，真正的登錄表偵測、下載/驗證/安裝協定全部散落在 `installer_core.py`——這正是上面那個 .NET 偵測 bug 排查起來比較繞路的原因。新模組的介面吃明確參數，不吃 `InstallerAPI` 的實例狀態。
- 新增 `upgrade.py`（`UpgradeCoordinator`）：覆蓋安裝子系統（偵測已安裝舊版本、備份、靜默呼叫舊版 `uninstall.exe`、必要時跨 UAC、失敗復原）原本是 `InstallerAPI` 上 8 個各自獨立、靠共享實例屬性互相傳遞狀態的方法，收斂成一個窄介面的協調物件，`InstallerAPI` 只保留薄委派方法。
- `run_uninstall_exe_elevated()` 補上選填的 `shell32=`/`kernel32=` 注入點，跟既有的 `registry=` seam 模式一致——測試現在可以換成假的「提權後行程」adapter，不用再改寫行程全域共用的 `ctypes.windll`。

### 文件

把 `v0.13.1` 的 Release Notes 補回 `main` 分支（原本只存在於發布 tag 上）。

### 測試

651 個測試，全數通過。

---

## Full commit list / 完整變更（commit）

```
ed42f92 docs: 補回 v0.13.1 的 release notes 到 main 分支
4149c4b fix: cryptography 改延遲 import，並補齊 CI/文件的 pip install 清單
36e14f8 fix: .NET Desktop Runtime 偵測改加掃安裝目錄備援，不再只信登錄表
da22d22 refactor: 收斂 installer_core.py/uninstall.py 重複的進度回報與行程操作原語
deb84d3 refactor: 拆出 dependency_install.py/version_compare.py，收斂相依元件子系統
f6e57dd refactor: 拆出 upgrade.py，收斂覆蓋安裝子系統成 UpgradeCoordinator
b140b04 refactor: run_uninstall_exe_elevated() 加 shell32=/kernel32= 注入點
```
