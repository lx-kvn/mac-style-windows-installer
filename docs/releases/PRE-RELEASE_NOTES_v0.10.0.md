# v0.10.0 — Pre-release Notes

Baseline: `v0.9.0` (2026-08-02) → `v0.10.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

---

## English

This release lets file associations use a different icon per extension, instead of one shared icon for every registered extension.

### New Features

**Per-extension file association icons (`doc_icons`)**

Previously, the "custom document icon" setting was a single field: check the box, pick one ICO, and every registered extension used that same icon. If an application registers several extensions representing different kinds of files (e.g. `.a` a project file, `.b` an exported file), users would want to tell them apart at a glance in File Explorer — which a single shared icon can't do.

A new optional field, `doc_icons` (GUI: a dynamic per-extension picker under "custom document icon"; CLI/JSON only — a dict, same reasoning as `eula_texts`, since a dict doesn't map cleanly to a single command-line flag), lets you assign a distinct ICO to each extension. Extensions not listed fall back to the shared `doc_icon`, and if that's not set either, to the main executable's icon.

Under the hood: each extension already has its own independent registry ProgID and `DefaultIcon` subkey (`file_assoc.py`'s `prog_id()`), so per-extension icons were always structurally possible — this just removes the caller-side assumption that all extensions share one icon. `file_assoc.register()`'s signature changed from a single `icon_ref` string to an `icon_refs` dict; `installer_core.py`'s `_resolve_doc_icon_ref(main_exe_path, ext)` resolves the fallback chain per extension, and the packaging step (`builder.py`) embeds each custom icon under a fixed name (`doc_icon_<ext>.ico`) so multiple source files with the same original name never collide.

This release packages this project's own two Builder Tool executables (`mswi-gui`/`mswi-cli`) using the CLI-relocation feature introduced in v0.9.0 (`local_appdata_files`) — `mswi-cli.exe` now actually installs to `%LOCALAPPDATA%\Programs\mac-style-windows-installer`, since v0.9.0 shipped the capability but the project's own release packaging step hadn't been wired up to use it yet.

### Testing

202 tests (up from 190), all passing — 12 new tests cover the icon-resolution fallback chain, `packaging_core.py` validation of `doc_icons`, and `builder.py`'s multi-icon embedding.

---

## 繁體中文

發布基準：`v0.9.0`（2026-08-02）→ `v0.10.0`。本輪讓檔案關聯可以幫不同副檔名各自套用不同圖示，不用再全部共用同一張。

### 新增功能

**檔案關聯可以幫不同副檔名各自設定不同圖示（`doc_icons`）**

原本「自訂文件圖示」是單一欄位：勾選、選一張 ICO，所有註冊的副檔名就全部共用這張圖示。如果應用程式關聯了多種副檔名、各自代表不同類型的檔案（例如 `.a` 是專案檔、`.b` 是匯出檔），使用者會希望在檔案總管裡一眼就能靠圖示分辨——單一共用圖示做不到這件事。

新增選填欄位 `doc_icons`（GUI：「自訂文件圖示」底下新增一個依副檔名動態產生的圖示選擇區；CLI/JSON only——字典結構，跟 `eula_texts` 一樣的理由，字典塞進單一命令列參數不實際），可以幫每個副檔名各自指定一張 ICO。沒列出的副檔名 fallback 用共用的 `doc_icon`，兩者都沒設定就沿用主程式圖示。

底層設計：每個副檔名本來就有自己獨立的登錄表 ProgID 跟 `DefaultIcon` 子機碼（`file_assoc.py` 的 `prog_id()`），結構上一直都支援各自設定圖示——這次只是拿掉呼叫端「所有副檔名共用一張圖示」的假設。`file_assoc.register()` 的簽名從單一 `icon_ref` 字串改成 `icon_refs` 字典；`installer_core.py` 的 `_resolve_doc_icon_ref(main_exe_path, ext)` 幫每個副檔名算 fallback 順序；打包階段（`builder.py`）把每張自訂圖示各自用固定命名（`doc_icon_<副檔名>.ico`）內嵌，避免不同副檔名剛好選了同名但內容不同的原始檔案互相覆蓋。

這次也把這個專案自己的兩顆打包工具執行檔（`mswi-gui`/`mswi-cli`）套用了 v0.9.0 就已經做出來的 CLI 改裝功能（`local_appdata_files`）——`mswi-cli.exe` 這次真的會裝到 `%LOCALAPPDATA%\Programs\mac-style-windows-installer`，因為 v0.9.0 雖然做出了這個能力，但當時這個專案自己的發布打包步驟還沒接上去用。

### 測試

202 個測試（從 190 個增加），全數通過——12 個新測試涵蓋圖示解析的 fallback 順序、`packaging_core.py` 對 `doc_icons` 的驗證，以及 `builder.py` 多張圖示的內嵌邏輯。

---

## Full commit list / 完整變更（commit）

```
（自 v0.9.0（b18aaef）之後沒有其他 commit，本輪內容全部包在這次
「chore: 發布 v0.10.0」commit 裡：新增 doc_icons 功能，涵蓋
file_assoc.py、installer_core.py、builder.py、packaging_core.py、
gui_config.py、builder_cli.py、ui/config.html 與對應測試）
```
