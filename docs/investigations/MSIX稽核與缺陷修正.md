# MSIX 稽核與缺陷修正

2026-09-05 對 MSIX 輸出功能（`msix_*.py`、`cert_subject.py`、`sdk_tools.py`、
`webview2_runtime.py`）及其與 `builder.py`／`packaging_core.py`／
`installer_core.py` 接合處所作的架構稽核，連同稽核中發現的缺陷與資料安全問題
之修正紀錄。

稽核基準為 `1439` 個測試全數通過。稽核採靜態閱讀，輔以對可疑之處的實際探測
（例如副檔名的字元處理以實際呼叫確認，見 D2）。

## 目錄

- [稽核範圍與方法](#稽核範圍與方法)
- [D1 安裝密碼保護在 MSIX 引擎下產出無法安裝的安裝檔](#d1-安裝密碼保護在-msix-引擎下產出無法安裝的安裝檔)
  - [重現方式](#重現方式)
  - [機制](#機制)
  - [方案評估](#方案評估)
  - [實際做了什麼](#實際做了什麼)
  - [驗收](#驗收)
- [已知限制](#已知限制)
- [待辦清單](#待辦清單)
- [已完成之待辦](#已完成之待辦)

## 稽核範圍與方法

範圍以「最近新增」為準，即 MSIX 輸出功能自 `feat(installer)` 系列提交起
所引入的模組，及其為了接上既有流程而修改的位置。稽核並涵蓋使用者要求的
三個面向：缺陷、漏洞、資料安全。

方法為逐檔閱讀加上針對性探測。不以測試套件的通過與否作為判準——本稽核發現的
三項缺陷在稽核當下皆處於「測試全綠」的狀態，其中 D1 與 D2 的成因正是測試涵蓋
的是各模組自身的行為，而缺陷發生在模組之間的接合處。

## D1 安裝密碼保護在 MSIX 引擎下產出無法安裝的安裝檔

嚴重程度：高。打包階段無任何徵兆，產出的安裝檔在所有機器上皆失敗。

### 重現方式

一份打包設定同時滿足兩個條件：

1. `install_engine` 為 `msix`
2. 啟用安裝密碼保護（三種填法任一：GUI 的勾選框 `need_install_password`、
   GUI 的直接輸入 `install_password`、設定檔的 `install_password_env`）

打包成功，無任何警告。產出的安裝檔啟動後顯示密碼關卡，輸入任何密碼皆失敗。

### 機制

`builder.build_all()` 決定內嵌內容的位置是一組三選一的分支：

```python
if is_msix:
    cmd.append(f"--add-data={embedded_msix};.")
elif password_protected:
    install_encryption.encrypt_directory(app_dir, temp_encrypted_payload, ...)
    cmd.append(f"--add-data={temp_encrypted_payload};.")
else:
    cmd.append(f"--add-data={app_dir};app_contents")
```

選定 MSIX 引擎時，加密那一條永遠不會執行，因此 `app_contents.enc` 不存在於
產出的安裝檔中。但同一個函式稍前組出的 `installer_config.json` 無條件寫入
`"password_protected": password_protected`——該值仍為真。

安裝端據此顯示密碼關卡，`verify_install_password()` 隨即開啟一個不存在的
檔案。原本的行為是 `open()` 拋出 `FileNotFoundError`，而該例外沒有任何
接住的地方：

- GUI：`ui/index.html` 的 `submitPassword()` 未包覆 `await`，Promise 遭拒絕
  後按鈕毫無反應，畫面停在密碼關卡且無任何回饋。
- 靜默安裝：`installer_core.run_silent_install()` 以未處理例外收場，無人
  值守的呼叫端只拿到一段追蹤訊息。

**為何未被既有機制攔下。** `install_engine.py` 這個模組的職責即為「哪些設定
在這個引擎下能用」，其 `_FIELD_CATEGORIES` 表登記了十二個欄位，但不含安裝
密碼保護的任何一個欄位。該模組的介面宣稱回答這個問題，實作只涵蓋一部分——
此為本次稽核架構部分的主要發現，詳見「待辦清單」第一項。

### 方案評估

**方案一（採用）：打包階段擋下，並使安裝端對此情形有自己的出口。** 分類為
第二類（尚未支援）而非第三類（格式限制）：MSIX 模式做得到這件事——把已簽章
的套件加密內嵌、驗證通過後解密再交給系統部署——只是第一版不做。依
`install_engine.py` 模組說明所載的區分原則，講成格式限制會使後續維護者認定
此路不通。

**方案二（不採用）：於 MSIX 模式下實作密碼保護。** 該功能需要在部署之前把
套件解密到暫存位置，涉及暫存明文套件的生命週期管理，屬新功能而非缺陷修正。
本次不擴大範圍。

**方案三（不採用）：MSIX 模式下靜默忽略密碼設定，將 `password_protected`
寫為假。** 使用者設定了保護卻沒有得到保護，且不會知道。這比目前的失敗更糟：
目前至少會失敗。

安裝端的處置不採用「回傳 False」：那等同告訴使用者密碼錯誤，而他不管輸入
什麼都不會成功，會一直重試一件不可能成功的事。因此改為拋出具名例外，由兩個
呼叫端各自說出真正的原因。

### 實際做了什麼

1. `install_engine.py`：`_FIELD_CATEGORIES` 新增 `install_password`
   （`UNSUPPORTED`），並新增 `_PASSWORD_FIELDS` 常數列出三個來源欄位。
   `check_settings()` 對這三個欄位任一有值即產生一則違規項——只產生一則，
   因為三者描述的是同一個功能，逐項列出會讓使用者以為要修三件事。
   訊息表補上 `field.install_password` 的中英文版本。
2. `ui/config.html`：`ENGINE_FIELD_TARGETS` 新增對應項，密碼欄位所在的
   `form-group` 補上 `id="group_install_password"`。前端不自行維護欄位清單，
   `tests/test_gui_engine_linkage.py` 會比對兩份是否一致。
3. `installer_core.py`：新增 `MissingEncryptedPayloadError`。
   `verify_install_password()` 在加密內容不存在時拋出它而非開啟檔案失敗。
   `run_silent_install()` 接住它，寫進紀錄檔並回傳非 0。
4. `ui/index.html`：`submitPassword()` 包覆 `await` 並顯示 `password_unusable_error`
   這則與「密碼錯誤」可區分的訊息（中英文皆備）。該分支不清空輸入框、不重新
   聚焦——那是「再試一次」的邀請，而重試不會成功。

### 驗收

新增測試 16 項，涵蓋：三種密碼來源各自被擋下、三者同時存在時只產生一則違規
項、訊息使用「尚未支援」語氣並指向傳統引擎、傳統引擎不受影響、GUI 靜態分類
含此欄位；安裝端的例外型別、暫存資料夾不殘留、靜默安裝的紀錄檔不出現「密碼
錯誤」字樣；前端的 `try`/`catch` 存在、訊息鍵兩種語言皆備、`catch` 分支不邀請
重試。

全套測試 `1455` 項通過（稽核基準為 `1439`）。

## 已知限制

- MSIX 模式下的安裝密碼保護仍未實作，目前的處置是於打包階段擋下並說明。
- D1 的安裝端修正處理的是修正之前已經編出去、仍在外面的安裝檔。那些安裝檔
  仍然無法安裝——修正使它們說出原因，不使它們可用。

## 待辦清單

- **`install_engine` 加深為引擎相容性的唯一答案。** D1 的成因不是漏掉一個
  欄位，而是「這個設定在這個引擎下能不能用」這件事同時存在於三處：
  `install_engine._FIELD_CATEGORIES`、`packaging_core` 的 msix 區塊與圖示檢查、
  以及 `builder.build_all()` 中六處 `is_msix` 分支。每新增一個打包欄位即多
  一次同類缺口的機會。
- 稽核其餘項目（D2、D3、S2、S3、S4）之修正。
- S1（簽章憑證密碼經由命令列參數傳給 `signtool`）之處置。該項會新增打包設定
  欄位，屬新功能，依 `CLAUDE.md`「製作新功能前要先問清楚需求」先行確認需求。

## 已完成之待辦

- D1 安裝密碼保護在 MSIX 引擎下產出無法安裝的安裝檔（2026-09-05）。
