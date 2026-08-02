# v0.11.0 — Pre-release Notes

Baseline: `v0.10.0` (2026-08-02) → `v0.11.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

---

## English

This release closes a batch of gaps identified from a feature comparison against Inno Setup / NSIS / WiX-MSI / InstallShield / Advanced Installer, adds full GUI support for the new packaging options, and gives the uninstall helper a proper windowed interface that matches the installer's macOS-style visual language.

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

**The uninstall helper now has a real window**

`uninstall.exe` used to be a plain console program: three interactive prompts were native `MessageBoxW` dialogs, and double-clicking it opened a visible console window printing step-by-step text — a jarring contrast with the installer's polished drag-to-install screen. It's now a `pywebview`-based window (`ui/uninstall.html`) sharing the installer's exact visual language (rounded corners, blurred overlays, pill buttons, frameless custom drag). The linear `main()` was split into a `UninstallerAPI` class (confirm → detect locking processes/running app → progress → done), mirroring `installer_core.py`'s existing `InstallerAPI` pattern. Silent/unattended uninstall (used by update-overwrite installs and enterprise batch deployment) is untouched — no window, same behavior, same log file.

### Testing

288 tests (up from 202), all passing.

---

## 繁體中文

發布基準：`v0.10.0`（2026-08-02）→ `v0.11.0`。這輪跟 Inno Setup/NSIS/WiX-MSI/InstallShield/Advanced Installer 做過功能比較後，修掉了一批落差，把新增的打包選項補上完整 GUI 支援，並且讓解除安裝助手換上一套跟安裝畫面一致的 macOS 風格視窗介面。

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

**解除安裝助手終於有真正的視窗了**

`uninstall.exe` 原本是純 console 程式：三個互動時刻都是原生 `MessageBoxW`，雙擊執行還會跳出一個印著步驟文字的黑底命令提示字元視窗——跟安裝時那套精緻的拖曳畫面完全是兩個世界。現在改成 `pywebview` 視窗（`ui/uninstall.html`），套用跟安裝畫面完全一致的視覺語言（圓角、模糊疊層、藥丸按鈕、frameless 自訂拖曳）。原本一路線性到底的 `main()` 拆成 `UninstallerAPI` class（確認 → 偵測鎖定程式/主程式執行中 → 進度 → 完成），跟 `installer_core.py` 既有的 `InstallerAPI` 是同一套設計。靜默/無人值守解除安裝（更新覆蓋安裝、企業批次部署用）完全不受影響——沒有視窗、行為不變、log 檔案格式不變。

### 測試

288 個測試（從 202 個增加），全數通過。

---

## Full commit list / 完整變更（commit）

```
2f1f7c9 feat: 相依元件偵測支援自動下載並靜默安裝，並修正兩個真實回報的安裝/解除安裝 bug
（加上這次「chore: 發布 v0.11.0」commit：custom_dependencies、no_admin_install、
pre_install_script/post_install_script、bundle_dependencies、signing、/LOG=
六項架構落差修復；ui/config.html 完整 GUI 支援；uninstall.exe 改用
pywebview 視窗化介面（新增 ui/uninstall.html、UninstallerAPI）；
builder.py/packaging_core.py/build_config_tool.py 對應打包端調整；
新增 dependency_defs.py 共用模組；規格文件.md/CLI_USAGE.md 文件更新；
對應測試新增與調整）
```
