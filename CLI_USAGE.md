# 打包工具 — CLI 使用說明

這份文件講的是打包工具的**命令列版本**（`builder_cli.py`，之後打包成
`mac-style-windows-installer_CLI_vX.Y.Z.exe`）：不需要開任何視窗，純靠指令
把一個應用程式資料夾打包成 macOS 風格拖曳安裝視窗的 Setup exe。

跟圖形介面版本（`InstallerBuilder.exe`/`gui_config.py`，見
[`使用說明書.md`](docs/使用說明書.md)）共用完全相同的驗證跟編譯邏輯
（`packaging_core.py`/`builder.py`），差別只在「資料從哪裡來」（這裡是
JSON 設定檔 + 命令列參數，不是表單）跟「進度怎麼呈現」（印到終端機，
不是視窗裡的進度條）。兩邊產出的安裝檔完全等價。

---

## 環境需求

跟圖形介面版本一樣：

```
pip install pyinstaller pywebview pywin32 cryptography
```

`pyinstaller`、`pywebview` 是硬性需求（`pack` 子指令執行前會自動檢查，
沒裝會印出缺什麼、非零 exit code 結束，不會編到一半才失敗）；`pywin32`
選用，只影響「建立開始功能表/桌面捷徑」這個功能。

**注意**：`builder_cli.py` 這支檔案本身完全不需要安裝 `pywebview` 就能
執行（跟 GUI 版的 `gui_config.py` 不同）——`pywebview` 是編譯出來的
`Setup_XXX.exe` 需要的執行環境，`check_build_environment()` 檢查的是
系統上的 `python` 直譯器有沒有裝它，不是 `builder_cli.py` 自己的執行環境。

---

## 五個子指令

### `init`：產生範本設定檔

```
python builder_cli.py init [--output installer_pack_config.json]
```

在指定路徑（預設 `installer_pack_config.json`）產生一份帶預留位置的
JSON 範本，把裡面的值改成你自己的即可。JSON 沒有註解語法，範本裡的值
本身就是提示（例如 `"app_name": "MyCustomApp"` 提示這裡要填應用程式
名稱）。

### `list-files`：列出 app_dir 底下的檔案

```
python builder_cli.py list-files --app-dir C:\path\to\your\app
```

列出 `app_dir` 底下所有檔案的相對路徑（含子資料夾，一行一個），方便
在寫 `--local-appdata-files` 或 JSON 設定檔的 `local_appdata_files`
欄位之前，先查一下有哪些檔案可以選，不用自己土法煉鋼翻資料夾——跟
GUI 版「個別檔案改裝到其他位置」的分支圖勾選，共用同一份掃描邏輯。
路徑不存在或底下沒有檔案時會印出提示訊息，exit code 仍是 0。

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

另有兩個與 `--workspace-dir` 同一性質的旗標（描述「這台機器上的東西在
哪」，不描述要打包成什麼產品，因此是旗標而不是設定檔欄位；效力只及於
這一次執行，不寫進持久設定）：

- `--sdk-tools-dir`：手動指定 `makeappx`／`signtool` 所在目錄，覆蓋這次
  建置的自動檢索。指到解壓出來的套件根目錄或 Windows SDK 的安裝位置都
  可以，不必指到最底層的架構子目錄。
- `--sdk-tools-cache-dir`：覆蓋 `fetch-sdk-tools` 的快取位置，供 CI 把
  該目錄納入自己的快取機制。

### `pack-msix`：產出未簽章的 `.msix`

```
python builder_cli.py pack-msix --config app.json [--output 路徑]
```

只有 `install_engine` 是 `msix` 時能用。產出一顆**未簽章**的 `.msix`，預設放在工作目錄的 `dist/` 底下、檔名用套件身分名稱（不用 `app_name`，因為那是自由文字、可以是中文，不保證能當檔名）。

**為什麼這一步不順便簽章**：已簽章的 `.msix` 必須在編 bootstrapper exe之前備妥，而簽章可能由你的雲端代簽服務處理、不一定當場完成。把簽章綁進這個指令，等於讓雲端代簽那條路沒有容身之處。所以流程是兩截的——這個指令產出未簽章的套件，你自己拿去簽，再用 `pack --signed-msix` 編出安裝檔。

這顆 `.msix` 本身就有用途：可以直接給 winget、企業側載、或讓使用者用App Installer 雙擊安裝。

**注意 `pack` 目前還不能在 MSIX 引擎下用**——bootstrapper（內嵌 `.msix`並交給系統部署的那顆 `Setup.exe`）尚未實作。設定填 `msix` 去跑 `pack`會直接報錯並指向這個指令，不會默默產出一顆傳統安裝檔。

---

### `fetch-sdk-tools`：取得 Windows SDK 工具

```
python builder_cli.py fetch-sdk-tools [--cache-dir 目錄] [--force]
```

`signing`（Setup.exe 的數位簽章）需要 `signtool`，未來的 MSIX 輸出還會
需要 `makeappx`，兩者同屬 Windows SDK。**Windows SDK 安裝後不會把這些
工具加進 PATH**，而且也不需要為此安裝數 GB 的 SDK：這個子指令會下載一份
固定版本的 `Microsoft.Windows.SDK.BuildTools` NuGet 套件（約 22 MB）、
驗證 SHA-256、解壓到使用者層級的持久位置，之後打包流程自己找得到。解壓
出來的工具直接執行即可，不需要安裝程序、不需要系統管理員權限。

**打包流程不會自行執行這件事。** 判準不是「打包時是否連網」（`bundle_dependencies`
本來就會下載），而是下載物在打包機器上是被內嵌還是被**執行**——後者的
最壞情況是打包機器遭入侵，而打包機器通常存放簽章憑證。所以這是一個需要
你明確下達的指令，打包時找不到工具只會中止並把這行指令印給你複製。

- `--cache-dir`：覆蓋快取位置（供 CI 納入自己的快取機制）。預設在
  `%LOCALAPPDATA%\mac-style-windows-installer\sdk-tools\<版本>` 底下。
- `--force`：即使快取已存在也重新下載。

工具的來源優先序是**手動指定 → 這個指令下載的快取 → PATH → 系統上裝的
Windows SDK**，依「你表達這個意圖有多明確」排列。建置過程會印出本次
實際採用的來源與版本，用來診斷「兩台機器打包結果不同」這類問題。

---

## 欄位對照表

以下欄位可以寫在 JSON 設定檔裡，也可以用對應的命令列 flag 覆蓋
（flag 名稱把底線換成連字號，例如 `app_name` 對應 `--app-name`）。
**這是「打包時的輸入」，跟打包完產生、內嵌進安裝檔裡的
`installer_config.json`（見 `docs/規格文件.md` §5.1）是兩個不同的東西**——
這份是你要「告訴打包工具做什麼」，那份是打包工具「做完之後留下的紀錄」，
兩者欄位大致對應但用途不同，不要搞混。

| JSON 鍵 | 對應 flag | 必填 | 說明 |
|---|---|---|---|
| `install_engine` | `--install-engine` | 否 | 安裝檔內部用哪一種方式把應用程式檔案落地：`traditional`（預設，目前唯一可用）由 `Setup.exe` 自己複製檔案、寫登錄表；`msix` 改交給 Windows 的套件引擎，由系統保證解除安裝乾淨。**MSIX 引擎尚未實作**，目前填 `msix` 只會跑完設定相容性檢查然後中止——這是刻意的，設定寫著 MSIX 卻默默產出一顆傳統安裝檔比直接報錯糟得多。兩者的差異與各自的代價見 `CONTEXT.md`「傳統引擎與 MSIX 引擎」一節 |
| `msix` | （只能透過 JSON） | MSIX 模式必填 | MSIX 專屬設定，`install_engine` 為 `traditional` 時完全不會被檢查。三個欄位：`identity_name`（套件身分名稱，**一經發布即不可變更**，改了系統會當成另一個不相關的應用程式並存安裝；3–50 個字元，只能用英文字母、數字、句點、連字號；不由 `app_name` 推導，見 `docs/adr/0007`）、`certificate_subject`（寫進套件清單的發行者，必須與簽章憑證上記載的名稱完全一致——不一致時系統直接拒絕安裝，而且錯誤訊息不會指向這個原因。**設定了 `signing` 且憑證讀得到時，留空即可，工具會自動填入並印出來**；填了但跟憑證對不上會在打包階段就報錯。雲端代簽這種憑證不在本機的情況才需要自己填。注意那個字串的形式不直覺——順序是反的、分隔符是逗號加空格、值裡有逗號時要用雙引號包起來，例如 `C=TW, CN="Foo, Inc."`）、`min_windows_version`（最低支援的 Windows 版本，留空即採 `10.0.17763.0`＝Windows 10 1809） |
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
| `dependencies` | `--dependencies` | 否 | 逗號分隔，可用值：`vcredist_x64`、`dotnet_desktop`。安裝介面載入時會用登錄表偵測這些執行環境是否已安裝，缺少的話彈窗提示，使用者可以選「自動安裝」（從官方下載點下載安裝檔並靜默執行，裝完後重新偵測、切回拖曳頁面並顯示提示），也可以選「仍要繼續安裝」略過（不會阻擋主程式安裝，只是主程式之後可能無法正常執行）。見規格文件 §8.22 |
| `file_associations` | `--file-associations` | 否 | 逗號分隔的副檔名，例如 `.xyz,.abc`；有填就等同 GUI 版「勾選需要註冊檔案關聯」 |
| `add_to_path` | `--add-to-path` / `--no-add-to-path` | 否 | 安裝後是否把路徑加入環境變數 PATH |
| `path_target_exe` | `--path-target-exe` | 否 | `add_to_path` 開啟時，指定只把這支執行檔所在的目錄加入 PATH（不填就是整個安裝目錄，見規格文件 §8.14） |
| `local_appdata_files` | `--local-appdata-files` | 否 | 逗號分隔，相對於 `app_dir` 的路徑，指定這些檔案改裝到 `%LOCALAPPDATA%\Programs\<folder_name>`（使用者自己的目錄，不需要系統管理員權限）而不是主安裝目錄；典型用途是跟主程式分開的 CLI 工具，讓使用者事後單純執行它不用每次都提權。如果 `path_target_exe` 也列在這裡，加進 PATH 的會自動變成這個別位目錄，見規格文件 §8.19 |
| `custom_dependencies` | （只能透過 JSON） | 否 | 自訂相依元件清單，讓 `dependencies` 不再侷限於內建的 `vcredist_x64`/`dotnet_desktop`。每筆是一個物件：`key`（不可跟內建 key 撞名）、`display_name`、`download_url`（靜默安裝檔下載連結）、`silent_args`（靜默安裝命令列參數陣列）、`registry_check`（`{hive, path, value_name, expected}`，`value_name` 留空代表只檢查這個機碼是否存在）。跟 `eula_texts`/`doc_icons` 一樣是巢狀結構，沒有對應的命令列 flag。見規格文件 §8.23 |
| `bundle_dependencies` | `--bundle-dependencies` | 否 | 逗號分隔，列在 `dependencies`（或 `custom_dependencies`）裡的相依元件 key，打包當下就把安裝檔下載下來內嵌進 Setup.exe，安裝時不需要再連網下載（安裝檔會變大）。沒列在這裡的相依元件維持原本「安裝時才連網下載」的行為。見規格文件 §8.24 |
| `no_admin_install` | `--no-admin-install` / `--no-no-admin-install` | 否 | 開啟後整個安裝檔（含解除安裝）完全不要求系統管理員權限，不會跳出 UAC 提示：預設安裝路徑改成 `%LOCALAPPDATA%\Programs\<folder_name>`，解除安裝登錄表、PATH、捷徑都改寫到使用者層級（HKCU、`%APPDATA%`/`%USERPROFILE%\Desktop`）而不是系統層級。適合單一使用者自己安裝、不需要讓電腦上其他使用者共用的情境。見規格文件 §8.25 |
| `pre_install_script` / `post_install_script` | `--pre-install-script` / `--post-install-script` | 否 | 相對於 `app_dir` 的路徑，指向一支要在安裝前/安裝後自動靜默執行的腳本或執行檔（例如 `.bat`/`.exe`/`.ps1`）。前置腳本失敗會中止整個安裝並回報錯誤；後置腳本失敗只記錄警告，不影響安裝結果（此時主程式已經裝好）。見規格文件 §8.26 |
| `signing` | （只能透過 JSON） | 否 | 設定後打包時自動用 `signtool` 幫 Setup.exe/uninstall.exe 簽數位簽章：`{"cert_path": "憑證檔案(.pfx)路徑", "cert_password_env": "存放密碼的環境變數名稱", "timestamp_url": "時間戳記伺服器（選填，預設 DigiCert）"}`。密碼不放在設定檔明文裡，只存環境變數名稱；打包當下這個環境變數必須有值，簽章失敗會讓整個 `pack` 流程失敗。`signtool` 用 `fetch-sdk-tools` 子指令取得即可（也可以用系統上既有的 Windows SDK，或用 `--sdk-tools-dir` 指定），憑證要自行準備（本工具不提供、也無法生成憑證）。見規格文件 §8.27 |
| `windows_service` | （只能透過 JSON） | 否 | 安裝時額外把應用程式的某支執行檔註冊成 Windows 服務：`{"service_name": "服務名稱", "exe_relative_path": "相對於 app_dir 的執行檔路徑", "start_type": "auto/demand/disabled 其中之一，預設 auto"}`。`service_name`/`exe_relative_path` 要嘛兩個都填，要嘛都留空；`exe_relative_path` 指定的檔案必須真的存在於 `app_dir`。解除安裝時會自動移除這個服務。 |
| `scheduled_task` | （只能透過 JSON） | 否 | 安裝時額外把應用程式的某支執行檔註冊成排程工作：`{"task_name": "工作名稱", "exe_relative_path": "相對於 app_dir 的執行檔路徑", "trigger": "schtasks /sc 支援的觸發條件，預設 onlogon"}`。`task_name`/`exe_relative_path` 要嘛兩個都填，要嘛都留空；`exe_relative_path` 指定的檔案必須真的存在於 `app_dir`。解除安裝時會自動移除這個排程工作。 |
| `create_restore_point_before_install` | （只能透過 JSON） | 否 | 開啟後，安裝流程開始寫入檔案前，先嘗試建立一個系統還原點，讓使用者萬一想反悔可以透過 Windows 內建的系統還原整個復原（不是這個工具自己的解除安裝功能，是作業系統層級的還原點）。Windows 8 以後同一天內只會真的建立一次還原點（節流限制），短時間內重複安裝不保證每次都產生新的還原點；建立失敗（例如系統還原功能被使用者關閉）不會中止安裝，只是沒有還原點可用。 |
| `dependencies_min_version` | （只能透過 JSON） | 否 | 只對內建相依元件（`vcredist_x64`/`dotnet_desktop`）額外要求最低版本，例如 `{"dotnet_desktop": "8.0.0"}`；鍵一定要同時列在 `dependencies` 裡（沒啟用等於這個設定不會生效），也只能是內建的兩個 key（自訂相依元件的版本門檻改用 `custom_dependencies` 裡對應項目的 `registry_check.min_version`）。已安裝但版本低於這裡設定的門檻，會被當成「未安裝」，一樣走 `dependencies` 的偵測/自動安裝流程。 |
| `install_password_env` | `--install-password-env` | 否 | 設定後，打包出來的安裝檔會把應用程式檔案整包加密，使用者安裝時要先輸入正確密碼（畫面出現在 EULA 之前）才能繼續，密碼錯誤不限制重試次數。密碼本身不放在設定檔裡，只存存放密碼的環境變數名稱（比照 `signing.cert_password_env` 的做法）；打包當下這個環境變數必須有值，而且要裝好 `cryptography` 套件。靜默安裝（`/S`）另外用 `/PASSWORD=密碼` 帶密碼，缺少或密碼錯誤會直接中止並回傳非 0 exit code，不會跳出任何視窗。定位是存取控制（防止安裝檔被誤傳/亂用），不是防範有心人暴力破解的資安機制。見 CONTEXT.md「安裝密碼保護」一節。 |

### MSIX 模式的版本號

MSIX 要求版本號是**正好四段純數字**，跟這個工具平常接受的格式（一到四段數字、可帶預發布後綴，見 `docs/adr/0003`）不一樣：

- **三段以下自動補到四段**（`1.2.3` → `1.2.3.0`），這是無損轉換，不用你動手。
- **每一段不能超過 65535**。
- **帶預發布後綴的會直接報錯**（`1.0.0-rc1`）。不是懶得處理——捨棄後綴的話`1.0.0-rc1` 跟 `1.0.0` 的版本號會完全一樣，而 Windows 判斷要不要升級看的正是版本號有沒有遞增，系統會認定兩者是同一版而**不執行升級**。更麻煩的是這個問題在打包階段不會有任何錯誤，要到使用者實際升級失敗才會發現。傳統模式維持原樣，後綴照用。

### MSIX 引擎的設定相容性檢查

`install_engine` 填 `msix` 時，`pack` 會先檢查這份設定跟 MSIX 相不相容，再決定要不要往下走。跟其他欄位驗證有兩點不同：

- **一次列出全部**，不是遇到第一個問題就停。因為你要判斷的是「切換引擎對這個專案划不划算」，那個判斷需要完整清單——一條一條擠牙膏的話，你每修一次就要重跑一次 CI，而且到最後一條之前都不知道總代價有多大。
- **訊息分成兩段，語氣不同**，那決定你該怎麼辦：
  - 「目前尚未支援」＝MSIX 做得到，只是這個工具還沒做，可以等。
  - 「MSIX 無法做到，此為格式本身的限制」＝別等了，安裝流程要重新設計。

另有一類設定在 MSIX 下不會有作用、但**也不需要**（例如 `folder_name`、`local_appdata_files`——套件的位置由系統決定，而它們原本的目的在 MSIX 下本來就成立），這類不擋建置，只會在建置訊息裡說明為什麼沒有作用。

---

### 設定檔不支援直接寫入安裝密碼

指定安裝密碼只有 `install_password_env` 這一種寫法。設定檔裡如果出現
`install_password` 這個欄位（不論值是什麼、甚至留空），`pack` 會直接回報
欄位驗證失敗，不會默默忽略它。

配置精靈（GUI）另外提供「直接輸入密碼」這個選項，那條路的密碼以獨立參數
傳遞、不經過設定檔，所以命令列這邊沒有對應的寫法——這是決定，不是遺漏。
理由是設定檔會被存進專案、傳給同事、上傳到版本控制，密碼寫在裡面等於整個
保護失效，而這正是繞環境變數一圈要避開的事。完整取捨見
[`docs/adr/0004`](docs/adr/0004-inline-install-password-is-gui-only.md)。

需要重複打包或自動化流程時，環境變數本來就是比較合適的做法：不用每次重打
密碼，也能在 CI 裡用祕密管理機制注入。

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
python build_config_tool.py --cli [--version X.Y.Z] [--icon icon.ico] [--publisher 名稱]
```

不開任何視窗，依序編譯出：

- `mac-style-windows-installer_GUI_vX.Y.Z.exe`（進入點 `gui_config.py`）
- `mac-style-windows-installer_CLI_vX.Y.Z.exe`（進入點 `builder_cli.py`）

`--version` 沒帶就讀取 repo 根目錄的 `VERSION` 檔案。`--publisher` 選填，
會被寫進這兩顆 exe 的 Win32 VERSIONINFO 資源（`CompanyName`/
`LegalCopyright`，見 `version_info.py`），讓檔案總管「內容 → 詳細資料」
頁籤顯示正確內容而不是空白；沒帶就等於發行者留空。這支指令主要是給
`/released` 這類自動化發布流程呼叫，一般開發者手動編譯打包工具，用不帶
參數的互動模式（`python build_config_tool.py`，跳出 Tkinter 視窗）通常
更方便（互動模式目前沒有收集版本/發行者欄位，編出來的 exe 不會帶
VERSIONINFO 資源）。

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
