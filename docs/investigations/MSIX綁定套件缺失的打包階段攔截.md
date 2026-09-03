# MSIX 綁定套件缺失的打包階段攔截

**狀態：已完成（2026-09-03）。**

修正基準：`main` 分支，工作區乾淨，
`python -m unittest discover -s tests -p "test_*.py"` 全數通過。

相關文件：[`docs/proposals/MSIX輸出規劃.md`](../proposals/MSIX輸出規劃.md)、
[`docs/investigations/CI與本機虛擬機的能力對比.md`](CI與本機虛擬機的能力對比.md)、
規格文件 §8.37。

## 重現方式

在繁體中文 Windows 11 主機上，以 MSIX 引擎打包一份 `Setup.exe`，工具全程
回報成功。把該安裝檔送進 Windows 10 1809（Enterprise LTSC 2019，17763.316）
虛擬機靜默安裝：

```
Setup_XXX.exe /S /LOG=install.log
```

安裝紀錄內容為：

```
=== 2026-09-03T14:48:25 靜默安裝 ===
[錯誤] 安裝失敗：無法使用 Windows 的套件部署介面：No module named 'winrt'。
       這個功能需要 winrt-* 綁定套件，安裝檔在打包時應該已經一併帶上它。
```

當時的環境條件全部正確：簽章憑證已匯入 `LocalMachine\Root` 與
`TrustedPeople`，`AllowAllTrustedApps = 1`。失敗純粹來自安裝檔內部缺少該
綁定套件，與目標機器的狀態無關。

打包機器的條件：未安裝 `winrt-*` 系列套件。

## 機制分析

四件事疊在一起，使這個缺陷可以一路走到終端使用者手上：

1. **匯入是延遲的。** `msix_deploy.py` 的 `_default_manager()` 在函式內部才
   `from winrt.windows.management.deployment import PackageManager`。這是既有
   的設計決定，理由是讓傳統引擎的安裝檔不必綁上這個相依（見
   `packaging_core.py` 的 `SHARED_DEEP_MODULES` 註解）。延遲匯入的代價是
   缺少時不會在匯入階段顯現，而要等到真正呼叫部署介面那一步。
2. **打包環境根本沒有那個套件。** PyInstaller 做靜態分析，套件不存在時沒有
   東西可以收集，產物因此少了那五個模組。這一步不會失敗，也不會警告。
3. **打包流程沒有任何一處檢查這件事。** 引擎相容性檢查（`install_engine.py`）
   問的是「這份設定與這個引擎相不相容」，`msix_settings.validate()` 問的是
   「套件清單的必填欄位齊不齊」，兩者都不問「這台機器裝了什麼」。
4. **版本庫沒有相依宣告檔。** 這五個套件只出現在
   `.github/workflows/test-packaging-options.yml` 的「安裝相依」步驟裡
   （`winrt-runtime==3.2.1` 等五項）。本機開發者沒有任何管道會知道要裝。

CI 涵蓋不到這個缺陷，因為 CI 每次都明確安裝那五個套件——CI 綠燈不代表本機
編出來的安裝檔可用。這與
[`CI與本機虛擬機的能力對比.md`](CI與本機虛擬機的能力對比.md) 記錄的差距
是同一類：CI 的環境是每次重新架設的，本機的環境是長期累積的。

## 方案評估

### 一、偵測對象：工具自己的行程，或編譯安裝檔的那個直譯器

既有的相依檢查（安裝密碼保護對 `cryptography`，
`packaging_core._encryption_backend_available()`）是在工具自己的行程裡
`import cryptography`。比照辦理是最小的改動。

不採用，因為判準錯誤：加密是在工具自己的行程裡進行的，行程內的 `import`
正是對的判準；而 `winrt-*` 需要出現在**編譯安裝檔的那個 pyinstaller 子行程**
背後的 Python 裡。兩者在以下情形分岔：

- 工具以 frozen exe 形式執行（`InstallerBuilder.exe` 或 `mswi-cli.exe`）時，
  它自己的行程裡永遠沒有 `winrt-*`（`packaging_core.py` 不匯入
  `msix_deploy.py`，該檔只以 `--add-data` 內嵌，不經靜態分析）。以行程內的
  `import` 當判準，會把每一次 MSIX 打包都誤判成缺套件，等於讓這個功能在
  發布出去的工具上完全不能用。
- 反過來把 `winrt-*` 也收進工具自己那顆 exe，則檢查恆為通過，完全偵測不到
  真正的問題（子行程的環境缺套件），並且讓工具多出約 800 KB 的負擔。

採用的是既有的另一個做法：`check_build_environment()` 已經以子行程探測
`pywebview` 與 `pywin32` 是否安裝在外部直譯器上。第三個探針掛進同一個子行程，
不增加額外的行程啟動成本（§8.8）。

### 二、攔截點：純函式內部，或呼叫端

`validate_and_build_pack_data()` 是純函式，其說明明確記載不呼叫
`check_build_environment()` 這類有外部副作用的檢查。把子行程探測放進去會
推翻那個性質，並使 `tests/test_gui_msix_engine.py` 等測試的結果取決於執行
機器上裝了什麼。

採用的是與 `builder.missing_workspace_resources()` 同一個形狀：
`packaging_core.missing_engine_dependencies(engine, env, lang)` 是純函式，
環境的答案由呼叫端問到之後傳進來，配置精靈與 CLI 各自在動手之前呼叫一次。

`env` 缺少 `msix_backend_found` 這個鍵時視為「沒有」。成因只有兩種：環境
檢查換過形狀而這裡沒跟上，或呼叫端傳了一份不是 `check_build_environment()`
產出的字典——兩者都不足以支持「套件在」這個結論。

### 三、hidden import 是否必要

規劃文件第三輪 spike 記載「PyInstaller 未需要任何額外設定即正確收集
`winrt-*` 套件」，但該探針的匯入位於模組頂層，不能直接推論到延遲匯入。

實測（2026-09-03，PyInstaller 6.18.0、Python 3.13、winrt-* 3.2.1）：以一支
只 `import msix_deploy` 的腳本做 `--onefile` 打包，產物的 `Analysis-00.toc`
包含 `winrt.windows.management.deployment` 等全部五個套件，以及
`_winrt_windows_management_deployment.cp313-win_amd64.pyd` 等原生模組。
結論：PyInstaller 的靜態分析會跟進函式內部的 `import`，不需要在
`builder.py` 或 `build_config_tool.py` 補宣告。

### 四、相依宣告的形式

採用 `requirements.txt`。工作流程改為 `pip install -r requirements.txt`，
不各自列一份套件名稱——各自列一份的代價不是多打幾個字，而是那幾份清單會
各自演化，其中一處加了套件而其他幾處沒跟上時，症狀是某個工作流程莫名其妙
失敗，或者更糟：通過了，但通過的原因與本機不同。

## 實際做了什麼

依 TDD 順序（測試先紅、再寫實作轉綠）：

| 檔案 | 改動 |
|---|---|
| `packaging_core.py` | 探針多印 `MSIX_BACKEND_OK`；回傳結構新增 `msix_backend_found`（不進 `ready`）；新增 `missing_engine_dependencies()`；訊息表新增 `msix.missing_dependency`（`zh-TW`／`en`） |
| `gui_config.py` | `start_pack()` 在驗證通過之後、準備工作目錄之前呼叫一次 |
| `builder_cli.py` | `cmd_pack()` 同上。`pack-msix` 不加這道檢查：那條路只產出 `.msix`，不編 bootstrapper exe，安裝端的部署介面與它無關 |
| `requirements.txt` | 新增。五個綁定套件鎖定 `==3.2.1`，其餘四項不鎖版本 |
| `.github/workflows/build.yml`、`test-packaging-options.yml` | 三處 `pip install` 改為 `-r requirements.txt` |
| `README.md`、`CLI_USAGE.md`、`docs/使用說明書.md`、`docs/規格文件.md`、`build_config_tool.py` | 安裝指示改為指向 `requirements.txt` |

新增與修改的測試：

- `tests/test_packaging_core.py`：探針回報 `msix_backend_found`、缺少時不影響
  `ready`、`missing_engine_dependencies()` 的六條分支（含訊息內容與語言）。
- `tests/test_gui_msix_engine.py`：缺少綁定套件時 `start_pack()` 回報錯誤且
  `build_msix`／`build_all` 都沒有被呼叫；傳統引擎不受影響。
- `tests/test_builder_cli.py`：`pack` 在同樣情形下以非零 exit code 結束，且
  訊息先於工作目錄檢查出現。
- `tests/test_dependency_manifest.py`（新增）：`requirements.txt` 存在、五個
  綁定套件都列出且鎖定確切版本、工作流程不自己列套件名稱。

## 驗收

- `python -m unittest discover -s tests -p "test_*.py"`：1373 項全數通過
  （改動前 1363 項）。
- PyInstaller 收集行為的實測見「方案評估」第三項。
- **GitHub Actions（第一次，run 33729860684）**：`test-msix-engine` job 通過
  ——相依改由 `requirements.txt` 安裝之後，打包、靜默安裝、套件被系統接收、
  移除清乾淨全數正常。同一次 run 的 `test-packaging-options` job 失敗，成因
  與本輪無關，見下一節第一項。
- **GitHub Actions（第二次，run 33733654851）**：下一節兩項處置完成後重跑，
  三個 job 全數通過（1m46s／1m27s／1m51s）：

  | Job | 結果 |
  |---|---|
  | `test-packaging-options`（免權限） | 安裝目錄、HKCU 解除安裝登錄、排程工作、使用者層級 PATH、前置與後置腳本皆 PASS，解除安裝後四項皆清乾淨；檔案關聯為 WARN（既有的已知限制：`file_assoc.py` 一律寫 HKLM，而該安裝檔不要求提權） |
  | `test-admin-only-options`（提權，本輪新增） | Program Files 安裝目錄、HKLM 解除安裝登錄、Windows 服務皆 PASS，解除安裝後三項皆清乾淨；系統還原點為 WARN（`This functionality is not supported on this operating system.`——Server 系作業系統沒有這項功能） |
  | `test-msix-engine` | 打包、靜默安裝、套件被系統接收、移除清乾淨全數通過 |

  新增的 job 於 1m51s 內完成且服務確實建立，因此「GitHub 的 Windows runner
  以系統管理員身分執行、不需要人回應提權要求」這個前提在 runner 上亦成立。
  連同虛擬機測得的「未提升時是立刻回絕而非等待」，原註解所述的「卡死」症狀
  至此有兩份互相獨立的反證。
- **Windows 10 1809 實機**（`python -m tools.verify_msix_1809`）：在已安裝
  `winrt-*` 的機器上重新打包一顆 MSIX 引擎的 `Setup.exe`，送進 17763.316
  的虛擬機：

  ```
  [PASS] A：MinVersion 可在 17763 部署
      套件已部署，版本與發行者皆與送入的套件一致。
  [PASS] B：2004 之前的企業版預設關閉側載
      側載登錄值未設定（系統預設）且部署被拒，敘述成立。
  ```

  A 正是本次缺陷原本失敗的那一項（該腳本的說明已記載「實際發生過：A 因為
  安裝檔本身缺少 winrt 綁定而失敗」）。

## 驗收過程另外發現、一併處理的兩項

### 一、CI 的傳統引擎測試設定自 2026-08-29 起即為無效組合

`test-packaging-options` job 的設定同時開啟 `no_admin_install` 與
`windows_service`／`create_restore_point_before_install`，而這兩組矛盾組合
的攔截於 2026-08-29（`5bf28f1`）加入打包階段的驗證。該 workflow 只以
`workflow_dispatch` 手動觸發，上一次成功執行是 2026-08-11，因此這個失效
狀態一直沒有被觀察到。與本輪的改動無關（本輪對該檔案的改動只有三行
`pip install`）。

處置：`no_admin_install` 必須維持為真（CI runner 是無人值守環境，沒有人可以
回應提權要求），因此把兩個需要系統管理員權限的欄位移出該 job，另立
`test-admin-only-options` job 以 `no_admin_install=false` 承接它們，維持端到端
覆蓋。不在同一個 job 裡裝兩次：runner 用完即丟，兩次安裝共用同一台機器時，
第一次留下的狀態會讓第二次的「已清乾淨」驗證失去意義。

#### 連帶更正兩處與實測不符的敘述

該 job 原有的註解寫的是「如果要求提權，執行時會等 UAC 確認，headless 環境下
會直接卡死整個 job」。以 Windows 11 虛擬機（25H2、繁體中文）實測，這個症狀
描述不成立：

| 情境 | 權杖 | 結果 |
|---|---|---|
| 使用者桌面（工作階段 1） | `IsInRole(Administrator)=False`，UAC 全開（`EnableLUA=1`、`ConsentPromptBehaviorAdmin=5`、`PromptOnSecureDesktop=1`） | 啟動**立刻**失敗於「操作被使用者取消」（ERROR_CANCELLED）。未出現同意提示、未出現任何視窗、未安裝任何東西。`Start-Process` 與直接呼叫兩種方式皆同 |
| vmrun 預設（工作階段 0） | `IsInRole(Administrator)=True` | 靜默安裝結束碼 0、耗時七秒；Program Files 安裝目錄、`app.exe`、`uninstall.exe`、HKLM 解除安裝登錄、`MswiElevatedTestSvc` 服務（StartType=Manual）皆成立，HKCU 無殘留。靜默解除安裝後三者皆清乾淨 |

成因是沒有可以顯示同意提示的輸入桌面時，Windows 直接回絕提權要求，而不是
等待。因此無人值守環境的風險是「必定失敗」，不是「卡死」。

第二列的權杖狀態與 GitHub 的 runner 相同，因此它同時是新增的
`test-admin-only-options` job 的預跑：那幾條只有拿到系統管理員權限才走得到的
分支確實成立。建立系統還原點在這台虛擬機上失敗（安裝紀錄寫入「建立系統還原點
失敗（不影響安裝結果）」，`Get-ComputerRestorePoint` 回報 0 筆），與該 job 把
這一項列為警告而非失敗的處置一致。

#### UAC 提示本身可以驗到哪裡

上表第一列的「立刻回絕」是提示畫在安全桌面、而該行程切換不過去所致，因此
不等於真人雙擊的行為。順著這一點續測，把「使用者面對 UAC 提示」拆成四段，
確認其中三段可在本機驗證：

| 情境 | 做法 | 結果 |
|---|---|---|
| 無人可回應提權要求 | 自使用者桌面（未提升）啟動 | 立刻失敗於「操作被使用者取消」（ERROR_CANCELLED） |
| 提示會不會出現 | 設 `PromptOnSecureDesktop=0`，把提示改畫在一般桌面 | 出現。`consent.exe` 於工作階段 1 執行，截圖可見「您是否要允許這個來自未知發行者的 App 變更您的裝置？」 |
| 同意被授予之後 | 設 `ConsentPromptBehaviorAdmin=0`（不提示直接提權），自未提升的桌面啟動 | 安裝結束碼 0、耗時七秒；Program Files 目錄、HKLM 解除安裝登錄、Windows 服務三項皆成立 |
| **按下按鈕這個動作本身** | UI Automation 尋找並叫用「是」 | **不可行**。未提升的行程找得到該視窗（名稱「使用者帳戶控制」、類別 `Credential Dialog Xaml Host`），卻讀不到其中任何按鈕（`ButtonCount=0`） |

最後一項是 UAC 的設計本身——不讓完整性等級較低的行程碰同意提示，正是它存在
的理由——不是驗證環境的缺口，因此不繞過它。需要驗證該動作時請真人操作，方式
記在 `.claude/skills/run-test-vm` 的「三種執行模式」第三種（該 skill 亦於本輪
補上此模式，並更正原本把工作階段 0 的權限觀測值當成整台機器性質的敘述）。

第三列採「等價設定」而非真人操作：驗的是「同意之後」，不是「同意本身」，而
同意由誰給不影響給了之後發生什麼。所有 UAC 設定改動都在每輪還原的快照上進行，
不會留存。

規格文件 §8.33 原記載「真實 UAC 互動沒辦法在開發環境模擬」，已依此更正為只有
最後一項不可模擬。

### 二、`build_msix()` 未清理組裝目錄

`msix_staging/` 在打包結束後留在工作目錄裡。以原始碼執行時工作目錄就是
版本庫本身，該目錄因此每次打包都出現在版本庫根目錄，成為一個未追蹤的
資料夾——內容是應用程式檔案與產生出來的清單，`.msix` 做好之後不再有用途。

處置：`build_msix()` 以 try/finally 清理該目錄。放在 finally 而非「成功才
清」：打包或簽章中途失敗留下的殘留與成功時一樣沒有用途，而失敗那條路正是
最容易被忘記的一條。只刪這個目錄、不掃整個工作目錄，因為一體式流程的
`.msix` 就放在工作目錄底下。

## 已知限制

## 已知限制

- 探針問的是 `shutil.which("python")` 找到的直譯器，而 `builder.py` 呼叫的是
  PATH 上的 `pyinstaller`。兩者屬於同一個 Python 環境是慣例而非保證。這個
  前提與既有的 `pywebview` 偵測完全相同，不在這一輪另外處理。
- 檢查回答的是「這五個套件能不能被匯入」，不是「PyInstaller 這次真的把它們
  收進去了」。後者要驗證只能在打包完成後拆解產物，成本與這個缺陷的發生率
  不相稱。
- 版本鎖定寫在 `requirements.txt`，工具不驗證打包機器上實際安裝的版本是否
  符合。以 `pip install -r requirements.txt` 之外的方式裝了其他版本時，工具
  不會察覺。
- **攔截本身尚未在真正缺少套件的機器上驗過。** 驗收所用的機器已安裝
  `winrt-*`，因此實機驗到的是「有裝時產出的安裝檔可用」；「沒裝時工具會
  擋下來」目前只有單元測試（以假的環境回報）覆蓋。要實機驗證需要一個未安裝
  那五個套件的環境。

## 待辦

1. 在未安裝 `winrt-*` 的環境實際跑一次打包，確認攔截訊息如預期出現（見
   「已知限制」最後一項）。
