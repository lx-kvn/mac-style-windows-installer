# CONTEXT.md

專案的領域詞彙表。跟 `規格文件.md`（給接手工程師的完整交接文件）不同，這份只
記錄「概念叫什麼名字」，架構決策的來龍去脈見 `規格文件.md` 跟未來的 `docs/adr/`。

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

## 深模組拆分（installer_core.py 瘦身）

`InstallerAPI`（`installer_core.py`）原本是一個混雜六種關注點的大 class，這輪
收斂拆出三個獨立、`installer_core.py` 跟 `gui_config.py` 可能共用的深模組：

- **`window_drag.py`** — `WindowDragController`，無邊框視窗的自訂拖曳邏輯，
  `ConfigAPI`（製作工具視窗）跟 `InstallerAPI`（安裝端視窗）共用同一份實作。
- **`disk_space.py`** — `check_disk_space()`/`required_install_size()`，純函式，
  不需要建構 `InstallerAPI()` 就能測。
- **`file_assoc.py`** — 見上面。

`trigger_installation()` 本身（複製檔案 + 完整性驗證 + 失敗回滾的協定）維持在
`InstallerAPI` 裡沒有拆，因為套用刪除測試：拆開之後，回滾協定的複雜度會原封不動
在別處重現，不是真的拆掉了什麼。

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
