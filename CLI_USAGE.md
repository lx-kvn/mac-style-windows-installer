# 打包工具 — CLI 使用說明

這份文件講的是打包工具的**命令列版本**（`builder_cli.py`，之後打包成
`mac-style-windows-installer_CLI_vX.Y.Z.exe`）：不需要開任何視窗，純靠指令
把一個應用程式資料夾打包成 macOS 風格拖曳安裝視窗的 Setup exe。

跟圖形介面版本（`InstallerBuilder.exe`/`gui_config.py`，見
[`使用說明書.md`](使用說明書.md)）共用完全相同的驗證跟編譯邏輯
（`packaging_core.py`/`builder.py`），差別只在「資料從哪裡來」（這裡是
JSON 設定檔 + 命令列參數，不是表單）跟「進度怎麼呈現」（印到終端機，
不是視窗裡的進度條）。兩邊產出的安裝檔完全等價。

---

## 環境需求

跟圖形介面版本一樣：

```
pip install pyinstaller pywebview pywin32
```

`pyinstaller`、`pywebview` 是硬性需求（`pack` 子指令執行前會自動檢查，
沒裝會印出缺什麼、非零 exit code 結束，不會編到一半才失敗）；`pywin32`
選用，只影響「建立開始功能表/桌面捷徑」這個功能。

**注意**：`builder_cli.py` 這支檔案本身完全不需要安裝 `pywebview` 就能
執行（跟 GUI 版的 `gui_config.py` 不同）——`pywebview` 是編譯出來的
`Setup_XXX.exe` 需要的執行環境，`check_build_environment()` 檢查的是
系統上的 `python` 直譯器有沒有裝它，不是 `builder_cli.py` 自己的執行環境。

---

## 兩個子指令

### `init`：產生範本設定檔

```
python builder_cli.py init [--output installer_pack_config.json]
```

在指定路徑（預設 `installer_pack_config.json`）產生一份帶預留位置的
JSON 範本，把裡面的值改成你自己的即可。JSON 沒有註解語法，範本裡的值
本身就是提示（例如 `"app_name": "MyCustomApp"` 提示這裡要填應用程式
名稱）。

### `pack`：驗證並編譯

```
python builder_cli.py pack --config app.json [--其他 flag 覆蓋個別欄位...]
```

- `--config`：JSON 設定檔路徑，選填。完全不給的話，所有欄位都要靠底下
  的 flag 補齊。
- **JSON 是底，命令列 flag 有帶值就覆蓋 JSON 裡對應的欄位**——這樣可以
  維護一份基礎設定檔，CI 或不同情境下用 flag 微調個別欄位（例如版本號），
  不用複製整份 JSON 改一個值。

驗證失敗或編譯過程出錯，訊息會印到 stderr，process exit code 非零；
成功的話印出安裝檔的完整路徑，exit code 是 0。

---

## 欄位對照表

以下欄位可以寫在 JSON 設定檔裡，也可以用對應的命令列 flag 覆蓋
（flag 名稱把底線換成連字號，例如 `app_name` 對應 `--app-name`）。
**這是「打包時的輸入」，跟打包完產生、內嵌進安裝檔裡的
`installer_config.json`（見 `規格文件.md` §5.1）是兩個不同的東西**——
這份是你要「告訴打包工具做什麼」，那份是打包工具「做完之後留下的紀錄」，
兩者欄位大致對應但用途不同，不要搞混。

| JSON 鍵 | 對應 flag | 必填 | 說明 |
|---|---|---|---|
| `app_dir` | `--app-dir` | 是 | 應用程式內容資料夾 |
| `png_icon` | `--png-icon` | 是 | 拖拽介面用的 PNG 圖示 |
| `ico_icon` | `--ico-icon` | 是 | 安裝檔封面用的 ICO 圖示 |
| `doc_icon` | `--doc-icon` | 否 | 檔案關聯的自訂文件圖示（ICO），套用到所有 `file_associations` 裡沒有在 `doc_icons` 個別指定的副檔名；有填就等同 GUI 版「勾選自訂文件圖示」 |
| `doc_icons` | （只能透過 JSON） | 否 | 字典 `{副檔名: ICO 路徑}`，讓不同副檔名各自套用不同圖示（例如 `.a` 用一張、`.b` 用另一張），優先於 `doc_icon`；沒列出的副檔名 fallback 用 `doc_icon`，兩者都沒有就沿用主程式圖示。跟 `eula_texts` 一樣是字典結構，沒有對應的命令列 flag |
| `app_name` | `--app-name` | 是 | 顯示給使用者看的應用程式名稱，可以是中文 |
| `folder_name` | `--folder-name` | 否 | 安裝路徑用的名稱，建議英數字，留空沿用 `app_name` |
| `version` | `--version` | 是 | 應用程式版本號，例如 `1.0.0` |
| `publisher` | `--publisher` | 是 | 軟體發行者/開發商 |
| `exe_name` | `--exe-name` | 是 | 最終輸出的安裝檔檔名（不含 `.exe`） |
| `main_exe` | `--main-exe` | 是 | 主要執行檔，相對於 `app_dir` 的路徑 |
| `eula_texts` | （只能透過 JSON） | 否 | 多語言 EULA，語言代碼對應文字的字典，例如 `{"zh-TW": "...", "en": "..."}`；空字典代表不顯示同意頁 |
| `eula_default_lang` | `--eula-default-lang` | `eula_texts` 非空時必填 | 終端使用者系統語言沒有對應版本時的回退語言 |
| `dependencies` | `--dependencies` | 否 | 逗號分隔，可用值：`vcredist_x64`、`dotnet_desktop` |
| `file_associations` | `--file-associations` | 否 | 逗號分隔的副檔名，例如 `.xyz,.abc`；有填就等同 GUI 版「勾選需要註冊檔案關聯」 |
| `add_to_path` | `--add-to-path` / `--no-add-to-path` | 否 | 安裝後是否把路徑加入環境變數 PATH |
| `path_target_exe` | `--path-target-exe` | 否 | `add_to_path` 開啟時，指定只把這支執行檔所在的目錄加入 PATH（不填就是整個安裝目錄，見規格文件 §8.14） |
| `local_appdata_files` | `--local-appdata-files` | 否 | 逗號分隔，相對於 `app_dir` 的路徑，指定這些檔案改裝到 `%LOCALAPPDATA%\Programs\<folder_name>`（使用者自己的目錄，不需要系統管理員權限）而不是主安裝目錄；典型用途是跟主程式分開的 CLI 工具，讓使用者事後單純執行它不用每次都提權。如果 `path_target_exe` 也列在這裡，加進 PATH 的會自動變成這個別位目錄，見規格文件 §8.19 |
| `restart_explorer_on_update` | `--restart-explorer-on-update` / `--no-restart-explorer-on-update` | 否 | 更新覆蓋安裝時是否暫時關閉檔案總管釋放被鎖定的檔案（見規格文件 §8.12） |

`eula_texts` 是字典結構，只能透過 JSON 設定檔提供，沒有對應的命令列 flag
（塞一整包多語言文字進命令列參數不實際）。

---

## 範例

```bash
# 1. 產生範本
python builder_cli.py init --output myapp.json

# 2. 編輯 myapp.json，把 app_dir/png_icon/ico_icon/app_name/... 填好

# 3. 編譯（純靠 JSON）
python builder_cli.py pack --config myapp.json

# 4. 編譯，但這次用不同版本號（CLI flag 覆蓋 JSON 裡的 version）
python builder_cli.py pack --config myapp.json --version 1.1.0

# 5. 完全不用 JSON，純靠 flag（欄位比較少的簡單情境）
python builder_cli.py pack \
  --app-dir "C:\MyApp" --png-icon icon.png --ico-icon icon.ico \
  --app-name "MyApp" --version 1.0.0 --publisher "Acme" \
  --exe-name Setup_MyApp --main-exe MyApp.exe
```

---

## `build_config_tool.py --cli`：把打包工具自己編譯成 exe

上面講的是拿 `builder_cli.py`（打包工具的 CLI 版本）去打包**別人的應用
程式**。如果你要的是重新編譯**打包工具自己**（GUI 版 `gui_config.py` +
CLI 版 `builder_cli.py`）成獨立的 exe，用的是另一支腳本：

```
python build_config_tool.py --cli [--version X.Y.Z] [--icon icon.ico]
```

不開任何視窗，依序編譯出：

- `mac-style-windows-installer_GUI_vX.Y.Z.exe`（進入點 `gui_config.py`）
- `mac-style-windows-installer_CLI_vX.Y.Z.exe`（進入點 `builder_cli.py`）

`--version` 沒帶就讀取 repo 根目錄的 `VERSION` 檔案。這支指令主要是給
`/released` 這類自動化發布流程呼叫，一般開發者手動編譯打包工具，用不帶
參數的互動模式（`python build_config_tool.py`，跳出 Tkinter 視窗）通常
更方便。

---

## 裝好之後，在 CMD 裡怎麼呼叫

跑完官方發布的 `Setup_mac-style-windows-installer_vX.Y.Z.exe`（`/released`
skill 產出的安裝檔）之後，這支 CLI 工具會被加進系統環境變數 PATH，可以
直接在**任何目錄**開新的 CMD／PowerShell 視窗打指令，不需要 `python`、
不需要 `cd` 到安裝目錄、也不用打完整路徑：

```cmd
mswi-cli init --output myapp.json
mswi-cli pack --config myapp.json
```

**指令名稱固定是 `mswi-cli`，不含版本號，不會因為升級版本而改變**——
這是刻意的設計：安裝時實際複製到電腦上的執行檔，檔名已經從建置產物
的 `mac-style-windows-installer_CLI_vX.Y.Z.exe` 改成固定的 `mswi-cli.exe`
（GUI 版對應是 `mswi-gui.exe`，用雙擊/開始功能表捷徑，不是給 CMD 叫的），
所以就算之後裝新版本覆蓋更新，寫在腳本、CI、工作排程器裡呼叫
`mswi-cli` 的指令完全不用跟著改。

**跟 `python builder_cli.py ...` 有什麼不一樣**：純粹是啟動方式不同，
底層邏輯完全共用（`packaging_core.py`）——沒有裝 Python 環境、只想單純
執行編譯打包的使用者，裝好安裝檔後直接打 `mswi-cli` 即可；上面幾節講的
`init`/`pack` 子指令、JSON 欄位對照表、CLI flag 覆蓋規則，`mswi-cli`
全部原封不動適用，只是把指令開頭的 `python builder_cli.py` 換成
`mswi-cli`：

```cmd
mswi-cli pack --config myapp.json --version 1.1.0
```

**新開的 CMD 視窗才吃得到新加的 PATH**：如果安裝時 CMD／PowerShell
視窗已經開著，要先關掉重開一次，PATH 變更才會生效；已經開著的視窗
繼續打 `mswi-cli` 會出現「不是內部或外部命令」。
