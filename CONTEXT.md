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
