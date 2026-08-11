# ADR-0001：延後把整個安裝流程重構成統一的 apply/undo 可回滾動作物件

## 狀態

已接受（本輪只做局部套用，全面重構延後到下一輪）。

## 背景

`installer_core.py` 的 `InstallerAPI._trigger_installation_impl_inner()`
依序做六類「有副作用、失敗要能回滾」的安裝動作：複製檔案、寫入解除安裝
登錄表項目（`_register_uninstall_entry()`）、建立捷徑
（`_create_shortcut()`）、註冊檔案關聯（`file_assoc.register()`）、
建立 Windows 服務/排程工作（`windows_service.create_service()`/
`scheduled_task.create_scheduled_task()`）、加入 PATH
（`_add_to_path_env()`）。對應的 `_rollback()` 原本用一長串個別旗標
（`registry_entry_created`/`shortcuts_created`/
`file_associations_registered`/`path_directory`，以及這次新加的
`windows_service_name`/`scheduled_task_name`）追蹤「這次安裝做了哪些
事」，每多一類新的可回滾動作，就要多加一個旗標參數，還要在 `_rollback()`
裡多寫一段對應的 `if 旗標: 復原(...)`——「做了什麼」跟「怎麼復原」分散
在兩個不同地方，這正是本輪修正的 B10/B11/B12 這幾個真實 bug 的共同根源
（旗標設定的時機、或設定的內容，跟實際做了什麼悄悄脫鉤）。

這一輪的架構稽核提出一個更徹底的方案：把每一類安裝動作包成一個統一的
「apply/undo」物件（例如一個 `InstallStep` 介面，`apply()`
執行、`undo()` 復原），`_trigger_installation_impl_inner()` 依序
`apply()` 一份步驟清單，任何一步失敗就對已經 `apply()` 過的步驟依相反
順序呼叫 `undo()`——徹底取代目前這種「先做動作，再另外設一個旗標，
`_rollback()` 另外讀旗標決定要不要復原」的兩階段寫法。

## 決定

**這一輪只把 windows_service/scheduled_task 這兩類最新加入、最少既有
呼叫端依賴的動作，改用新增的 [`install_journal.py`](../../install_journal.py)
（`InstallJournal.record()`/`unwind()`）處理**——呼叫端在動作成功的
當下立即連同「怎麼復原」一起記下來，`_rollback()` 收到 journal 就依
相反順序 `unwind()`，不用再為這兩類多加旗標參數。

**registry/shortcuts/file_associations/PATH 這四類既有、已經穩定運作
一段時間的回滾邏輯，這一輪維持原本的旗標寫法，不強行套用同一套
apply/undo 物件重構**，理由：

1. **風險與可驗證性不對等**：這四類的既有旗標式回滾已經有完整測試
   覆蓋（`TestRollbackCoversSystemEntries` 等既有測試類別），也是這個
   工具核心安裝流程裡最常被實際使用者跑到的路徑。把它們一起改寫成新的
   apply/undo 物件，等於同時重寫整個安裝主流程的控制結構，任何一處
   細節出錯（例如復原順序、例外處理時機）都可能是使用者實際裝機時才會
   踩到的迴歸，而這個專案目前沒有能在真正 Windows 使用者機器上跑過一輪
   真實安裝/回滾的自動化管道能提前攔下這種問題
   （`.github/workflows/test-packaging-options.yml` 覆蓋的是「打包出來
   的功能有沒有生效」，不是「刻意讓安裝中途失敗、驗證回滾動作正確」這種
   情境）。
2. **先驗證模式本身，再擴大範圍**：windows_service/scheduled_task 是
   這次新加的功能、呼叫端最少（只有安裝流程本身，沒有其他既有程式碼
   依賴這兩個旗標的當下語意），拿來當這個新模式的第一組使用者風險
   最低。等 `InstallJournal` 這個模式在下一輪實際發布、有機會被更多
   情境驗證過之後，再评估要不要把 registry/shortcuts/file_associations/
   PATH 這幾類也遷移過去，是更保守、可以分階段驗證的路徑。
3. **這輪的時間預算是「修正稽核發現的錯誤 + 有限度的架構改善」**，不是
   「重寫整個安裝引擎」——把全部六類一次全部改掉，範圍已經超出「架構
   改善，下次發布再帶上」這個定位，比較接近一次獨立的大型重構專案，
   應該有自己完整的規劃跟測試策略，不該跟這次的稽核修正批次混在一起。

## 後果

- `_rollback()` 現在同時存在兩種回滾動作的描述方式：舊的旗標式（四類）
  跟新的 journal 式（兩類）。這是刻意接受的過渡狀態，不是遺漏——下一次
  有人要新增新一類可回滾的安裝動作時，應該優先考慮用 `InstallJournal`，
  不要再新增旗標參數。
- 如果之後真的要把剩下四類也遷移過去，預期的改動範圍：`_register_uninstall_entry()`/
  `_create_shortcut()`/`file_assoc.register()`/`_add_to_path_env()`
  這幾個呼叫點都要改成呼叫成功後立刻 `journal.record(...)`，
  `_rollback()` 的簽名跟四個既有旗標參數可以整個拿掉，改成單一個
  `journal` 參數；對應的測試（`TestRollbackCoversSystemEntries` 等）
  也要跟著重寫斷言方式（從「檢查呼叫端傳了哪個旗標」改成「檢查 journal
  裡記錄了哪些復原動作」）。這份工作量足夠獨立成一次自己的實作跟驗證
  批次，不在這份 ADR 的範圍內先行預估細節。
- 未來的架構稽核不需要重新提案「要不要用 apply/undo 物件取代旗標式
  回滾」這個方向本身——這份決定已經確認方向是對的，只是分階段導入，
  重新提案時應該接續這裡的進度（「四類還沒遷移」），而不是重新論證
  要不要做。
