# CONTEXT.md

專案的領域詞彙表。跟 `docs/規格文件.md`（給接手工程師的完整交接文件）不同，這份只
記錄「概念叫什麼名字」，架構決策的來龍去脈見 `docs/規格文件.md` 跟 `docs/adr/`
（已定案的個別決策）；還沒拍板、仍在研究階段的功能規劃見 `docs/proposals/`
（例如 `MSIX輸出規劃.md`）。文件放置的完整慣例見 `CLAUDE.md`「文件放哪裡」一節。

## 目錄

- [檔案關聯（File Association）](#檔案關聯file-association)
- [使用者關聯覆寫（User Association Override）](#使用者關聯覆寫user-association-override)
- [registry seam](#registry-seam)
- [副檔名（file_extension.py）](#副檔名file_extensionpy)
- [拖曳安裝（Drag-to-Install）與視窗拖曳（Window Drag）](#拖曳安裝drag-to-install與視窗拖曳window-drag)
  - [ui/ 底下的共用前端檔案](#ui-底下的共用前端檔案)
- [深模組拆分（installer_core.py 瘦身）](#深模組拆分installer_corepy-瘦身)
- [多語系的兩層（安裝過程／安裝完成後）](#多語系的兩層安裝過程安裝完成後)
- [命令列別名（Execution Alias）](#命令列別名execution-alias)
- [安裝路徑與使用者範圍](#安裝路徑與使用者範圍)
- [InstallScope（no_admin_install 的 hive/目錄判斷）](#installscopeno_admin_install-的-hive目錄判斷)
- [跨 no_admin_install 模式的升級偵測與跨 UAC 呼叫](#跨-no_admin_install-模式的升級偵測與跨-uac-呼叫)
- [system_entries（登錄表項目/捷徑/PATH 的移除原語）](#system_entries登錄表項目捷徑path-的移除原語)
  - [移除原語的兩條共通規則](#移除原語的兩條共通規則)
- [編譯工作目錄（packaging_core.get_workspace_dir() / packaging_settings.py）](#編譯工作目錄packaging_coreget_workspace_dir--packaging_settingspy)
- [共用深模組的打包清單（packaging_core.ENTRY_SCRIPTS / SHARED_DEEP_MODULES）](#共用深模組的打包清單packaging_coreentry_scripts--shared_deep_modules)
- [self_delete（uninstall.exe 自我刪除）](#self_deleteuninstallexe-自我刪除)
- [explorer_lock_release（檔案鎖定釋放：分層策略）](#explorer_lock_release檔案鎖定釋放分層策略)
- [version_info（打包出來的 exe 帶上 Win32 VERSIONINFO 資源）](#version_info打包出來的-exe-帶上-win32-versioninfo-資源)
- [版本號格式（三個模組共用同一個定義）](#版本號格式三個模組共用同一個定義)
- [免管理員權限安裝與需要提權的選項互斥](#免管理員權限安裝與需要提權的選項互斥)
- [安裝密碼保護（Install Password Protection）](#安裝密碼保護install-password-protection)
  - [指定密碼的兩種填法](#指定密碼的兩種填法)
- [深模組拆分（第二輪：架構稽核 /improve-codebase-architecture）](#深模組拆分第二輪架構稽核-improve-codebase-architecture)
- [SDK 工具（SDK Tools）](#sdk-工具sdk-tools)
- [傳統引擎與 MSIX 引擎](#傳統引擎與-msix-引擎)
- [套件身分名稱（Package Identity Name）](#套件身分名稱package-identity-name)
- [WebView2 Runtime（webview2_runtime.py）](#webview2-runtimewebview2_runtimepy)

## 檔案關聯（File Association）

使用者設定的「某個副檔名要用主程式打開」這個功能，橫跨安裝（寫入）跟解除安裝
（清除）兩個方向。核心邏輯收斂在 `file_assoc.py`：

- **`register()`** — 安裝時的動作：把副檔名寫進登錄表指向主程式，同時盡量清掉
  使用者先前手動設定過的殘留（見下面「使用者關聯覆寫」）。
- **`unregister()`** — 解除安裝時的動作：對稱地清掉 `register()` 寫過的一切。
- **ProgID** — Windows 用來串起「副檔名 -> 開啟方式 -> 圖示」的登錄表識別字串，
  這個專案的命名慣例固定是 `AppFile{副檔名}`（例如 `.locked` -> `AppFilelocked`），
  由 `file_assoc.prog_id()` 產生，`register()`/`unregister()` 都靠這個對齊。

## 使用者關聯覆寫（User Association Override）

Windows 針對「使用者選過這個副檔名要用什麼開」記住的三層殘留，`register()`/
`unregister()` 都要對稱處理，不然新關聯不會真的生效：

1. **UserChoice** — `HKCU\...\FileExts\<ext>\UserChoice`，帶雜湊保護，Explorer
   解析雙擊要開哪個程式時的最高優先權，任何 HKLM 關聯都蓋不過它。
2. **HKCU 覆寫** — `HKCU\Software\Classes\<ext>`，per-user 的關聯覆寫，在傳統
   `HKEY_CLASSES_ROOT` 合併規則裡優先權高於 `HKLM\Software\Classes`。
3. **開啟方式候選清單** — `FileExts\<ext>\OpenWithProgids` / `OpenWithList`，
   餵給「選取應用程式以開啟」對話框「建議的應用程式」清單，累積過期候選的地方
   （注意跟第 2 點的 `Software\Classes\<ext>\OpenWithProgids` 是不同機碼路徑）。

即使清乾淨以上三層，只要目前沒有 UserChoice，Windows 的既定設計就是雙擊時跳出
「選取應用程式以開啟」對話框問一次——這是刻意的反挾持保護機制，無法被程式繞過。

## registry seam

`file_assoc.py` 的 `register()`/`unregister()` 都吃一個 `registry` 參數，預設是
真正的 `winreg` 模組；測試用 `tests/_fakes.py` 的 `FakeWinReg`（介面跟 `winreg`
一致）當這個參數傳進去，不需要 monkeypatch `sys.modules` 或模組屬性。這是這個
專案裡「登錄表」這個概念唯一的注入點。

## 副檔名（file_extension.py）

「副檔名」在這個專案裡不只是一段字串——它會被推導成四個不同的名字，分別給
四個地方使用：

| 推導出來的名字 | 用在哪裡 | 形式 |
| --- | --- | --- |
| **ProgID** | 傳統引擎的登錄表關聯 | `AppFile<副檔名去掉所有句點>` |
| **傳統引擎的內嵌圖示檔名** | 打包端內嵌、安裝端取用 | `doc_icon_<副檔名去掉開頭的點>.ico` |
| **關聯群組名** | MSIX 套件清單的 `uap:FileTypeAssociation@Name` | 副檔名去掉開頭的點 |
| **MSIX 套件內的圖示檔名** | 套件目錄裡的實體檔案 | `doc_<關聯群組名>.png` |

四個推導與「什麼樣的副檔名算合法」這條規則都收斂在 `file_extension.py`。
`file_assoc.prog_id()` 與 `msix_manifest.association_group_name()`／
`association_logo_name()` 保留原名，實作轉呼叫這裡——那兩個名字是 CONTEXT.md
與 ADR 記載過的對齊點。

**合法性的兩層來源**：長度上限 64、全小寫、不含空白，是 Microsoft 對關聯群組名
的規定；字元集限於英文字母、數字、句點、連字號、底線，是本工具自訂的限制
（官方文件未載明字元集，而這個字串同時會成為檔名，放行路徑分隔符等於讓設定值
決定檔案寫到哪裡）。

這個模組的由來見 `docs/investigations/MSIX稽核與缺陷修正.md` 的 D2。

## 拖曳安裝（Drag-to-Install）與視窗拖曳（Window Drag）

這個專案裡有兩種不同的「拖曳」，兩者都存在於安裝端視窗上，講的時候一定要分開：

- **視窗拖曳** — 按住無邊框視窗的空白處，把整個視窗搬到桌面上的另一個位置。
  實作在 `window_drag.py`（`WindowDragController`），製作工具視窗跟安裝端視窗
  共用同一份。
- **拖曳安裝** — 把 App 圖示拖到安裝目的地圖示上放開，藉此觸發安裝。這是安裝
  流程的核心動作，也是這個專案模仿 macOS DMG 的識別所在。
- **拖曳解除安裝** — 同一個動作的另一端：把 App 圖示拖到垃圾桶上放開，觸發
  解除安裝。

**拖曳安裝與拖曳解除安裝是同一套實作**（`ui/drag_to_target.js`，見
[`ADR-0002`](docs/adr/0002-drag-to-install-self-rendered-drag.md)）。兩端
真正不同的只有目的地自己的回應：安裝端是資料夾「吞一下」，解除安裝端是
垃圾桶掀蓋（懸停）與闔蓋（被吸進去時），用 callback 參數化。

這兩種拖曳的組成：

- **拖曳本體** — 使用者手上那張跟著游標移動的 App 圖示。
- **原位殘影** — 拖曳本體被拿起後，留在原本位置上的視覺替身，用來表達「這東西
  仍然屬於這裡，只是暫時被拿起來」，同時作為沒有命中時彈回去的目的地。
- **目的地圖示** — 接收拖曳本體的元素。安裝端是資料夾（`#drop-target`），
  同時也是更改安裝路徑的入口（點它會開啟選擇資料夾對話框）；解除安裝端是
  垃圾桶（`#trashDropTarget`）。
- **命中判定** — 放開的瞬間，判斷這次拖曳算不算「放進去了」。採用**重疊即命中**：
  拖曳本體與目的地圖示的範圍有重疊就算命中，不要求游標本身壓在目的地上。
  不採用依放開速度往前推算落點的做法（那適用於可回頭的動作，安裝／解除安裝
  會實際改動使用者系統，不應該因為手一抖甩過去就觸發）。

鍵盤操作走的是同一段觸發邏輯，但不播放拖曳動畫（見
`tests/test_ui_accessibility.py`、`tests/test_ui_uninstall_drag.py`）。

**進行中不可再拖**：安裝／解除安裝一旦開始，圖示就不再能被抓起來，完成之後
也不行（失敗或取消退回主畫面則恢復）。這不是可有可無的細節——真實抓到的
缺陷（2026-08-30）：使用者在成功畫面出現前抓住圖示、切過去之後再放到目的地
上，會把動作觸發第二次。畫面上的覆蓋層擋不住這種拖曳，因為已經取得指標捕獲
的事件完全不經過命中測試，所以顯示結果畫面時要主動終結進行中的拖曳。安裝端
後端另有一層重入防護（`InstallerAPI.trigger_installation()`）。

### `ui/` 底下的共用前端檔案

| 檔案 | 職責 | 誰載入 |
|---|---|---|
| `spring.js` | 彈簧求解器（拖曳位移、垃圾桶蓋角度共用） | index、uninstall |
| `drag_to_target.js` | 自繪的拖曳手勢（需要 `spring.js`） | index、uninstall |
| `i18n.js` | 介面翻譯（`data-i18n` 四種標記） | index、uninstall、config |

載入順序有相依：`spring.js` 要排在 `drag_to_target.js` 之前。這幾個檔案怎麼
被帶進打包產物，見 `packaging_core.ensure_workspace_files()` 的覆蓋策略與
`tests/test_ui_asset_packaging.py`。

## 深模組拆分（installer_core.py 瘦身）

`InstallerAPI`（`installer_core.py`）原本是一個混雜六種關注點的大 class，這輪
收斂拆出三個獨立、`installer_core.py` 跟 `gui_config.py` 可能共用的深模組：

- **`window_drag.py`** — `WindowDragController`，無邊框視窗的自訂拖曳邏輯，
  `ConfigAPI`（製作工具視窗）跟 `InstallerAPI`（安裝端視窗）共用同一份實作。
- **`disk_space.py`** — `check_drive_space()`/`required_install_size()`，純函式，
  不需要建構 `InstallerAPI()` 就能測。`check_drive_space()` 吃的是一組
  「落地目錄 → 需要多少空間」，依磁碟代號分組後逐一檢查——安裝內容可能
  同時落在安裝目錄、`%LOCALAPPDATA%\Programs\<folder_name>`（見
  `local_appdata_files`）與 `%TEMP%`（覆蓋安裝的備份），三者不保證在
  同一顆磁碟上。
- **`file_assoc.py`** — 見上面。

`trigger_installation()` 本身（複製檔案 + 完整性驗證 + 失敗回滾的協定）維持在
`InstallerAPI` 裡沒有拆，因為套用刪除測試：拆開之後，回滾協定的複雜度會原封不動
在別處重現，不是真的拆掉了什麼。

## 多語系的兩層（安裝過程／安裝完成後）

這個專案裡「多語系」指的可能是兩件不同的事，講的時候要分開，混用會導致
把不相干的兩套機制接在一起：

- **安裝過程的多語系** — `ui/i18n.js`（`config.html`、`index.html`、
  `uninstall.html` 三份畫面共用的翻譯機制）加上 `eula_texts` 的 EULA 文字。
  這些是**打包工具與安裝程式自己的畫面**，全部在把應用程式交付落地之前
  顯示完畢，且屬於 `Setup.exe` 的一部分。
- **安裝完成後的多語系** — 應用程式裝好之後，Windows 在開始功能表、
  「設定 → 應用程式」清單、工作列上顯示它所用的**名稱**。傳統引擎沒有這
  一層（顯示的就是 `app_name` 這個單一字串）；MSIX 引擎可以有，來源是套件
  清單的顯示名稱宣告。

兩者沒有對應關係：EULA 用什麼語言顯示，與應用程式裝好之後叫什麼名字，是
不同的問題。

## 命令列別名（Execution Alias）

「讓使用者在命令列直接打名字就能執行」這個功能，兩種引擎的機制不同，
效果也不完全一樣：

- **傳統引擎**是把**安裝目錄**加入 PATH 環境變數，結果是該目錄底下**所有**
  執行檔都能被呼叫。`path_target_exe` 的作用是**縮小**這個範圍到單一支。
- **MSIX 引擎**註冊的是**命令列別名**：別名綁定在單一應用程式項目上，沒有
  「把目錄加入 PATH」這種形式。因此 `path_target_exe` 在 MSIX 模式是必要
  資訊，而非縮小範圍的選項。

「加進 PATH」這個說法在 MSIX 模式並不精確——實際發生的事是註冊別名，不是
修改 PATH 環境變數。

## 安裝路徑與使用者範圍

「應用程式的檔案落在哪裡」與「這台電腦上哪些使用者用得到它」是兩件不同的
事。傳統引擎把兩者綁在同一個選擇上，MSIX 引擎會把它們分開，因此講的時候
要分開講：

- **安裝路徑** — 應用程式的檔案實際落在哪個資料夾。
- **使用者範圍** — 安裝完成後，這台電腦上哪些使用者能使用這個應用程式。
  兩種值：**當前使用者**（只有執行安裝的那一位）、**全機器**（機器上每一位，
  其他使用者登入後也能使用）。

傳統引擎裡兩者焊在一起，無法分開設定：打包時的「安裝位置三選一」
（`ui/config.html` 的 `isNoAdminInstall()`，對外是 `no_admin_install` 欄位）
同時決定兩者——選 `Program Files` 即為全機器範圍（解除安裝項目寫 HKLM、
捷徑放在 `ProgramData` 與 `Public\Desktop`，其他使用者登入後看得到並可執行），
選使用者目錄即為當前使用者範圍。

MSIX 引擎打斷這個焊點：**安裝路徑由系統決定、不可選擇**；**使用者範圍仍然
可選**，但不再靠選路徑來表達，而是由部署時採用哪一個系統 API 決定——當前
使用者與全機器是兩個不同的呼叫，後者另有前置條件。

在 MSIX 模式下表達使用者範圍的欄位是 `msix.all_users`，預設為假（當前
使用者）。**此欄位已定案、尚未實作。**`no_admin_install` 在該模式下不表達使用者範圍，也不表達安裝路徑
——它原本綁的兩件事在這裡各自另有歸屬。理由見
[ADR-0013](docs/adr/0013-msix-all-users-scope-is-an-opt-in-field.md)。

因此在 MSIX 模式下描述損失時，講的是「其他使用者不會有這個應用程式」，
不是「裝不到 `Program Files`」——後者對使用者不具意義，前者才是他會察覺的
結果。

**不要與 `InstallScope` 混淆**（見下一節）：那個 class 涵蓋的是兩者焊在
一起的舊概念（hive、PATH、捷徑位置、預設安裝路徑一次全決定），不等於這裡
的「使用者範圍」。

## InstallScope（no_admin_install 的 hive/目錄判斷）

`no_admin_install` 這個打包選項開啟時，整個安裝流程（含解除安裝）完全不要求
系統管理員權限，所有登錄表/PATH/捷徑/預設安裝路徑都要從系統層級位置改成
使用者層級位置。這個判斷原本在 `installer_core.py`（安裝端）跟 `uninstall.py`
（解除安裝端）各自獨立重新推導過一次，收斂進 `install_scope.py` 的
`InstallScope` class，兩邊共用同一份規則。

`InstallScope` 建構時可以選填注入 `registry` 參數——這是跟 `file_assoc.py`
同一個「registry seam」，但注入方式不同：`installer_core.py` 的測試用
`mock.patch.dict(sys.modules, {"winreg": fake})`，`InstallScope` 對它可以
不用傳 `registry`（每次存取都重新 `import winreg`，吃得到這種 patch）；
`uninstall.py` 是在檔案最上面 `import winreg` 一次、測試改用
`mock.patch.object(un, "winreg", fake)`，這種情境下 `uninstall.py` 呼叫
`InstallScope` 時要把自己那個（可能已被 patch 掉的）`winreg` 名字傳進去。

## 跨 no_admin_install 模式的升級偵測與跨 UAC 呼叫

真實抓到的 bug：`installer_core.py.check_existing_install()` 原本只查
「這次打包設定」算出來的單一 hive，如果舊版本是用不同的 `no_admin_install`
設定裝的（例如舊版本用預設設定裝在 Program Files、登錄表寫在 HKLM，
這次改用 `--no-admin-install` 重新打包），完全查不到舊版本的登錄表紀錄，
誤判成「沒裝過」，跳過「是否要更新」的提示，新舊兩份安裝各自獨立存在。
修正：兩邊 hive 都查，回傳值多一個 `"hive"` 欄位（`"HKLM"`/`"HKCU"`，
記錄實際找到的那一邊）。

延伸出另一個問題：`run_upgrade_uninstall()` 呼叫舊版 `uninstall.exe`
原本一律用 `subprocess.run()`（底層是 `CreateProcess`）。Windows 的
manifest 自動提權（跳 UAC 詢問）只有透過 `ShellExecute` 這條路徑才會被
認得，`CreateProcess` 不會觸發提權，會直接用目前（未提權）行程的權杖把
子行程跑起來——如果舊版本需要管理員權限（`hive == "HKLM"`）但這次新
安裝檔是免權限執行，舊版 `uninstall.exe` 會在寫入 Program Files／刪除
HKLM 機碼時默默失敗，卻不拋出任何例外，看起來像是清乾淨了、實際上沒有。
修正：新增 `_is_current_process_elevated()`（`IsUserAnAdmin()`）判斷目前
行程是否已提權，`hive == "HKLM"` 且未提權時改用
`_run_uninstall_exe_elevated()`（`ShellExecuteExW` + `"runas"` 動詞 +
`WaitForSingleObject` 等待完成）跨 UAC 呼叫，其餘情況維持原本的
`subprocess.run()`。這條路徑的真實 UAC 互動沒辦法在開發環境模擬，只能
用 mock 驗證呼叫參數正確，實際跳 UAC 的行為需要在實機驗證。

## system_entries（登錄表項目/捷徑/PATH 的移除原語）

`system_entries.py` 收斂了三個「移除」原語：`remove_registry_entry()`、
`remove_shortcut()`、`remove_from_path()`。原本這幾個函式只活在
`uninstall.py` 裡（真正解除安裝時才會用到）；現在拆成獨立模組，讓
`installer_core.py` 安裝失敗時的回滾（`_rollback()`）也能呼叫同一份實作，
清掉這次安裝這一輪已經寫入的登錄表項目/捷徑/PATH，不用另外維護一份邏輯
幾乎一樣的複本。跟 `file_assoc.py` 用同一種 registry seam：`registry`
參數預設是真正的 `winreg` 模組，測試直接把 `FakeWinReg` 當參數傳進去；
`uninstall.py` 呼叫時明確傳自己模組層級那個（可能已被測試 patch 掉的）
`winreg` 名字進去，跟 `InstallScope` 的用法一致。

`installer_core.py._rollback()` 現在除了原本「刪掉這次複製出去的檔案」
之外，也會依「後寫的先復原」順序（跟安裝時登錄表 → 捷徑 → 檔案關聯 →
PATH 的寫入順序相反）呼叫 `system_entries` 的移除函式跟
`file_assoc.unregister()`，只回滾這次安裝這一輪自己成功寫入的部分——
呼叫端（`trigger_installation()`）在四個寫入步驟各自成功後才把對應的
狀態記下來，失敗時原封不動傳給 `_rollback()`。

### 移除原語的兩條共通規則

**一、回傳值的語義是「這個函式結束之後，目標是否確實不存在」**，不是
「這次有沒有刪到東西」。目標本來就不存在（登錄表機碼 `DeleteKey` 拋
`FileNotFoundError`、捷徑檔案不存在、PATH 裡本來就沒有這筆）一律視為成功，
只有實際移除失敗才回傳 `False`。`file_assoc.unregister()` 與
`system_entries.remove_from_path()` 原本不回傳任何值，現在也遵守同一套語義。

這個語義是「解除安裝完成畫面顯示未清乾淨項目」的前提：舊語義下「本來就
沒有這個項目」跟「移除失敗」回傳同一個值，接上畫面之後，使用者自己刪過
捷徑、或安裝當時捷徑建立就失敗過（那是可忽略的設計）這些正常情境，全部
會變成解除安裝結束時的假警告。

**二、四個移除點都嘗試兩個位置。** `manifest` 裡的 `no_admin_install`
可能跟當初實際安裝時用的模式不符（欄位遺失時 `uninstall.py` 預設回退成
`False`、或 `manifest` 被手動編輯過），只認推導出來的單一位置時，項目
實際寫在另一個位置就完全找不到，殘留永遠留著。四個移除點分別是：

| 移除點 | 兩個位置 |
|---|---|
| `remove_registry_entry()` | HKLM ／ HKCU 的 Uninstall 機碼 |
| `remove_shortcut()` | Public Desktop・ProgramData 開始功能表 ／ 使用者自己的桌面・開始功能表 |
| `remove_from_path()` | 機器層級 ／ 使用者層級的 Environment 機碼 |
| `file_assoc.unregister()` | HKLM ／ HKCU 的 `Software\Classes` |

擴大範圍帶來的兩個保護措施：

- `remove_from_path()` 每個 hive 先唯讀探一次，確認安裝路徑真的在裡面才
  用寫入權限重開。沒有這一步的話，一般權限執行的解除安裝會在機器層級的
  Environment 拿到 `PermissionError`，變成一個假的失敗回報——那個 hive 裡
  本來就沒有東西要清。
- `file_assoc.unregister()` 只在 `Software\Classes\<ext>` 的預設值確實
  指著我們的 ProgID（`AppFile<ext>`，見 `prog_id()` 的命名慣例）時才刪除
  那個副檔名機碼。ProgID 機碼本身確定是我們寫的，可以直接刪；但副檔名
  機碼指向的 ProgID 隨時可能已經是另一個應用程式的（使用者事後改用別的
  程式開啟這個副檔名）。

## 編譯工作目錄（packaging_core.get_workspace_dir() / packaging_settings.py）

真實抓到的 bug：`get_workspace_dir()` 在 frozen exe 情境下原本固定用
「這支工具（GUI 版）自己被安裝到的資料夾」當工作目錄（`dist/`/`build/`
等編譯產物的落地位置）。如果使用者把這支工具裝在 `Program Files`，一般
權限雙擊執行時寫不進自己所在的資料夾，編譯/打包直接失敗——「裝完立刻
啟動」之所以能用，是因為那次啟動繼承了安裝程式（`--uac-admin`）的提權
權杖，之後從開始功能表/桌面捷徑正常雙擊打開就會踩到。

修法分兩層：`packaging_core.default_workspace_dir()` 固定改用使用者層級、
保證寫得進去的 `%LOCALAPPDATA%\mac-style-windows-installer\workspace`
當預設值，跟這支 exe 裝在哪完全脫鉤；另外新增 `packaging_settings.py`
（通用 key/value JSON 持久化，目前只有 `workspace_dir` 這一個 key），讓
`ui/config.html` 可以提供一個「編譯工作目錄」欄位，使用者自訂並記住偏好
的位置，`get_workspace_dir()` 的優先順序是：使用者自訂設定 >
`default_workspace_dir()`。CLI（`builder_cli.py`）已有的 `--workspace-dir`
旗標優先權比這兩者都高，不受影響。

## 共用深模組的打包清單（packaging_core.ENTRY_SCRIPTS / SHARED_DEEP_MODULES）

真實抓到的 bug（在另一個使用這個工具的專案裡發現）：`mswi-cli`/`mswi-gui`
打包出來的 `Setup_XXX.exe` 一執行就 `ModuleNotFoundError: No module named
'system_entries'`。根本原因是這個打包工具自己（frozen 之後的 mswi-gui.exe/
mswi-cli.exe）要讓 `installer_core.py`/`uninstall.py` 在使用者電腦上重新被
`pyinstaller` 編譯成最終安裝檔/uninstall.exe 之前，得先把這兩支 entry point
實際 import 的所有專案內部模組（`file_assoc.py`/`install_scope.py`/
`self_delete.py`/`system_entries.py` 等）內嵌進自己身上、執行期再解壓回
工作目錄——這件事原本要靠 `packaging_core.ensure_workspace_files()` 跟
`build_config_tool.py` 的 `--add-data` 兩份手動維護的清單保持同步，新增
一個深模組卻忘記同步更新任一邊，frozen exe 執行到那一步就會找不到模組；
`.py` 直接執行（開發環境）完全不會踩到，因為工作目錄本來就是原始碼目錄。

修法：`packaging_core.py` 新增 `ENTRY_SCRIPTS`/`SHARED_DEEP_MODULES` 這兩個
模組層級常數，當唯一真實來源；`build_config_tool.py` 的 `_SHARED_ADD_DATA`
直接從這兩個常數組出來，不再自己維護一份複本。`tests/test_shared_module_packaging.py`
用 `ast` 解析 `installer_core.py`/`uninstall.py` 實際的 import 陳述式，
自動比對這份清單有沒有漏掉，以後新增深模組卻忘記同步登記會直接紅燈，
不需要每次有人手動記得。

## self_delete（uninstall.exe 自我刪除）

`uninstall.exe` 解除安裝完成後刪除自己這件事收在 `self_delete.py`。這段
`.bat` 產生 + 重試邏輯已經踩過三輪真實 bug（第一輪：延遲時間不夠、失敗不
重試；第二輪：`--noconsole` 編譯後 stdin/stdout/stderr 無效控制代碼；第三輪：
`for /l` 迴圈把整個主體當靜態區塊解析、鎖釋放後依然持續回報失敗，改成
`.bat` 檔的 `:retry`/`goto retry` 標籤式重試）。`schedule_if_needed(argv,
current_dir, exe_path, safe_to_remove_whole_dir)` 把「要不要排程」（看
`argv` 裡有沒有 `--upgrade`）跟「真的排程」收在同一個介面，呼叫端
（`uninstall.py` 的 `run_silent_uninstall()`/`UninstallerAPI.finish_and_exit()`）
不用自己先檢查前置條件。

## explorer_lock_release（檔案鎖定釋放：分層策略）

`explorer_lock_release.py` 收斂了「檔案被 explorer.exe 鎖住時怎麼釋放」這件
事，取代原本 `close_locking_processes()`/`_kill_processes()` 裡「無腦
taskkill 整個殼層行程」的做法。分兩層：

- **關窗**（`close_windows_browsing_path()`）——先只關閉正在瀏覽目標路徑的
  檔案總管**視窗**（`Shell.Application` COM，呼叫 `.Quit()`），不動
  `explorer.exe` 這個殼層**行程**本身。工作管理員裡「應用程式」跟
  「Windows 處理程序」兩個 `explorer.exe` 項目行為不同就是這個道理：前者
  是單一視窗，關掉不影響桌面/工作列；後者才是整個殼層行程。
- **強制關殼層**（`release_locking_processes()`/`_terminate_process()`）——
  只有關窗解決不了才進到這一步：暫停 `AutoRestartShell`（避免
  `explorer.exe` 結束後瞬間自動復活、在檔案操作完成前搶回同一個鎖）、直接
  呼叫 `OpenProcessToken`/`AdjustTokenPrivileges` 啟用 `SeDebugPrivilege`
  後 `TerminateProcess`（不是 `taskkill.exe`——它預設不啟用這個權限，對
  `explorer.exe` 會回報存取被拒），檔案操作完成後（`try/finally`，不管
  成功失敗）呼叫 `restore_after_lock_release()` 補重啟殼層、寫回原值。

**已知限制**：即使兩層都正確執行，第三方防毒/端點防護軟體（例如把
`explorer.exe` 列為「關鍵行程保護」對象）仍可能在核心層攔截
`TerminateProcess`（`OpenProcess` 成功、`TerminateProcess` 回報存取被拒）。
這是防毒軟體的合理行為，不是這支安裝程式的 bug，也不該嘗試繞過——遇到
這種情況只能提示使用者去檢查防毒/安全軟體設定（`_describe_install_os_error()`
的訊息已補上這句提示）。整條流程的除錯紀錄落地在
`%TEMP%\mswi_explorer_lock_debug.log`，供事後排查用。

## version_info（打包出來的 exe 帶上 Win32 VERSIONINFO 資源）

`version_info.py` 是個純函式深模組：`render_version_file(...)` 組出
PyInstaller `--version-file` 要求的文字格式（回傳字串，不寫檔），
`write_version_file(path, **fields)` 把結果寫進暫存檔。根本問題：
PyInstaller 只有拿到 `--version-file` 才會把 FileDescription/ProductName/
FileVersion/CompanyName/LegalCopyright 這些欄位嵌進 exe 資源，這個專案
原本從沒生成過這個檔案，即使 GUI 表單早就收了「版本號」「發行者」欄位，
也從未真正流進被打包 exe 的資源區塊，導致檔案總管「內容 → 詳細資料」
頁籤全部空白。

兩個呼叫端各自在呼叫 PyInstaller 前生成一份暫存 version-file：
`build_config_tool.py`（打包這個工具自己的 GUI/CLI exe，`ProductName`
固定是專案名稱，新增 `--publisher` CLI 參數）跟 `builder.py`（打包使用者
的 app 成 Setup.exe/uninstall.exe，`ProductName` 沿用既有的 `app_name`
欄位，`LegalCopyright` 由「建置當下年份 + 發行者」自動組成，不新增 GUI
欄位）。這個模組只在開發機的建置流程用到，不會被打包進最終 exe，所以
不列進 `packaging_core.py` 的 `SHARED_DEEP_MODULES`。

## 版本號格式（三個模組共用同一個定義）

版本欄位的格式是 `<主>.<次>.<修>[-<後綴>]`：數字段 1 至 4 段、每段皆為
非負整數，後綴是連字號之後的任意非空文字（`1.0.0`、`1.2.3.4`、
`1.0.0-rc1`、`2.0.0-beta`）。決定與理由見
[`docs/adr/0003`](docs/adr/0003-allow-prerelease-suffix-in-version-string.md)。

同一個定義分佈在三個模組，各自負責一段：

- `packaging_core._validate_version_string()`——**唯一的把關點**，在
  `validate_and_build_pack_data()` 裡，於任何檔案系統副作用發生之前回報。
  原本這裡只檢查非空字串，真正的格式檢查發生在 `builder.py` 中段，此時
  `dist/`／`build/` 已於流程開頭被清空。
- `version_info._parse_version_tuple()`——Win32 VERSIONINFO 的數值欄位
  （`filevers`／`prodvers`）依規格固定是 4 個 16 位元整數，容不下文字，
  所以每一段只取開頭連續的數字（`1.0.0-rc1` → `(1, 0, 0, 0)`）。字串
  欄位（`FileVersion`／`ProductVersion`）保留原始文字——檔案總管「內容
  → 詳細資料」顯示的是字串欄位，終端使用者仍看得到完整的預發布版本號。
- `version_compare.parse_version()`／`compare_versions()`——覆蓋安裝的
  版本比較。同樣是「每段只取開頭連續數字」，跟 `version_info.py` 對數字
  段的看法一致；預發布的判定以「有無連字號」為準。

這三者原本並不一致：`version_compare.py` 早就完整處理帶後綴的版本，
但 `version_info._parse_version_tuple()` 要求每段都是純整數，`1.0.0-rc1`
會拋 `ValueError` 中止建置——這種版本號根本無法打包產出，`version_compare.py`
的預發布比較邏輯在實際流程中永遠不會被執行到。

## 免管理員權限安裝與需要提權的選項互斥

`no_admin_install` 開啟時 `builder.py` 不加入提權設定，整個安裝流程在一般
權限下執行。**「安裝為 Windows 服務」與「安裝前建立系統還原點」在這個模式
下必定失敗**（`sc.exe create` 與系統還原點建立都需要管理員權限），所以
`packaging_core.validate_and_build_pack_data()` 直接把這兩個組合視為欄位
驗證失敗，`ui/config.html` 也會在切換「安裝位置」時同步停用這兩個選項並
顯示原因。

**排程工作不在此列**：`schtasks.exe` 以目前使用者身分建立 `onlogon` 觸發的
工作不需要管理員權限，免權限安裝仍然可以用。

GUI 端「這次是不是免權限安裝」只在 `isNoAdminInstall()` 算一次，送出資料的
`no_admin_install` 欄位與畫面停用邏輯共用它，不各自維護一份判斷。

## 安裝密碼保護（Install Password Protection）

**已實作**（2026-08-12 grilling session 定案，之後依這份設計實作完成）。
下面記載的每一項都對得上實際的程式碼：

| 這一節提到的 | 實際位置 |
|---|---|
| 加密／解密本體 | `install_encryption.py`（`encrypt_directory()`／`decrypt_to_directory()`／`WrongPasswordError`） |
| `install_password_env` 欄位驗證 | `packaging_core._validate_install_password_env()` |
| 打包時加密 `app_dir` | `builder.build_all()` 的 `app_contents.enc` 處理 |
| 密碼關卡（EULA 之前） | `installer_core.InstallerAPI.is_password_protected()`／`verify_install_password()`、`ui/index.html` 的 `passwordView` |
| 解密後的複製來源 | `installer_core.InstallerAPI._app_contents_dir()` |
| `/PASSWORD=` 靜默參數 | `installer_core._parse_cli_args()`／`run_silent_install()` |
| 配置精靈的欄位 | `ui/config.html` 的 `install_password_section` |
| CLI 使用說明 | `CLI_USAGE.md` |

### 指定密碼的兩種填法

密碼有兩個可能的來源，**只有一種能寫進設定檔**：

| 填法 | 配置精靈 | 設定檔（CLI） |
|---|---|---|
| 直接輸入密碼 | ✅ 預設 | ❌ 明白報錯 |
| 填環境變數名稱（`install_password_env`） | ✅ | ✅ |

不對等是決定，不是遺漏，理由見
[`docs/adr/0004`](docs/adr/0004-inline-install-password-is-gui-only.md)：
`validate_and_build_pack_data()` 收的那包 `data`，欄位集合就是設定檔的
格式，而 GUI 跟 CLI 共用同一個驗證函式——讓「直接輸入密碼」變成 `data`
的一個一般欄位，等於同時讓設定檔也能寫明文密碼。

直接輸入的密碼因此走一條獨立的參數路徑
（`ConfigAPI.start_pack(data, install_password)` →
`builder.build_all(install_password=...)`），全程不進 `data`、不進
`pack_data`。`validate_and_build_pack_data()` 只收到一個布林值
（`has_inline_password`），知道「這次有沒有用直接輸入」就足以做驗證。

`need_install_password` 是配置精靈那顆勾選框的狀態，跟 `need_file_assoc`／
`use_custom_doc_icon` 是同一種欄位（勾選框決定要不要套用旁邊的欄位）；
CLI 沒有勾選框，由 `builder_cli.py` 依 `install_password_env` 有沒有內容
推斷。有了它，後端才分得出「沒啟用」跟「啟用了但兩種填法都留空」——後者
要明白報錯，不能默默做出一顆沒有密碼保護的安裝檔。

選填功能：打包時可以設定一組密碼，安裝時使用者要輸入正確密碼
才能繼續，否則無法取得應用程式檔案。定位是**存取控制**（防止安裝檔被
誤傳/亂用），不是防範有心人暴力破解的資安機制——這個定位決定了下面
好幾個子決策的方向，不要事後模糊掉。

**加密範圍**：只加密內嵌的應用程式檔案本體（`builder.py` 現有
`--add-data={app_dir};app_contents` 這塊），不加密整個安裝程式——EULA、
拖曳互動、相依元件偵測這些安裝流程本身的邏輯照常執行，不受密碼保護
影響。

**打包時**：`app_dir` 整包（不是逐檔案）加密成一份檔案，`--add-data`
改指向這份加密檔而不是原始資料夾，比照 `doc_icon`/`dependencies` 現有
「先暫存再 `--add-data`」的模式。密碼透過新增的 JSON 欄位
**`install_password_env`**（環境變數名稱，不是密碼明文，比照現有
`signing.cert_password_env` 的做法）在打包當下讀出；`validate_and_build_pack_data()`
的驗證規則也直接比照 `cert_password_env`——只檢查環境變數有沒有值，
不額外要求密碼長度/複雜度。演算法用 AES-256-GCM + PBKDF2 金鑰衍生，
透過新增的 `cryptography` 套件（這個專案第一個真正的第三方加密相依
套件，PyInstaller 有現成支援）——刻意不用 ctypes 直接刻 BCrypt，因為
加密邏輯寫錯是資安問題，不該冒手刻 ctypes 介面出錯的風險。

**安裝時**：密碼輸入畫面是一道「前置關卡」，比照現有 EULA 同意頁的
模式，出現在 EULA **之前**——安裝程式一開啟，有設定密碼保護的話先跳
這關，通過才會依序看到 EULA、主拖曳畫面。密碼正確後解密整份加密檔到
一個暫存資料夾，之後完全沿用 `_trigger_installation_impl_inner()`
現有的複製迴圈/完整性驗證/rollback 邏輯，只是複製來源從
`get_resource_path("app_contents")` 換成這個解密後的暫存資料夾——這段
既有邏輯已經踩過好幾輪真實 bug 修正，不去動它。密碼輸入錯誤**不限制
重試次數**（跟前面的存取控制定位一致：不是要擋暴力猜測，那是 PBKDF2
高迭代次數的責任，不是 UI 層重試次數限制的責任）。

**靜默安裝**：比照 Inno Setup 既有慣例，新增 **`/PASSWORD=密碼`**
命令列旗標。沒帶密碼或密碼錯誤時，靜默模式**不能跳出任何視窗、不能
卡住等輸入**，直接中止並回傳非 0 exit code，原因寫進既有的靜默安裝
log 機制（`%TEMP%\<app_name>_silent_install_log.txt` 或 `/LOG=` 指定
路徑）。

## 深模組拆分（第二輪：架構稽核 `/improve-codebase-architecture`）

繼「深模組拆分（installer_core.py 瘦身）」那一輪之後，這輪又拆出三個
深模組，動機都是同一個：`installer_core.py` 仍是全 repo 異動最頻繁、
體積最大的檔案，這幾個關注點各自有獨立、可以脫離 `InstallerAPI` 單獨
驗證的複雜度，卻只因為都掛在 `InstallerAPI` 上而被迫互相靠實例屬性
傳遞狀態。

- **`progress_report.py`** — `report_progress(window, js_callback_name,
  percent, message)`。原本 `installer_core.py`（安裝主流程/相依元件
  安裝，各自對應前端不同進度條）跟 `uninstall.py` 各有一份逐位元組幾乎
  相同的 `_report_progress()`，只差前端 callback 名稱。收斂成一份後
  意外暴露一個真實 bug：`window` 這個模組層級全域變數原本從未被顯式
  初始化，只有 `main()` 真正建立 pywebview 視窗時才會賦值——之所以
  沒出過事，是因為原本的重複程式碼各自包在自己的 `try/except
  Exception` 裡，讀取未賦值全域變數的 `NameError` 被原地吞掉，效果
  剛好等於「還沒建立好視窗就不做事」。收斂之後這個巧合消失，兩個模組
  現在都顯式 `window = None`。`system_entries.py` 也同時多了
  `cleanup_empty_dirs()`/`kill_process_by_name()` 兩個跟登錄表無關的
  移除原語，理由相同（原本兩邊各自一份重複實作）。

- **`version_compare.py`**（`parse_version()`/`compare_versions()`）
  跟 **`dependency_install.py`**（登錄表偵測 checker 群 +
  `build_checkers()`/`get_warnings()`/`install()` 下載驗證安裝協定）
  ——原本 `dependency_defs.py` 的說明文字宣稱「checker/URL/靜默參數
  本體定義在這」，但那個檔案一直只有一個沒有行為的 metadata dict，
  真正的行為全部散落在 `installer_core.py`，去 `dependency_defs.py`
  找行為的人只會撲空（診斷 .NET Desktop Runtime 誤判那次真的踩到
  這個落差）。`version_compare.py` 拆成獨立模組是因為
  `parse_version()`/`compare_versions()` 同時被 `upgrade.py` 的覆蓋
  安裝版本偵測跟 `dependency_install.py` 的相依元件版本門檻共用，兩邊
  誰都不該匯入對方換取這兩個純函式。`dependency_install.py` 的介面吃
  明確參數（`custom_dependencies`/`bundle_dependencies`/`checkers`），
  不吃 `InstallerAPI` 實例狀態。

- **`upgrade.py`**（`check_existing()` 模組函式 +
  `UpgradeCoordinator` class）——覆蓋安裝（偵測已安裝舊版本、備份、
  靜默呼叫舊版 `uninstall.exe`、必要時跨 UAC、失敗復原）原本是
  `InstallerAPI` 上 8 個各自獨立的方法，靠 `selected_path`/`_scope`/
  `_upgrade_backup_path`/`_upgrade_backup_original_path` 這幾個共享
  實例屬性互相傳遞狀態——內部複雜度是真的（踩過三輪真實 bug：
  dual-hive 版本偵測、`CreateProcess` 不會觸發 UAC、pending-delete
  競態），但沒有窄介面隔開，`InstallerAPI` 其他方法可以隨意伸手進備份
  路徑狀態。收斂後備份路徑收進 `UpgradeCoordinator` 物件內部
  （`backup_path`/`backup_original_path` 兩個屬性），`InstallerAPI`
  只保留 `check_existing_install()`/`run_upgrade_uninstall()`/
  `_restore_upgrade_backup()`/`_discard_upgrade_backup()` 四個薄委派
  方法（後兩個因為 `trigger_installation()`/`close_window()` 有多處
  各自獨立呼叫，保留成方法而非直接呼叫 `self._upgrade.xxx()`，維持
  既有呼叫點不動）。

## SDK 工具（SDK Tools）

`makeappx.exe`（打包 MSIX）與 `signtool.exe`（簽數位簽章）這兩支同屬
Windows SDK 的執行檔。在這個專案裡「SDK 工具」專指這兩支，不泛指
Windows SDK 的其他內容。定位與取得收斂在 `sdk_tools.py`，是它們唯一的
取得入口。理由與五項決定見
[ADR-0008](docs/adr/0008-sdk-build-tools-are-fetched-on-explicit-request-only.md)。

- **來源（source）** — 一次檢索結果的出處，四者之一，優先序即依「使用者
  表達該意圖的明確程度」排列：`manual`（設定 `sdk_tools_dir`）→ `cache`
  （執行過取得指令而下載的快取）→ `path`（PATH 上找到的）→ `system`
  （系統上碰巧裝著的 Windows SDK）。`find_tool()` 回傳的 `ToolLocation`
  帶著來源與版本，`describe()` 是印進建置訊息的那一行。
- **取得指令（fetch-sdk-tools）** — CLI 的獨立子指令，下載固定版本的
  `Microsoft.Windows.SDK.BuildTools` NuGet 套件並驗證 SHA-256。打包流程
  不會自行執行它：判準不是「打包時是否連網」，而是下載物在打包機器上
  是被內嵌還是被**執行**，而打包機器通常存放簽章憑證。
- **快取（cache）** — 取得指令解壓出來的位置，在 `%LOCALAPPDATA%` 底下、
  路徑含版本號、持久保存。不放編譯工作目錄（那裡每次建置開頭即清空）。

注意兩個版本號不同且不可互推：NuGet 套件的版本（`10.0.26100.4948`）與
解壓後內部工具組目錄的版本（`bin/10.0.26100.0/`）。

## 傳統引擎與 MSIX 引擎

打包時二選一的兩種「安裝檔內部運作方式」。兩者的產出物都是一顆
`Setup_XXX.exe`、都顯示拖曳安裝介面，差別在應用程式檔案實際落地的方式，
以及由此連帶產生的一項行為差異（見下方「使用者範圍」）：

- **傳統引擎**——預設值，未指定即為此。`Setup.exe` 自己複製檔案、寫登錄表、
  產生 `uninstall.exe`，落地的每一筆寫入都記進 `install_manifest.json`，
  解除安裝的乾淨程度取決於這份清單記得夠不夠完整。
- **MSIX 引擎**——`Setup.exe` 內嵌一份已簽章的 `.msix`，顯示完拖曳介面
  之後把落地工作交給 Windows 的套件引擎，由系統保證解除安裝乾淨。選用
  這個引擎時，同一次打包額外產出一顆獨立的 `.msix`（給 winget、企業側載、
  App Installer 雙擊安裝用），該檔案本來就是編 `Setup.exe` 之前的中間
  產物。

兩者在**安裝介面上**的可見差異有兩處：拖曳目的地的資料夾圖示不同
（傳統引擎是 `ui/folder_icon.png`，箭頭；MSIX 引擎是
`ui/windows_folder_icon.png`，系統標記，且不可點選——MSIX 的安裝路徑由
系統決定，沒有選擇餘地），以及 MSIX 引擎沒有垃圾桶解除安裝介面（見
[ADR-0006](docs/adr/0006-msix-mode-has-no-custom-uninstall-ui.md)）。

**使用者範圍**是第三處差異，它不在介面上，而在安裝完成之後。**以下為已
定案、尚未實作的形狀；目前 MSIX 引擎只提供當前使用者範圍。** 兩個引擎都
表達得出兩種範圍，但**預設相反**：傳統引擎預設全機器（`no_admin_install`
未開啟即為此），MSIX 引擎預設當前使用者（須以 `msix.all_users` 明確啟用
全機器）。在多人共用的電腦上，第二位使用者登入後應用程式是否存在，因此
取決於下游專案的設定——這是終端使用者會直接察覺的差異。

兩者的全機器範圍在**移除**上也不對稱：傳統引擎由 `uninstall.exe` 一次移除
乾淨，MSIX 的全機器範圍則無法透過系統介面完整移除（使用者只移除得掉自己
那一份，該機器其後建立的新帳號仍會取得該應用程式）。理由與實測見
[ADR-0013](docs/adr/0013-msix-all-users-scope-is-an-opt-in-field.md)，第一版
只做當前使用者範圍的原始決定見
[ADR-0009](docs/adr/0009-msix-engine-first-version-is-per-user-scope-only.md)。

「模式」這個詞在本專案指的就是這兩者的選擇，不指其他任何二選一的設定。

## 套件身分名稱（Package Identity Name）

MSIX 套件清單中，系統用來判定「兩包套件是否為同一個應用程式」的唯一
依據。與 `app_name`（顯示名稱）是不同的概念，兩者不可互相推導：`app_name`
是給人看的、可以隨時改；套件身分名稱一經發布即不可變更，改了系統就當成
另一個不相關的應用程式，不執行升級而是並存安裝。理由與欄位設計見
[ADR-0007](docs/adr/0007-package-identity-name-is-an-explicit-required-field.md)。

另有一個容易與之混淆的欄位：套件清單的**發行者**，其值必須與簽章憑證上
記載的名稱完全一致（格式為憑證的識別名稱寫法，例如
`CN=某某, O=某某, C=TW`），對不上時系統直接拒絕安裝。這與現有的
`publisher` 欄位（自由文字，寫進 exe 的 VERSIONINFO 當公司名）也是不同
的東西。

## WebView2 Runtime（webview2_runtime.py）

Windows 的**系統元件**，不是 Python 套件——這個區分是關鍵：`pywebview` 是
Python 套件（用 pip 安裝），它需要 WebView2 Runtime 才畫得出畫面，而後者
由 Windows 或 Microsoft 的安裝程式提供。三個進入點（安裝介面、解除安裝
介面、配置精靈）都依賴它。

缺少它時的行為不是「打不開」，而是**視窗開得起來但 CSS 與 JavaScript 都不
生效**：版面塌成直向堆疊並溢出視窗，應用程式名稱停在 `ui/index.html` 的
預設佔位文字「載入中...」，全程不顯示錯誤訊息、行程也不結束。使用者看到的
是一個像是還在載入的畫面。

**它不能被當成一般的「相依元件」處理。** `dependency_install.py` 那一套的
偵測、詢問與進度全都呈現在 `ui/index.html` 裡，而那個頁面正是缺少它時打不開
的東西——雞生蛋。因此偵測與處置全程在 Python 內完成，實作在
`webview2_runtime.py`，介面用原生 `MessageBoxW`。

三個進入點的處置**刻意不同**（安裝端代為安裝、解除安裝端改走靜默路徑、配置
精靈只告知），理由見 `docs/adr/0012`；那不是遺漏，不要統一。
