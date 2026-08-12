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

## 安裝密碼保護（Install Password Protection）

**設計階段，尚未實作**（2026-08-12 grilling session 定案，見對應的實作
task）。選填功能：打包時可以設定一組密碼，安裝時使用者要輸入正確密碼
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
