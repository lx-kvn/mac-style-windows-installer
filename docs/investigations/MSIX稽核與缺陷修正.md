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
- [D2 副檔名的字元從未被驗證](#d2-副檔名的字元從未被驗證)
  - [重現方式](#重現方式-1)
  - [機制](#機制-1)
  - [方案評估](#方案評估-1)
  - [實際做了什麼](#實際做了什麼-1)
  - [驗收](#驗收-1)
- [S3 與 S4 兩項小型加固](#s3-與-s4-兩項小型加固)
  - [S3 解密後的解壓沒有落點檢查](#s3-解密後的解壓沒有落點檢查)
  - [S4 檔案 URI 沒有做百分比編碼](#s4-檔案-uri-沒有做百分比編碼)
  - [驗收](#驗收-2)
- [D3 已安裝的 MSIX 套件沒有被偵測](#d3-已安裝的-msix-套件沒有被偵測)
  - [重現方式](#重現方式-2)
  - [機制](#機制-2)
  - [方案評估](#方案評估-2)
  - [實際做了什麼](#實際做了什麼-2)
  - [驗收](#驗收-3)
- [S2 下載回來的載入器執行前沒有驗證](#s2-下載回來的載入器執行前沒有驗證)
  - [重現方式](#重現方式-3)
  - [機制](#機制-3)
  - [方案評估](#方案評估-3)
  - [一個實測推翻的預設](#一個實測推翻的預設)
  - [實際做了什麼](#實際做了什麼-3)
  - [驗收](#驗收-4)
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

## D2 副檔名的字元從未被驗證

嚴重程度：中。失敗發生在流程尾端，錯誤訊息不指向副檔名欄位；其中一種輸入會
使檔案被寫到組裝目錄之外。

### 重現方式

於檔案關聯欄位填入以下任一內容，皆通過打包階段的驗證：

| 輸入 | 推導出來的關聯群組名／檔名 | 後果 |
| --- | --- | --- |
| `.my ext` | `my ext` | 含空白，不符合 `Name` 屬性的規定 |
| `.中文` | `中文` | 非 ASCII |
| `.a"b` | `a"b` | 引號 |
| `.` + 80 個字元 | 80 個字元 | 超過 64 字元上限 |
| `..\..\evil` | 圖示檔名 `doc_\..\evil.png` | 圖示被複製到組裝目錄之外 |

前四種於 `makeappx` 階段失敗，錯誤訊息不指向副檔名欄位。第五種不報錯。

以上為實際呼叫 `msix_manifest.association_group_name()` 與
`association_logo_name()` 取得的結果，非推論。

### 機制

副檔名這個概念的規則散在四個地方各自實作：

1. `packaging_core` 解析使用者輸入的清單——只做「補上開頭的點、轉小寫」
2. `file_assoc.prog_id()` 推 ProgID
3. `builder.py` 推傳統引擎的內嵌圖示檔名 `doc_icon_<副檔名>.ico`
4. `msix_manifest.association_group_name()`／`association_logo_name()` 推套件
   清單的關聯群組名與套件內的圖示檔名

四處皆未檢查字元集。`association_group_name()` 的註釋寫著「字元集的檢查留在
驗證階段」——專案裡不存在那個階段。此為「規則沒有一個歸屬處」的典型後果：
每一處都假定驗證由別處負責。

路徑穿越的成立條件為第 3、4 點——推導出來的字串被直接用作檔名
（`shutil.copy(source, os.path.join(staging_dir, name))`）。輸入來源是打包者
自己的設定檔而非終端使用者，因此不構成可遠端利用的漏洞；列為缺陷的理由是
`sdk_tools._safe_extract_bin()` 對一份已通過 SHA-256 驗證的檔案尚且檢查解壓
落點，同一條原則在此處未被套用。

### 方案評估

**方案一（採用）：抽出 `file_extension.py`，規則與四個推導集中於此。**
推導函式一律先驗證再產出——推導是最後一道防線，驗證被繞過時不該安靜地產出
一個會被當成路徑使用的字串。

**方案二（不採用）：只在 `packaging_core` 加一段字元檢查。** 這修得了本次
的五種輸入，但四個推導點仍各自實作、仍無歸屬，下一個新增的推導點會重複同樣
的假定。D2 的成因不是漏了一段檢查，是沒有一個地方負責這件事。

**方案三（不採用）：於推導時靜默替換不合法的字元。** 會產生一個與使用者輸入
對不起來的群組名稱，且他不會知道。

**字元集的界定。** 長度上限 64、全小寫、不含空白，取自 Microsoft 對
`uap:FileTypeAssociation` 的 `Name` 屬性的規定（原文為「A string between 1
and 64 characters in length」與「must be all lower case characters with no
spaces」）。字元集限於英文字母、數字、句點、連字號、底線，則是本工具自訂的
限制，不宣稱為格式的規定：官方文件未載明 `Name` 與 `uap:FileType` 的字元集，
依推測放寬等同作出無人驗證的承諾；且該字串同時會成為檔名。實際會用到的形式
（`.txt`、`.tar.gz`、`.7z`、`.my-type`）皆在此集合內。

### 實際做了什麼

1. 新增 `file_extension.py`：`normalize()`（形狀）、`validate()`（判斷）、
   `parse_list()`（使用者輸入的整串解析），以及四個推導 `prog_id()`、
   `traditional_icon_name()`、`msix_group()`、`msix_logo_name()`。推導函式
   對未通過驗證的值拋 `InvalidExtension`。
2. `packaging_core`：清單解析改呼叫 `parse_list()`，錯誤加上既有的「欄位驗證
   失敗」前綴後回傳。`doc_icons` 的鍵改用 `normalize()`。
3. `file_assoc.prog_id()`、`msix_manifest.association_group_name()`／
   `association_logo_name()`、`builder.py` 的內嵌圖示檔名，四處改為轉呼叫
   新模組。前三者保留原名——那些名字是 CONTEXT.md 與 ADR 記載過的對齊點。
4. `packaging_core.SHARED_DEEP_MODULES` 加入 `file_extension.py`。`file_assoc`
   會匯入它，漏加的話 frozen exe 產出的安裝檔一執行即 `ModuleNotFoundError`。
5. `CONTEXT.md` 新增「副檔名（file_extension.py）」一節，含四個推導的對照表。

**附帶修正的一項**：重複填寫的副檔名（`txt, .TXT`）原本會產生兩筆關聯，於
MSIX 下即兩個同名的關聯群組，使套件清單無效。`parse_list()` 收斂為一項並保留
第一次出現的位置。

### 驗收

新增測試 34 項：`tests/test_file_extension.py` 29 項（正規化、驗證、清單解析、
四個推導的既有慣例、推導對未驗證輸入拋例外），`tests/test_packaging_core.py`
5 項（空白、路徑穿越、非 ASCII、超長、重複收斂皆於打包階段被擋下）。

全套測試 `1489` 項通過。

## S3 與 S4 兩項小型加固

兩項皆非可被外部利用的漏洞，列入的理由是同一份專案內對同一條原則的處置不
一致。合併為一節，因為兩者各自只有數行。

### S3 解密後的解壓沒有落點檢查

`install_encryption.decrypt_to_directory()` 原本以 `zipfile.extractall()`
解壓。該 zip 由 `encrypt_directory()` 以 `os.walk` 產生，項目名稱皆為相對
路徑，因此現況並無穿越。

列為問題的理由是 `sdk_tools._safe_extract_bin()` 對一份**已通過 SHA-256
驗證**的下載檔案尚且逐項檢查落點，其註釋載明理由為「不該由『檔案內容可信』
推導出『可以把它寫到它自己指定的任何路徑』」。此處的密文來源是安裝檔本身，
而安裝檔會被傳來傳去，同一條原則沒有理由不適用。

**處置**：新增 `_extract_within()` 逐項解壓並檢查落點，不合格者拋
`UnsafeArchiveEntry`（與 `WrongPasswordError` 分開——密碼錯誤是使用者可以
自行處理的事，這一項代表內容不對，重試沒有意義）。並將 `encrypt_directory()`
拆出 `_write_encrypted()`，使「壓成 zip」與「加密並落地」各自可測。

**不沿用 `extractall()` 的理由**：它會把不合法的項目名稱靜默地改成合法的
（去掉開頭的斜線、丟掉 `..`），結果是檔案落在與封裝時不同的位置而無人知道。

**一個不直覺之處**：絕對路徑與帶磁碟機代號的項目必須獨立擋下，不能只靠落點
比對。實測 `os.path.join(dest, "C:", "x")` 的結果是 `dest\x`——`os.path.join()`
把 `C:` 當成磁碟機規格處理，該項目因此安靜地落回目的地底下。沒有穿越，但也
沒有出聲，而「安靜地改寫成另一個位置」正是不採用 `extractall()` 的理由本身。

### S4 檔案 URI 沒有做百分比編碼

`msix_deploy._file_uri()` 原本以字串直接相接組出 `file:///<路徑>`。套件路徑
來自 `sys._MEIPASS`，即使用者的 `%TEMP%`，其中含使用者名稱——而 Windows 帳號
名稱允許 `#`。`#` 在 URI 中是片段的起點，路徑會自該處被截斷，部署因此找不到
檔案，且錯誤訊息不會提到帳號名稱。`%` 與空白同理。

**處置**：改以 `urllib.parse.quote(path, safe="/:")` 編碼。`safe` 保留磁碟機
代號的冒號與路徑分隔符——編碼過頭會讓 URI 不再指向同一個檔案，那與不編碼是
同一種錯誤的另一半。

並將函式拆為兩支：`_file_uri()` 產生字串（純函式，可測），`_deployment_uri()`
負責包成 `Uri` 物件。原本兩條路徑（有無 winrt）各自組一次字串，那是兩份會
分頭漂移的相同邏輯，且有 winrt 的那一條無法在測試中檢查其編碼結果。

### 驗收

新增測試 7 項：`tests/test_msix_deploy.py` 5 項（`#`、`%`、空白各自被編碼，
磁碟機冒號與分隔符不被編碼，scheme 不變），`tests/test_install_encryption.py`
2 項（穿越項目與絕對路徑項目皆被拒絕，且檔案未落在目的地之外）。

全套測試 `1496` 項通過。

## D3 已安裝的 MSIX 套件沒有被偵測

嚴重程度：中。同版本重裝與降版直接失敗，使用者拿到的是系統的原始錯誤碼。

### 重現方式

一台已經以 MSIX 引擎安裝過某應用程式的機器，執行同一版本（或更舊版本）的
安裝檔。部署失敗，訊息為系統的錯誤碼，不指出「這台電腦上已經有同一個應用
程式的套件」這個成因。

版本較新的情形不受影響：Windows 對版本遞增的套件本來就會就地更新。

### 機制

`msix_install.run()` 的 `check_existing` 接的是
`installer_core.check_existing_install`，該函式查的是登錄表——只看得到
**傳統模式**的既有安裝。同一個 identity 已經以 MSIX 裝過的情形完全沒有分支，
直接交給 `add_package_async`。

`msix_deploy` 裡已有 `find_installed()` 與 `remove()`，兩者皆有測試，皆無
產品端呼叫者。此為「接縫設計好了但沒有插線」的形狀：兩支函式的存在會讓
閱讀者以為這條路已經走過。

安裝端另有一個前置問題：套件身分名稱（identity）只有打包端知道，它不由
`app_name` 推導（[ADR-0007](../adr/0007-package-identity-name-is-an-explicit-required-field.md)），
而 `installer_config.json` 沒有帶這個值——即使接上查詢，安裝端也不知道要查
哪一個名字。

### 方案評估

**方案一（採用）：查得到就把話說清楚，不自動移除。** 部署失敗且確實查到同名
套件時，在系統的錯誤訊息之後附上一段，指出已安裝的套件完整名稱、說明版本
較新時系統會自動就地更新（因此這個失敗通常代表同版本或更舊），並告知先到
「設定 → 應用程式」解除安裝再重跑。

**方案二（暫不採用，待實機驗證）：部署失敗時自動移除舊套件並重試一次。**
這能讓重裝與降版直接成功，但前提是失敗原因確實是同名套件已存在。失敗也可能
來自別的原因（憑證不受信任、磁碟空間不足），那時移除等於白白弄丟使用者的
既有應用程式，而重試照樣失敗。此方案的取捨取決於 `add_package_async` 對
同版本與降版的真實錯誤形態是否可與其他失敗區分——那屬於
`CLAUDE.md`「CI 驗不到的事情」四類中的第一類，須於 Windows 11 虛擬機實測。
列入待辦。

**方案三（不採用）：刪除 `find_installed()` 與 `remove()`。** 對未被呼叫的
程式碼，刪除是預設處置；此處不適用的理由是 `find_installed()` 於本次即接上，
而 `remove()` 是方案二唯一需要的東西，方案二尚未被否決、只是尚未驗證。

**查詢失敗的處置**：`_find_installed()` 吞掉例外並回傳 None。這個結果只用來
把訊息講清楚，不是流程的必要條件——查詢失敗不該讓一次本來會成功的安裝失敗。

### 實際做了什麼

1. `msix_install.run()` 新增 `find_installed_package` 參數（可注入的查詢，
   比照本模組其餘三件事的作法）。查在部署之前執行一次：那一行事前告知讓
   使用者知道安裝程式在動一個已經存在的東西是預期中的步驟。
2. 部署失敗且查到同名套件時，於系統的 `error_text` 之後附加說明。附加而非
   取代——系統給的說明是完整且已在地化的，自己另編一則只會失去資訊。
3. `builder.build_all()` 新增 `msix_identity_name` 參數，寫進
   `installer_config.json`；`builder_cli.py` 與 `gui_config.py` 兩個呼叫端
   都從 `msix` 區塊取值。
4. `installer_core` 讀取該欄位，並在有值時把 `msix_deploy.find_installed()`
   接上去。沒有值時不接（舊版工具編出來的安裝檔沒有這個欄位）——拿空字串
   去比對只會得到一個沒有意義的答案。

### 驗收

新增測試 11 項：`tests/test_msix_install.py` 8 項（失敗時指名已安裝的套件、
保留系統原本的說明、指出去哪裡移除、查不到時訊息不變、事前告知、只查一次、
沒有傳這個參數時行為不變、查詢拋例外不影響一次會成功的安裝）；
`tests/test_builder.py` 1 項（identity 進得了設定檔）；
`tests/test_installer_core_misc.py` 2 項（設定檔的 identity 到得了查詢、
沒有 identity 時不接這條線）。

全套測試 `1507` 項通過。

## S2 下載回來的載入器執行前沒有驗證

嚴重程度：中。執行位置在終端使用者的機器上，且安裝檔常為已提升權限。

### 重現方式

無法以一般操作重現——這是一條「攻擊者做得到什麼」的路徑，不是一個會自行
顯現的錯誤。`webview2_runtime.acquire()` 自
`https://go.microsoft.com/fwlink/p/?LinkId=2124703` 下載載入器至暫存目錄，
隨即 `subprocess.run([path])`。執行之前的把關只有 HTTPS 與 Content-Length
比對，後者防的是「下載被截斷」，不是「下載回來的是不是那個東西」。

### 機制

判準已載於 `sdk_tools.py` 的模組說明：「判準不是『打包時是否連網』，而是
下載物在打包機器上是被內嵌還是被執行——後者的最壞情況是打包機器遭入侵」。
`webview2_runtime` 執行下載物的位置是終端使用者的機器，同一條判準推出來的
強度應該更高，而實際上更低。

`sdk_tools` 採用的手段（釘死版本加 SHA-256）在此不適用：Evergreen 載入器是
內容會變動的永久連結，釘不住雜湊。可以驗的是簽章。

### 方案評估

**方案一（採用）：兩道關卡——`WinVerifyTrust` 加上簽章者的組織名稱。**
只驗第一道的話，任何一張有效憑證簽出來的檔案都會通過，而遭竊的程式碼簽章
憑證是真實存在的東西。

**組織名稱的比對不能用子字串。** `O=Microsoft Corporation Fake` 含有
`O=Microsoft Corporation` 這段文字，以 `in` 判斷會放行——而取得一張那樣的
憑證比取得一張真的微軟憑證容易得多。比對以完整的 RDN 為單位進行。

**不做撤銷檢查**（`WTD_REVOKE_NONE`）。撤銷檢查需要連到憑證機構的
OCSP／CRL 端點，而這段程式碼執行的時機正是使用者網路狀況本來就有問題的
時候（他正在下載一個缺失的元件）。逾時會讓安裝流程停頓數十秒，而使用者
看到的是一個沒有任何說明的停頓。

### 一個實測推翻的預設

`dwProvFlags` 最初帶了 `WTD_SAFER_FLAG`（0x100）——搭配 `WTD_UI_NONE` 一起
帶上它是常見的建議。**實測結果推翻了這個選擇。**

對真正的 `MicrosoftEdgeWebview2Setup.exe`（自上述永久連結下載，
1,783,000 位元組）：

| `dwProvFlags` | `WinVerifyTrust` 回報 |
| --- | --- |
| `WTD_SAFER_FLAG` | `0x800B0109`（CERT_E_UNTRUSTEDROOT） |
| `0` | `0`（通過） |

同一支檔案的簽章者主體為
`CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US`。
也就是該旗標會把正版的載入器判成不受信任。

這不是可以擇一的取捨：**一道會拒絕正版檔案的驗證比沒有驗證更糟**——使用者
永遠取得不到 WebView2，而錯誤訊息指向的是簽章，不是這個旗標。因此
`dwProvFlags` 固定為 0。`WTD_UI_NONE` 本身已經足夠：需要使用者自行判斷的
檔案會回報非 0，而本模組只把 0 當成通過。

此事亦說明為何這一項不能只靠單元測試收工：測試用的樣本是本機的 Python
直譯器，它帶 `WTD_SAFER_FLAG` 時通過，缺陷只有在對真正的目標檔案驗證時
才顯現。

### 實際做了什麼

1. 新增 `authenticode.py`：`verify_file(path, expected_organization=None)`
   回傳 `(是否通過, 說明)`，不拋例外——呼叫端在所有失敗形態下的處置相同
   （不要執行這個檔案）。主體字串的轉換重用
   `cert_subject.subject_string_from_der()`。
2. `webview2_runtime.acquire()` 新增 `verify_fn` 這道步驟（可注入，比照該
   模組既有的 registry／sleep 接縫），預設為 `verify_bootstrapper()`，
   期望的組織為 `Microsoft Corporation`。未通過即刪除該檔案——留著的話，
   下一次就可能被當成「已經下載過」而直接執行，這與 `download()` 刪除半截
   檔案是同一個理由。
3. `packaging_core.SHARED_DEEP_MODULES` 加入 `authenticode.py` 與
   `cert_subject.py`。

### 驗收

新增測試 18 項：`tests/test_authenticode.py` 12 項（組織名稱比對的七種情形
含「較長的組織名稱不得相符」、檔案不存在、無簽章、有效簽章通過、組織不符
被擋下）；`tests/test_webview2_runtime.py` 6 項（未通過驗證的檔案不被執行、
未通過的檔案被刪除、通過的才執行、驗證收到的是下載回來的那個檔案、期望的
簽章者是微軟、沒有注入替身時走的是真的驗證）。

**實機驗證**：實際下載真正的 `MicrosoftEdgeWebview2Setup.exe` 並通過本模組
的兩道關卡（簽章有效、簽章者為 `O=Microsoft Corporation`），換一個組織名稱
則被擋下。此步驟即為上一節那個實測的來源。

全套測試 `1525` 項通過。

## 已知限制

- MSIX 模式下的安裝密碼保護仍未實作，目前的處置是於打包階段擋下並說明。
- D1 的安裝端修正處理的是修正之前已經編出去、仍在外面的安裝檔。那些安裝檔
  仍然無法安裝——修正使它們說出原因，不使它們可用。
- D3 的處置只讓失敗說出原因，不讓重裝與降版成功。使用者仍須自行先到
  「設定 → 應用程式」移除既有套件。
- S2 的驗證不做憑證撤銷檢查，理由見該節。因此一張已遭撤銷但仍在有效期內的
  微軟憑證所簽的檔案會通過。
- S2 的兩道關卡皆在安裝端執行。若未來微軟改以另一個組織名稱簽署載入器，
  既有的安裝檔會開始拒絕它。`BOOTSTRAPPER_ORGANIZATION` 因此獨立成一個常數。

## 待辦清單

- **`install_engine` 加深為引擎相容性的唯一答案。** D1 的成因不是漏掉一個
  欄位，而是「這個設定在這個引擎下能不能用」這件事同時存在於三處：
  `install_engine._FIELD_CATEGORIES`、`packaging_core` 的 msix 區塊與圖示檢查、
  以及 `builder.build_all()` 中六處 `is_msix` 分支。每新增一個打包欄位即多
  一次同類缺口的機會。
- **於 Windows 11 虛擬機量測 `add_package_async` 對同版本與降版的錯誤形態**，
  據以決定是否採用 D3 的方案二（部署失敗時自動移除舊套件並重試一次）。
  判準為該失敗是否可與其他失敗原因（憑證不受信任、磁碟空間不足）區分——
  不可區分時自動移除會弄丟使用者的既有應用程式而重試照樣失敗。
  `msix_deploy.remove()` 為此保留，目前無產品端呼叫者。
- S1（簽章憑證密碼經由命令列參數傳給 `signtool`）之處置。該項會新增打包設定
  欄位，屬新功能，依 `CLAUDE.md`「製作新功能前要先問清楚需求」先行確認需求。

## 已完成之待辦

- D1 安裝密碼保護在 MSIX 引擎下產出無法安裝的安裝檔（2026-09-05）。
- D2 副檔名的字元從未被驗證（2026-09-05）。
- S3 解密後的解壓沒有落點檢查（2026-09-05）。
- S4 檔案 URI 沒有做百分比編碼（2026-09-05）。
- D3 已安裝的 MSIX 套件沒有被偵測（2026-09-05，部分：偵測與告知已完成，
  自動移除待虛擬機驗證，見待辦清單）。
- S2 下載回來的載入器執行前沒有驗證（2026-09-05）。
