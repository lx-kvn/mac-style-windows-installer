# dmg-style-installer-builder

**A tool that packages any Windows application into a macOS-DMG-style drag-to-install experience.**
**把任何 Windows 應用程式打包成 macOS DMG 風格拖曳安裝體驗的工具。**

![platform](https://img.shields.io/badge/platform-Windows-blue)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![status](https://img.shields.io/badge/status-pre--release-orange)

**Language: [English](#english) | [繁體中文](#繁體中文)**

---

## English

### What is this?

This project has two parts:

1. **The Builder Tool** — a desktop app (GUI) you use to configure and compile an installer for *your own* application.
2. **The Output Installer** — the `.exe` the Builder Tool produces, which your end users double-click. It shows a macOS-style window: drag your app's icon onto a folder icon to install it — instead of a typical Windows "Next, Next, Next" wizard.

Both the Builder Tool and the installers it produces are standalone Windows desktop apps, built with Python + [pywebview](https://pywebview.flowrl.com/) (for the UI) and packaged with [PyInstaller](https://pyinstaller.org/).

> **⚠️ This project was built with significant assistance from Claude (Anthropic's AI).** Architecture decisions, code, and this README were developed in collaboration with AI. If that matters to you when evaluating a project, now you know. Bug reports and code review are very welcome — AI-assisted doesn't mean bug-free.

### Screenshots


| Builder Tool — Main Screen | Environment Check |
|---|---|
| ![Builder main screen](docs/screenshots/builder-main.png) | ![Environment check](docs/screenshots/env-check.png) |

| Installer — Drag to Install | EULA Screen |
|---|---|
| ![Drag to install](docs/screenshots/installer-drag.png) | ![EULA screen](docs/screenshots/eula.png) |

| Build Progress |
|---|
| ![Build progress](docs/screenshots/build-progress.png) |

### Features

**Building the installer (Builder Tool)**
- Configure app name (display name, can be any language) separately from the install-folder name (recommended ASCII-safe)
- Pick a main executable, PNG/ICO icons, EULA text (optional), dependency hints (VC++ Redistributable / .NET Desktop Runtime — detection only, not silent install), file associations, PATH registration
- Environment check on launch — warns if `pyinstaller` / `python` / `pywebview` aren't found on the machine building the installer, with install commands
- Runnable both as a raw `.py` script and as a compiled `.exe` (see [Requirements](#requirements) below)
- Real-time build progress with staged, non-linear animation (not a fake linear bar)

**The installer it produces**
- macOS DMG-style drag-to-install window, custom-drawn window drag (no native-drag jump bug), DPI-aware rendering
- EULA screen (skipped if not configured)
- Existing-installation detection → silent upgrade flow
- Disk space check, running-process check, single-instance lock
- Real copy progress + **post-copy integrity verification (CRC32 checksum, not just file size)**
- **Automatic rollback on failure** — a failed install cleans up after itself, no half-installed leftovers
- Desktop / Start Menu shortcuts, file associations, PATH registration
- Registry entries for "Apps & Features": `DisplayName`, `Publisher`, `DisplayVersion`, `InstallLocation`, `EstimatedSize`, `InstallDate`, `UninstallString`, `QuietUninstallString`
- **Silent / CLI install mode** for enterprise deployment: `Setup_XXX.exe /S /D=C:\Apps\MyApp /NODESKTOPSHORTCUT`, exit code reflects success/failure
- Manifest-based uninstaller — only removes what it installed, preserves user-generated files in the install folder; falls back to full-folder cleanup only when no manifest is found

### Requirements

To **run/build the Builder Tool** (`gui_config.py` or a compiled `InstallerBuilder.exe`), the machine needs:

```
pip install pyinstaller pywebview pywin32
```

- `pyinstaller` and `pywebview` are required — the Builder Tool checks for them on launch and warns you if missing.
- `pywin32` is optional — only affects shortcut creation.

The **installers it produces** are fully standalone — end users don't need Python installed. The only external dependency is the **WebView2 Runtime** (a Windows system component, usually pre-installed on Windows 11 and updated Windows 10; may be missing on older/un-updated Windows 10 machines).

### Usage

1. Install the requirements above.
2. Run `python gui_config.py` (or double-click `InstallerBuilder.exe` if you've built it — see below).
3. Fill in the form: app name, folder name, version, publisher, output filename, app folder, main executable, icons, and any optional settings.
4. Click "Start Building" (開始編譯安裝檔). The output `.exe` lands in `dist/`.

To build `InstallerBuilder.exe` itself:

```
python build_config_tool.py
```

This opens a small build GUI where you can pick a custom `.ico` and watch the PyInstaller output live.

Full documentation: see [`使用說明書.md`](使用說明書.md) (Traditional Chinese).

### Roadmap

- [ ] Code signing for `InstallerBuilder.exe` via [SignPath Foundation](https://signpath.io/solutions/open-source-community)'s free OSS program (requires this repo + GitHub Actions build pipeline — the compiled installers it *produces* would still be unsigned, since they're built locally, not through this repo's CI)
- [ ] Multi-language support — **not started, feasibility TBD.** Low priority unless there's actual demand from non-Chinese-speaking users; see discussion in project history for the reasoning
- [ ] Optional hash upgrade for integrity verification (currently CRC32; a stronger hash could be offered for higher-assurance use cases)
- [ ] Digital-signature pass-through for the *output* installers (would require moving the build step into CI, a bigger architectural change)

### Known Limitations

- No digital signature yet on either the Builder Tool or the installers it produces (see Roadmap)
- Dependency checks (VC++ Redistributable, .NET Desktop Runtime) are detection-only — no silent install of the dependency itself
- No multi-language UI yet — everything is in Traditional Chinese
- Requires WebView2 Runtime on very old/un-updated Windows 10 machines

### License

MIT — see [`LICENSE`](LICENSE).

---

## 繁體中文

### 這是什麼

這個專案分成兩個部分：

1. **打包工具**——一個桌面 GUI 應用程式，你用它來設定、編譯出**你自己軟體**的安裝檔。
2. **輸出的安裝檔**——打包工具編譯出來、給你的終端使用者雙擊的那顆 `.exe`。畫面是 macOS 風格：把你的軟體圖示拖到資料夾圖示上就完成安裝，而不是傳統 Windows「下一步、下一步、下一步」那種精靈式安裝。

打包工具本身跟它輸出的安裝檔，都是完全獨立的 Windows 桌面應用程式，用 Python + [pywebview](https://pywebview.flowrl.com/)（畫面）搭配 [PyInstaller](https://pyinstaller.org/)（打包成 exe）做出來的。

> **⚠️ 這個專案是在 Claude（Anthropic 的 AI）大量協助下完成的。** 架構決策、程式碼、連這份 README 本身，都是跟 AI 協作寫出來的。如果這件事會影響你對這個專案的判斷，先讓你知道。歡迎回報 bug、歡迎 code review——AI 協助不等於沒有 bug。

### 截圖


| 打包工具主畫面 | 環境檢查 |
|---|---|
| ![打包工具主畫面](docs/screenshots/builder-main.png) | ![環境檢查](docs/screenshots/env-check.png) |

| 安裝端拖曳安裝畫面 | EULA 同意頁 |
|---|---|
| ![拖曳安裝](docs/screenshots/installer-drag.png) | ![EULA](docs/screenshots/eula.png) |

| 編譯進度 |
|---|
| ![編譯進度](docs/screenshots/build-progress.png) |

### 功能

**製作安裝檔(打包工具端)**
- 應用程式顯示名稱（可以是任何語言）跟安裝資料夾名稱（建議英數字）分開設定
- 選主要執行檔、PNG/ICO 圖示、EULA 文字（選填）、相依元件提示（VC++ Redistributable / .NET Desktop Runtime，只做偵測不做靜默安裝）、檔案關聯、加入 PATH
- 開啟時自動環境檢查——沒裝 `pyinstaller` / `python` / `pywebview` 會直接跳提示附安裝指令
- **`.py` 直接執行跟編譯成 `.exe` 都能用**（見下方〈環境需求〉）
- 即時編譯進度，分階段的漸進動畫（不是假的線性進度條）

**輸出的安裝檔**
- macOS DMG 風格拖曳安裝視窗，自訂拖曳邏輯（不用原生拖曳，沒有跳動 bug），DPI 感知渲染
- EULA 同意頁（沒設定就自動跳過）
- 偵測已安裝版本 → 靜默更新覆蓋流程
- 磁碟空間檢查、執行中偵測、單一實例鎖
- 真實複製進度 + **複製後完整性驗證（CRC32 checksum，不只是比檔案大小）**
- **失敗自動回滾**——安裝失敗會自動清乾淨，不留下裝到一半的殘骸
- 桌面/開始功能表捷徑、檔案關聯、加入 PATH
- 「新增或移除程式」清單的完整登錄表欄位：`DisplayName`、`Publisher`、`DisplayVersion`、`InstallLocation`、`EstimatedSize`、`InstallDate`、`UninstallString`、`QuietUninstallString`
- **靜默 / 命令列安裝模式**，給企業批次部署用：`Setup_XXX.exe /S /D=C:\Apps\MyApp /NODESKTOPSHORTCUT`，exit code 直接反映成功或失敗
- 清單式解除安裝——只刪自己裝的東西，保留使用者在安裝目錄裡自己產生的檔案；找不到清單才會退回整個資料夾清除

### 環境需求

**執行/打包「打包工具」**（`gui_config.py` 或編譯好的 `InstallerBuilder.exe`）的這台電腦需要：

```
pip install pyinstaller pywebview pywin32
```

- `pyinstaller`、`pywebview` 是必要的——打包工具開啟時會檢查，沒裝會跳提示。
- `pywin32` 選用，只影響捷徑功能。

**打包工具輸出的安裝檔**是完全獨立的，終端使用者不需要裝 Python。唯一的外部依賴是 **WebView2 Runtime**（Windows 系統元件，Windows 11 跟更新過的 Windows 10 通常已內建，較舊、沒更新的 Windows 10 可能會缺）。

### 使用方式

1. 安裝上面的環境需求。
2. 執行 `python gui_config.py`（或雙擊編譯好的 `InstallerBuilder.exe`，見下方）。
3. 填表單：應用程式名稱、資料夾名稱、版本、發行者、輸出檔名、應用程式資料夾、主執行檔、圖示，以及其他選填設定。
4. 按「開始編譯安裝檔」，輸出的 `.exe` 會在 `dist/` 資料夾底下。

要打包 `InstallerBuilder.exe` 本身：

```
python build_config_tool.py
```

會跳出一個小型建置 GUI，可以選自訂 `.ico`、即時看 PyInstaller 輸出。

完整文件：見 [`使用說明書.md`](使用說明書.md)。

### 未來方向

- [ ] 幫 `InstallerBuilder.exe` 透過 [SignPath Foundation](https://signpath.io/solutions/open-source-community) 的免費開源方案做數位簽章（需要這個 repo 本身 + GitHub Actions 建置流程；它動態產生出來的安裝檔因為是本機編譯、不經過這個 repo 的 CI，暫時還是簽不到）
- [ ] 多語言支援——**還沒開始，可行性待評估。** 除非真的有非中文使用者提出需求，不然優先度偏低，理由詳見專案討論過程
- [ ] 完整性驗證的雜湊演算法升級選項（目前是 CRC32，未來可以考慮給更高安全需求的情境提供更強的雜湊）
- [ ] 讓「輸出的安裝檔」也能被簽章（需要把編譯流程搬進 CI，是比較大的架構調整）

### 已知限制

- 打包工具本身跟它輸出的安裝檔目前都還沒有數位簽章（見〈未來方向〉）
- 相依元件檢查（VC++ Redistributable、.NET Desktop Runtime）只做偵測，不會靜默安裝該元件本身
- 目前沒有多語言介面，全部是繁體中文
- 非常舊、沒更新過的 Windows 10 可能需要另外安裝 WebView2 Runtime

### 授權

MIT——見 [`LICENSE`](LICENSE)。
