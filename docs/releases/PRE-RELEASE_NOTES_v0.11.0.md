# v0.11.0 — Pre-release Notes

Baseline: `v0.10.0` (2026-08-02) → `v0.11.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

---

## English

This release closes a batch of gaps identified from a feature comparison against Inno Setup / NSIS / WiX-MSI / InstallShield / Advanced Installer, adds full GUI support for the new packaging options, gives the uninstall helper a proper windowed interface that matches the installer's macOS-style visual language, adds a native "drag app to trash" uninstall gesture with an undo grace period, and starts automating this project's own release builds via GitHub Actions.

### New Features

**Dependency detection is no longer hard-coded (`custom_dependencies`)**

`DEPENDENCY_CHECKERS` used to be a fixed module-level dict covering only `vcredist_x64`/`dotnet_desktop`. A new `custom_dependencies` field (JSON-only, same reasoning as `eula_texts`/`doc_icons`) lets a packager register any dependency: display name, download URL, silent-install args, and a generic registry check (`{hive, path, value_name, expected}`). A new `_generic_registry_check()` replaces the two hand-written checkers; built-in and custom dependencies merge into a per-instance table so tests (and future processes) never see stale patched state.

**No-admin (per-user) install mode (`no_admin_install`)**

Every installer this tool produced required elevation unconditionally. With `no_admin_install` enabled, both `Setup.exe` and `uninstall.exe` compile without `--uac-admin`, the default install root becomes `%LOCALAPPDATA%\Programs\<folder>`, the uninstall registry entry/PATH/shortcuts move to per-user locations (`HKEY_CURRENT_USER`, the user's own Desktop/Start Menu) instead of machine-wide ones — no UAC prompt at all, end to end.

**Pre/post-install script hooks (`pre_install_script` / `post_install_script`)**

Two new optional fields let a packager run a script or executable before file copying starts (failure aborts the install) or after the registry/shortcuts are written (failure is only logged as a warning). This is the first extension point this tool has ever offered — previously there was no way to run custom logic during install at all.

**Dependencies can be bundled instead of downloaded (`bundle_dependencies`)**

`install_dependency()` used to always download over the network. `bundle_dependencies` lets the packager embed the redistributable inside the installer at pack time (downloaded once during packaging, stored at a fixed `dependencies/<key>.exe` path) — useful when the target machine may be offline. Not listed = still downloads online as before.

**Code-signing automation (`signing`)**

This doesn't obtain a certificate for you — it wires up the `signtool` step so that, once you have your own `.pfx` and set `signing.cert_path`/`cert_password_env`/`timestamp_url`, packaging automatically signs both `Setup.exe` and `uninstall.exe`. The password itself is never stored in the config, only the name of an environment variable to read it from at packaging time.

**Configurable silent-install/uninstall log path (`/LOG=`)**

The silent install and uninstall log used to be hard-coded to `%TEMP%\<AppName>_..._log.txt`. Both `Setup.exe` and `uninstall.exe` now accept a `/LOG=<path>` command-line flag; a write failure at the custom path falls back to the original `%TEMP%` location rather than failing the whole operation.

**Full GUI support for all of the above**

`ui/config.html` gained a dynamic add/remove list for custom dependencies, a checkbox-gated signing panel with a certificate file picker, plain-text inputs for the two script hooks, and checkboxes for no-admin install and per-dependency bundling — none of this previously had a form, only JSON/CLI support.

**Dependencies can now be auto-installed, not just detected**

Missing dependencies (`vcredist_x64`/`dotnet_desktop`) are downloaded from their official sources and installed silently during install, then re-detected — no more "here's a link, go install it yourself and re-run." Users can still choose to skip.

**The uninstall helper now has a real window**

`uninstall.exe` used to be a plain console program: three interactive prompts were native `MessageBoxW` dialogs, and double-clicking it opened a visible console window printing step-by-step text — a jarring contrast with the installer's polished drag-to-install screen. It's now a `pywebview`-based window (`ui/uninstall.html`) sharing the installer's exact visual language (rounded corners, blurred overlays, pill buttons, frameless custom drag). The linear `main()` was split into a `UninstallerAPI` class (confirm → detect locking processes/running app → progress → done), mirroring `installer_core.py`'s existing `InstallerAPI` pattern. Silent/unattended uninstall (used by update-overwrite installs and enterprise batch deployment) is untouched — no window, same behavior, same log file.

**Drag the app icon onto the trash can to uninstall**

The confirm-delete step now mirrors macOS's native uninstall gesture: drag the app icon onto a trash can, whose lid springs open on hover (a small hand-rolled JS spring, not CSS transitions, so it stays interruptible if you drag in and out quickly) and settles closed if you let go elsewhere. Dropping onto the trash starts a progress bar that fills the first quarter over a 4-second undo window (non-uniform speed, reusing the same decelerating-approach curve the packaging tool's own progress bar uses) with a visible "Undo" button; if you don't cancel, the same bar continues seamlessly into the real deletion progress.

**GitHub Actions build workflow**

A new `.github/workflows/build.yml` reproduces this project's own release build steps (test → compile GUI/CLI → pack Setup.exe) on a clean CI runner, triggered by pushing a `v*` tag or manually. This doesn't sign or publish anything yet — it's the prerequisite for eventually getting this project's own release artifacts signed via SignPath Foundation's free open-source program, since SignPath only trusts binaries built from a registered repo's CI, not local builds. The icon assets needed for packaging (previously living outside the repo) were copied into a new `branding/` folder so CI can access them.

### Bug Fixes

- Uninstall now uses the Windows Restart Manager API (new `restart_manager.py`) to detect and close processes that are actually locking installed files, instead of hardcoding an assumption that it's always `explorer.exe`; interactive manual uninstall now also benefits from the `restart_explorer_on_update` setting (previously it only applied to unattended uninstall, so shell-extension DLLs could linger after a manual uninstall and cause a false "access denied" on the next install to the same path).
- Install failure messages are now classified by the actual Windows error code instead of always being reported as "insufficient permissions" (which is rarely the real cause, since the installer already runs elevated) — locked files, access-denied, and read-only-disk are now distinguished, and a locked-file failure names the offending process.
- Fixed the uninstaller's self-delete retry logic: a `for /l` batch loop that looked correct but silently never re-evaluated its lock-check under real file-lock contention (root-caused via a controlled repro with a held file lock), replaced with a `.bat`-file goto/retry mechanism.
- Fixed a `--noconsole` + `subprocess.Popen(shell=True)` invalid-handle failure that silently broke the self-delete launch, by explicitly redirecting stdio to `DEVNULL`.
- Fixed a ~1-2 second visible hang before the uninstaller window disappears (a WebView2 GPU-compositor artifact) by calling `window.hide()` immediately before process exit.
- Fixed flaky trash-drop-target hover detection (a classic HTML5 drag-and-drop child-element `dragenter`/`dragleave` boundary bug) by making the inner trash-can graphic non-interactive for pointer events.
- Fixed a visually tilted progress bar on the uninstall progress screen (missing horizontal centering).

### Documentation

- `使用說明書.md` (user manual) brought up to date with all of the above: dependency auto-install, per-user install mode, pre/post scripts, bundled dependencies, digital signature setup, the new uninstall UI, and a new section on using the CLI from your own CI pipeline.
- `規格文件.md` gained new sections recording: the installer window/layout conventions (previously undocumented, now the reference for any new screen), the uninstall confirm screen's drag-to-trash design decisions, and a consolidated color-token reference across all three GUI surfaces.
- Fixed an incorrect SignPath Foundation URL (`signpath.io` → the correct `signpath.org`) in the docs.
- Added `CLAUDE.md`: a `v*` release tag must only ever be created by actually running the `/released` process, never as a side effect of an ad-hoc packaging request.

### Testing

292 tests (up from 202), all passing.

---

## 繁體中文

發布基準：`v0.10.0`（2026-08-02）→ `v0.11.0`。這輪跟 Inno Setup/NSIS/WiX-MSI/InstallShield/Advanced Installer 做過功能比較後，修掉了一批落差，把新增的打包選項補上完整 GUI 支援，讓解除安裝助手換上一套跟安裝畫面一致的 macOS 風格視窗介面，新增仿 macOS 原生「拖曳 App 圖示到垃圾桶」的解除安裝手勢（含反悔倒數），並開始把這個專案自己的發布建置流程搬進 GitHub Actions 自動化。

### 新增功能

**相依元件清單不再寫死在原始碼裡（`custom_dependencies`）**

`DEPENDENCY_CHECKERS` 原本是模組層級寫死的字典，只有 `vcredist_x64`/`dotnet_desktop` 兩個。新增 `custom_dependencies` 欄位（只能透過 JSON，跟 `eula_texts`/`doc_icons` 同樣理由），讓封裝者可以自訂任意相依元件：顯示名稱、下載連結、靜默安裝參數、泛用登錄表檢查（`{hive, path, value_name, expected}`）。新增的 `_generic_registry_check()` 取代原本兩個各自寫死的檢查函式；內建跟自訂的相依元件合併成實例層級的對照表，避免測試（或未來多實例情境）撿到過期的 patch 狀態。

**免系統管理員權限（per-user）安裝模式（`no_admin_install`）**

這個工具產出的安裝檔原本一律要求提權。開啟 `no_admin_install` 後，`Setup.exe`/`uninstall.exe` 都不帶 `--uac-admin` 編譯，預設安裝路徑改成 `%LOCALAPPDATA%\Programs\<資料夾>`，解除安裝登錄表/PATH/捷徑都改寫到使用者層級（`HKEY_CURRENT_USER`、使用者自己的桌面/開始功能表）——全程完全不跳 UAC。

**安裝前置/後置腳本掛鉤（`pre_install_script`/`post_install_script`）**

兩個新增選填欄位，讓封裝者可以在複製檔案之前（失敗會中止安裝）或登錄表/捷徑寫完之後（失敗只記警告）執行自訂腳本或執行檔——這是這個工具第一次提供的擴充點，之前完全沒有在安裝過程插入自訂邏輯的方式。

**相依元件可以打包時內嵌，不用線上下載（`bundle_dependencies`）**

`install_dependency()` 原本一律連網下載，新增 `bundle_dependencies` 讓封裝者可以在打包當下把安裝檔內嵌進去（固定掛載路徑 `dependencies/<key>.exe`），適合不確定目標機器有沒有網路的情境；沒列進去的相依元件維持原本線上下載的行為。

**數位簽章自動化（`signing`）**

這裡不會幫你生出憑證——設定好自己準備的 `.pfx` 憑證跟 `signing.cert_path`/`cert_password_env`/`timestamp_url` 之後，打包會自動幫 `Setup.exe`/`uninstall.exe` 跑 `signtool` 簽章。密碼本身不會存進設定檔，只存放密碼的環境變數名稱，打包當下才讀取。

**靜默安裝/解除安裝的紀錄路徑可自訂（`/LOG=`）**

原本紀錄檔路徑寫死在 `%TEMP%\<AppName>_..._log.txt`。現在 `Setup.exe`/`uninstall.exe` 都支援 `/LOG=<路徑>` 命令列參數，指定路徑寫入失敗會退回原本的 `%TEMP%` 位置，不會讓整個操作失敗。

**以上欄位全部補上完整 GUI 支援**

`ui/config.html` 新增了自訂相依元件的動態新增/移除列表、勾選開關的簽章設定區塊（含憑證選擇按鈕）、兩個腳本欄位的文字輸入框、免提權安裝跟逐一相依元件內嵌的勾選框——這些原本都只有 JSON/CLI 支援，沒有對應表單。

**相依元件現在可以自動安裝，不只是偵測**

缺少的相依元件（`vcredist_x64`／`dotnet_desktop`）會從官方下載點自動下載並靜默安裝，裝完後重新偵測，不再只是「給你一個連結，自己去裝完再重跑」。使用者仍然可以選擇略過。

**解除安裝助手終於有真正的視窗了**

`uninstall.exe` 原本是純 console 程式：三個互動時刻都是原生 `MessageBoxW`，雙擊執行還會跳出一個印著步驟文字的黑底命令提示字元視窗——跟安裝時那套精緻的拖曳畫面完全是兩個世界。現在改成 `pywebview` 視窗（`ui/uninstall.html`），套用跟安裝畫面完全一致的視覺語言（圓角、模糊疊層、藥丸按鈕、frameless 自訂拖曳）。原本一路線性到底的 `main()` 拆成 `UninstallerAPI` class（確認 → 偵測鎖定程式/主程式執行中 → 進度 → 完成），跟 `installer_core.py` 既有的 `InstallerAPI` 是同一套設計。靜默/無人值守解除安裝（更新覆蓋安裝、企業批次部署用）完全不受影響——沒有視窗、行為不變、log 檔案格式不變。

**拖曳 App 圖示到垃圾桶＝解除安裝**

確認刪除這一步改成仿 macOS 原生手勢：把 App 圖示拖到垃圾桶上，垃圾桶蓋子會彈開（純手刻的小型 JS 彈簧動畫，不是 CSS transition，快速拖進拖出時才能保持可中斷、不卡頓），放開在外面則平滑闔回。放到垃圾桶上放開後，進度條會開始跑，前 1/4 是 4 秒的反悔倒數（速度不是勻速，沿用打包工具進度條本來就有的那種先快後慢的漸進趨近曲線），期間有明顯的「復原」按鈕；沒有取消的話，同一條進度條會無縫接續真正的刪除進度。

**GitHub Actions 建置流程**

新增 `.github/workflows/build.yml`，在乾淨的 CI 機器上重現這個專案自己的發布建置步驟（跑測試 → 編 GUI/CLI → 打包 Setup.exe），推 `v*` tag 或手動觸發都會跑。目前還不簽章、不自動發布——這是之後要幫這個專案自己的發布產物申請 SignPath Foundation 開源免費簽章方案的前提，因為 SignPath 只信任「從已註冊 repo 的 CI 建置出來的東西」，本機建置的產物簽不到。打包需要的圖示素材（原本放在 repo 外層）也一併複製進新增的 `branding/` 資料夾，讓 CI 拿得到。

### 錯誤修正

- 解除安裝改用 Windows Restart Manager API（新增 `restart_manager.py`）實際偵測並結束真正鎖定檔案的進程，不再寫死假設一定是 `explorer.exe`；互動式手動解除安裝現在也套用 `restart_explorer_on_update` 設定（原本只有無人值守情境才生效，導致手動解除安裝後殼層擴充功能 DLL 依然殘留，下次安裝到同一路徑時誤報權限不足）。
- 安裝失敗訊息改依真正的 Windows 錯誤碼分類，不再一律歸類成「權限不足」（這支安裝程式本身就是系統管理員身分執行，這個建議大部分情況下沒有意義）——現在會分辨檔案被鎖住/存取被拒/磁碟唯讀，檔案被鎖住時直接點名是哪個程式。
- 修正解除安裝助手的自我刪除重試邏輯：原本的 `for /l` 批次迴圈表面上看起來沒問題，但在真實檔案鎖定情境下會靜默地永遠不重新檢查鎖定狀態（透過控制變因的鎖定重現實驗根因確認），改成用 `.bat` 檔的 goto/retry 機制取代。
- 修正 `--noconsole` 搭配 `subprocess.Popen(shell=True)` 的無效控制代碼錯誤，這個錯誤會讓自我刪除的啟動動作靜默失敗，改成明確把 stdio 導向 `DEVNULL`。
- 修正解除安裝視窗消失前會卡住約 1-2 秒的問題（WebView2 GPU 合成器的視覺殘留），在程序結束前先呼叫 `window.hide()`。
- 修正拖曳到垃圾桶上方時偶爾「愛理不理」偵測不到的問題（經典的 HTML5 拖放子元素 `dragenter`/`dragleave` 邊界問題），讓垃圾桶內部的圖案不接收滑鼠事件。
- 修正解除安裝進度條頁面歪一邊的問題（缺少水平置中）。

### 文件

- `使用說明書.md` 補齊以上所有改動：相依元件自動安裝、免提權安裝模式、前置/後置腳本、內嵌相依元件、數位簽章設定、新版解除安裝介面，以及新增「在自己的 CI 裡使用這個工具的 CLI」段落。
- `規格文件.md` 新增：安裝畫面視窗/版面慣例（先前沒有記錄，現在是新畫面的參考依據）、解除安裝確認畫面拖曳到垃圾桶的設計取捨紀錄、三個 GUI 介面統整過的色票對照表。
- 修正文件裡 SignPath Foundation 網址寫錯的問題（`signpath.io` → 正確的 `signpath.org`）。
- 新增 `CLAUDE.md`：`v*` 發布 tag 只能透過真的執行 `/released` 流程產生，不能因為單純的打包測試請求就順便打上去。

### 測試

292 個測試（從 202 個增加），全數通過。

---

## Full commit list / 完整變更（commit）

```
2f1f7c9 feat: 相依元件偵測支援自動下載並靜默安裝，並修正兩個真實回報的安裝/解除安裝 bug
0745065 feat: 新增 GitHub Actions 建置流程，並把打包用的圖示資產收進 repo
（加上這次「chore: 發布 v0.11.0」commit：custom_dependencies、no_admin_install、
pre_install_script/post_install_script、bundle_dependencies、signing、/LOG=
六項架構落差修復；ui/config.html 完整 GUI 支援；uninstall.exe 改用
pywebview 視窗化介面（新增 ui/uninstall.html、UninstallerAPI）；解除安裝
助手拖曳到垃圾桶手勢＋反悔倒數進度條；自我刪除重試機制與 window.hide()
相關修正；builder.py/packaging_core.py/build_config_tool.py 對應打包端
調整；新增 dependency_defs.py、restart_manager.py 共用模組；
規格文件.md/使用說明書.md/CLI_USAGE.md 文件更新；新增 CLAUDE.md；
對應測試新增與調整）
```
