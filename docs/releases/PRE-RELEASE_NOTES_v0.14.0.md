# v0.14.0 — Pre-release Notes

Baseline: `v0.13.1` (2026-08-12) → `v0.14.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

> **Code signing**: This project has applied to the [SignPath Foundation](https://signpath.io/solutions/open-source-community) open-source code signing program to provide trusted, signed installers. Signing is not yet active on this release — this note will be updated once the integration is live.
> **簽章聲明**：本專案已申請加入 [SignPath Foundation](https://signpath.io/solutions/open-source-community) 開源簽章方案，用以提供受信任的已簽章安裝檔。這個版本尚未套用簽章，整合完成後會更新這則說明。

---

## English

This release adds one real end-user feature — optional install-time password protection — plus a batch of internal architecture cleanup identified in an earlier codebase audit, and a repo-wide documentation reorganization. No breaking changes to existing packaging config fields.

### New Features

**Optional install password protection**

A new `install_password_env` packaging field lets you password-protect the application files inside the generated installer. When set:

- The entire `app_contents` payload is encrypted (AES-256-GCM, key derived via PBKDF2) before being embedded into the installer, via a new `install_encryption.py` module (adds a `cryptography` dependency — the project's first real third-party cryptography package).
- End users see a password screen (appearing *before* the EULA step) when they run the installer. A correct password decrypts the payload to a temp folder that feeds into the existing copy/verify/rollback pipeline unchanged; the decrypted temp folder is always cleaned up when installation finishes, success or failure.
- Silent install gains a `/PASSWORD=` command-line flag (matching the Inno Setup convention). A missing or wrong password aborts immediately with a non-zero exit code — no window, no hang.
- This is access control (guarding against an installer being redistributed or misused), not a hardening measure against a determined attacker — wrong-password attempts are not rate-limited by design.

Also fixes two accessibility gaps discovered while building the password screen (verified with real screenshots and simulated Tab-key navigation, not just code review):
- `.nice-btn` and the password input had `outline: none` with no replacement — keyboard users tabbing through the installer had no visible focus indicator at all. Replaced with `:focus-visible` rings.
- The top-left close button was a `<div onclick>`, which is not in the Tab order and has no keyboard activation — keyboard users could not close the window at all. Now a real focusable, `role="button"` element with Enter/Space support.

### Improvements

**Architecture cleanup from the codebase audit**

- Config-schema single source of truth: `packaging_core.py`'s `windows_service` start-type validation now reads from `windows_service.VALID_START_TYPES` instead of an independently hardcoded copy; built-in dependency key checks now derive from `dependency_defs.BUILT_IN_DEPENDENCIES` instead of two separate hardcoded literal sets. `builder_cli.py`'s `init` template gained the `windows_service`/`scheduled_task`/`create_restore_point_before_install`/`dependencies_min_version` fields it had been missing.
- New `tests/test_js_api_contract.py` statically cross-checks every `pywebview.api.*` call in `ui/*.html` against the corresponding Python API class's actual methods — catches a renamed/removed method that previously would only surface when a user clicked the broken button.
- New `install_journal.py` (`InstallJournal.record()`/`unwind()`): Windows service/scheduled task rollback now uses this pattern instead of individual `windows_service_name`/`scheduled_task_name` flag parameters threaded through the rollback call chain. Also removed a batch of dead parameters from `_trigger_installation_impl_inner()` that were being silently shadowed by local reassignment.
- New `docs/adr/0001` records the decision to apply the above journal pattern only to the two newest rollback categories (Windows service/scheduled task) this round, leaving the four older categories (registry/shortcuts/file associations/PATH) on their existing flag-based approach — so a future audit doesn't need to re-litigate the same question.

### Documentation

**Repository-wide documentation reorganization**

- 14 markdown files scattered across the repo root are now organized: only project-standard files (`README.md`, `CLAUDE.md`, `CONTEXT.md`, `CLI_USAGE.md`, `COMMIT_CONVENTION.md`) remain at root. Everything else moved into `docs/`, split by kind: `docs/規格文件.md` / `docs/使用說明書.md` (project-level reference), `docs/adr/` (settled individual decisions), `docs/proposals/` (not-yet-decided feature research, e.g. the MSIX packaging investigation), `docs/releases/` (this file and its predecessors — frozen historical snapshots, not updated retroactively when paths change).
- Corrected a stale claim in `README.md` ("no multi-language UI yet") — the installer, uninstaller, and packaging tool have supported `zh-TW`/`en` UI chrome since v0.something; the actual current gap (documented in `docs/規格文件.md` §8.13) is 7 untranslated English strings in `ui/index.html`'s process-running/file-locked/older-version-detected dialogs, plus backend-generated progress/error text that bypasses the translation layer entirely.

### Testing

629 tests, all passing.

---

## 繁體中文

發布基準：`v0.13.1`（2026-08-12）→ `v0.14.0`。這個版本新增一項真正的使用者功能——選填的安裝密碼保護——外加一批先前架構稽核提出的內部程式碼改善，以及一次全 repo 範圍的文件整理。既有打包設定欄位沒有任何破壞性變更。

### 新增功能

**選填的安裝密碼保護**

新增 `install_password_env` 打包欄位，可以把安裝檔裡的應用程式檔案加密保護。開啟後：

- 整包 `app_contents` 會先用 AES-256-GCM（金鑰透過 PBKDF2 衍生）加密，才內嵌進安裝檔，透過新增的 `install_encryption.py` 模組（新增 `cryptography` 相依套件——這個專案第一個真正的第三方加密套件）。
- 使用者執行安裝檔時會先看到密碼畫面（出現在 EULA **之前**）。密碼正確會解密到一個暫存資料夾，接著沿用現有的複製/驗證/回滾流程，完全不用修改；不管安裝最後成功或失敗，這份解密暫存資料夾都會被清乾淨。
- 靜默安裝新增 `/PASSWORD=` 命令列旗標（比照 Inno Setup 既有慣例）。缺少密碼或密碼錯誤會立刻中止並回傳非 0 exit code——不開視窗、不卡住。
- 這個功能的定位是存取控制（防止安裝檔被誤傳/亂用），不是防範有心人的資安強化措施——密碼錯誤刻意不限制重試次數。

順手修正了兩個做密碼畫面時發現的無障礙缺陷（用實際截圖 + 模擬 Tab 鍵驗證過，不只是看程式碼）：
- `.nice-btn` 跟密碼輸入框原本 `outline: none` 拿掉了鍵盤焦點框卻沒有補替代標記——鍵盤使用者 Tab 過安裝畫面時完全看不出焦點在哪。改用 `:focus-visible` 補上。
- 左上角關閉鈕原本是 `<div onclick>`，不在 Tab 順序裡也沒有鍵盤觸發方式——鍵盤使用者根本按不到。現在是真正可聚焦、帶 `role="button"` 且支援 Enter/Space 的元素。

### 改善

**架構稽核帶來的內部程式碼改善**

- Config schema 單一真實來源：`packaging_core.py` 的 `windows_service` start_type 驗證改從 `windows_service.VALID_START_TYPES` 讀取，不再自己另外寫死一份；內建相依元件 key 的判斷改從 `dependency_defs.BUILT_IN_DEPENDENCIES` 動態算出，取代原本兩份各自寫死的常數。`builder_cli.py` 的 `init` 範本補上原本漏列的 `windows_service`/`scheduled_task`/`create_restore_point_before_install`/`dependencies_min_version` 欄位。
- 新增 `tests/test_js_api_contract.py`，靜態比對 `ui/*.html` 每一個 `pywebview.api.*` 呼叫跟對應 Python API class 的實際方法——抓出「方法被改名/刪除」這種原本要等使用者點到壞掉的按鈕才會發現的落差。
- 新增 `install_journal.py`（`InstallJournal.record()`/`unwind()`）：Windows 服務/排程工作的安裝失敗回滾改用這個模式，取代原本一路傳遞 `windows_service_name`/`scheduled_task_name` 個別旗標參數的寫法。順便清掉 `_trigger_installation_impl_inner()` 一批從外層傳入、實際上會被內部重新賦值悄悄蓋掉的死參數。
- 新增 `docs/adr/0001`，記錄「這輪只在最新的兩類回滾（Windows 服務/排程工作）套用上面的 journal 模式，registry/捷徑/檔案關聯/PATH 這四類既有的回滾邏輯維持原本的旗標寫法」這個決定，避免以後的稽核重新論證同一個問題。

### 文件

**全 repo 範圍的文件整理**

- 原本散落在 repo 根目錄的 14 個 markdown 檔案重新整理：根目錄只留標準檔（`README.md`/`CLAUDE.md`/`CONTEXT.md`/`CLI_USAGE.md`/`COMMIT_CONVENTION.md`），其餘依性質搬進 `docs/`：`docs/規格文件.md`/`docs/使用說明書.md`（專案級別參考文件）、`docs/adr/`（已定案的個別決策）、`docs/proposals/`（還沒拍板的功能研究，例如 MSIX 打包規劃）、`docs/releases/`（本檔案跟歷史版本——內容凍結，路徑變動不會回頭更新）。
- 修正 `README.md` 裡一句過時的說法（「目前沒有多語言介面」）——安裝檔、解除安裝程式、打包工具其實從某個版本開始就支援 `zh-TW`/`en` 介面 chrome；真正還存在的落差（記錄在 `docs/規格文件.md` §8.13）是 `ui/index.html` 有 7 個字串沒翻成英文（程式正在執行/檔案使用中/偵測到較新版本這三組畫面），以及後端動態產生的進度/錯誤文字完全沒有納入這套多語言機制。

### 測試

629 個測試，全數通過。

---

## Full commit list / 完整變更（commit）

```
27e4c34 feat: 新增安裝密碼保護（optional install password）功能
a5eda1d refactor: 架構改善——config schema 單一來源、js_api 契約檢查、install journal
```
