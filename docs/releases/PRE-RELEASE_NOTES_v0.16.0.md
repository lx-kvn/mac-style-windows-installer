# v0.16.0 — Pre-release Notes

Baseline: `v0.15.0` (2026-08-30) → `v0.16.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

> **Code signing**: This project has applied to the [SignPath Foundation](https://signpath.io/solutions/open-source-community) open-source code signing program to provide trusted, signed installers. Signing is not yet active on this release — this note will be updated once the integration is live.
> **簽章聲明**：本專案已申請加入 [SignPath Foundation](https://signpath.io/solutions/open-source-community) 開源簽章方案，用以提供受信任的已簽章安裝檔。這個版本尚未套用簽章，整合完成後會更新這則說明。

---

## English

This release adds a second output format. Alongside the traditional self-extracting installer, the tool can now produce an **MSIX package** — installed and removed by Windows itself, with a guaranteed clean uninstall. It also adds a certificate-store signing mode that keeps the signing password off the command line, WebView2 Runtime detection at every entry point, and a security review of the MSIX code that fixed nine findings.

### New Features

**MSIX output engine**

`installer_config.json` gained an `engine` field. With `msix`, the tool assembles a package manifest, builds a `.msix` with the Windows SDK's `makeappx`, signs it with `signtool`, and wraps it in an installer that hands the package to the operating system for deployment. The application then appears in Settings → Apps like any store-installed application, and removing it removes everything it wrote — that guarantee is the reason for the whole feature.

Supporting pieces: package identity and quad version numbers (`msix_settings`), file type associations with per-extension icons, multi-language display names, PNG icon size validation with per-icon overrides, and Windows SDK tool discovery with an explicit fetch subcommand (`fetch-sdk-tools`) rather than silent downloads.

Ten decisions behind the design are recorded as ADRs 0005–0013 and 0015 — among them: the installer never writes certificates into trust stores, MSIX mode ships no `uninstall.exe`, package identity is never derived from the app name, and per-machine scope requires an explicit field rather than being inferred.

**Signing from the Windows certificate store**

`signing` gained `cert_thumbprint`. Filling it selects store mode: the certificate and its private key stay in the certificate store, `signtool` is invoked with `/sha1 <thumbprint>`, and no password appears on the command line at all. This closes a real exposure — any process on the machine can read another process's command line, and the packaging machine is where the signing certificate lives.

Only thumbprints are accepted, not subject-name fragments: a fragment can match two certificates, and `signtool` then silently picks one. Choosing the wrong one produces an MSIX package Windows refuses to install, with an error that does not point at the cause. See [`docs/adr/0014`](../adr/0014-signing-certificate-is-identified-by-thumbprint-only.md).

A new `list-certs` subcommand lists the certificates in both personal stores that can actually sign code, with subject, expiry and thumbprint. The config wizard offers the two sources as a radio choice, with the store option as a dropdown.

The file mode (`.pfx` plus a password environment variable) is kept, since cloud signing cannot use the store — but the build log now states plainly that the password will appear on the command line.

**MSIX downgrade asks first**

Installing an older version over a newer one fails in MSIX mode (`0x80073D06`); the only way through is to remove the installed package first, and Windows removes an MSIX application's data along with it. The installer now compares versions before deploying and warns, in the same three-way form the traditional engine already uses, that continuing will erase the application's data. Silent installs (`/S`) proceed without asking — a deployment script switching engines should not suddenly start failing — and write what happened to the `/LOG=` file. A same-name package from a different publisher is reported as coexisting, not removed. See [`docs/adr/0015`](../adr/0015-msix-downgrade-asks-the-user-except-in-silent-mode.md).

**WebView2 Runtime detection**

All three entry points now check for the WebView2 Runtime before opening a window. Without it, the previous behaviour was a window that never appeared. The downloaded bootstrapper is verified by Authenticode signature — publisher and trust chain — before being executed.

**Config wizard rework**

An engine selector, the MSIX fields, and incompatible fields that react in place as soon as an engine is chosen. Optional features are grouped into four collapsible sections ordered by dependency, so the form no longer presents every field at once.

### Bug Fixes

**Subprocess output was being discarded**

Thirteen call sites decoded subprocess output using the system's locale encoding. On a Traditional Chinese system (cp950), a byte that encoding cannot decode made the whole output vanish — and in the `capture_output=True` form, silently: the return code stayed correct while stdout became `None`. Packaging would report success with the output gone, or fail with an error pointing at decoding rather than the actual cause.

The other half of that pipeline surfaced during this release's own build: the replacement character produced by the fix could not be *encoded* to a cp950 console, which aborted the build outright. Both directions are now handled. Neither half is reachable on CI, whose runner is English.

**MSIX with install password protection produced an uninstallable installer**

The two features combined produced a package that could not be installed on any machine. Rejected at pack time now.

**File extensions were never validated**

Any string was accepted and carried into ProgIDs, icon names and MSIX association names, where the platform's own constraints then applied. The rules are collected in a `file_extension` module with one definition of what a valid extension is.

**Security fixes**

Archive extraction gained a destination check (an entry path escaping the target directory is refused), and file URIs gained percent-encoding.

**An already-installed MSIX package was never detected**

The install flow only read the traditional uninstall registry; the MSIX case had no branch at all. It now queries the package by identity and explains what it found.

**Packaging machine missing the `winrt-*` bindings**

The tool reported a successful build and produced a package that would not install anywhere. Now caught at pack time. This is the defect that produced the "CI green does not mean the local build works" rule in `CLAUDE.md` — CI installs those packages explicitly every run, so it could never have caught this.

Also fixed: the disk-space message showing `0 MB` for sub-gigabyte amounts; the CLI's terminal output only stripping `<br>` rather than all tags; the MSIX staging directory not being cleaned; and the `init` template's `signing` block, which listed only one certificate source and broke the defaults when left empty.

### Improvements

User-facing messages across `install_engine`, `msix_settings`, `png_size`, `cert_subject` and `packaging_core` moved to translatable keys behind a shared mechanism, and the CLI gained `--lang`. The project icon was replaced. Documentation was reorganised into `docs/adr/`, `docs/proposals/` and `docs/investigations/` by what each kind of document is for.

Development tooling gained a VM driver covering Windows 10 1809 LTSC and a Traditional Chinese Windows 11, with lease-based coordination so two sessions cannot restore each other's snapshots mid-run.

### Documentation

Eleven ADRs (0005–0015). Four investigation records covering the MSIX audit, the subprocess decoding fix, the missing-bindings interception, and the CI/VM capability comparison. The specification document gained §8.27 (rewritten) and §8.38; `CONTEXT.md` gained the MSIX vocabulary; `CLI_USAGE.md` documents the six subcommands. `CLAUDE.md` gained rules on test execution, line endings, and when the local VMs must be used instead of CI.

### Testing

1654 tests, all passing (852 at `v0.15.0`).

### Known limitations

- An MSIX package must be signed by a certificate the target machine trusts. The installer does not write certificates into trust stores, so a self-signed certificate requires the user to install it themselves — this is a deliberate boundary, not an omission ([`docs/adr/0005`](../adr/0005-installer-never-installs-certificates-into-trust-stores.md)).
- File-mode signing still passes the password on the command line. Store mode is the way to avoid it.
- In silent mode, a downgrade erases the application's data and this is recorded only in the log file, after the fact.
- Downgrade detection needs the package version recorded by the packaging step. Installers built before this release keep their previous behaviour.

---

## 繁體中文

發布基準：`v0.15.0`（2026-08-30）→ `v0.16.0`。這個版本多了第二種輸出格式：除了原本的自解壓安裝檔，現在可以產出 **MSIX 套件**——由 Windows 自己安裝與移除，解除安裝保證乾淨。另外補上憑證存放區簽章模式（密碼不再出現在命令列上）、三個進入點的 WebView2 Runtime 偵測，以及一輪針對 MSIX 程式碼的資安稽核，修掉九項。

### 新功能

**MSIX 輸出引擎**

`installer_config.json` 多了 `engine` 欄位。設為 `msix` 時，工具會組出套件清單、用 Windows SDK 的 `makeappx` 打包成 `.msix`、以 `signtool` 簽章，再包成一顆把套件交給作業系統部署的安裝檔。裝完之後應用程式會像商店安裝的程式一樣出現在「設定 → 應用程式」裡，移除時它寫過的東西一併清掉——這個保證正是整個功能存在的理由。

配套的部分：套件身分與四段式版本號（`msix_settings`）、檔案關聯與每個副檔名各自的圖示、多語系顯示名稱、PNG 圖示的尺寸驗證與個別覆蓋，以及 Windows SDK 工具的定位與明確的取得子指令（`fetch-sdk-tools`），不做靜默下載。

設計背後的十項決定記在 ADR 0005–0013 與 0015，其中包括：安裝檔從不寫入信任存放區、MSIX 模式不編 `uninstall.exe`、套件身分不由應用程式名稱推導、全機器範圍必須以明確欄位啟用而非推測。

**改由 Windows 憑證存放區簽章**

`signing` 多了 `cert_thumbprint`。填了它就是存放區模式：憑證與私鑰留在憑證存放區，`signtool` 以 `/sha1 <指紋>` 呼叫，命令列上完全不出現密碼。這關掉一個真實的暴露面——同一台機器上的任何行程都讀得到其他行程的命令列，而打包機器正是簽章憑證所在的地方。

只接受指紋，不接受主體名稱片段：片段可能同時符合兩張憑證，`signtool` 此時不報錯、逕自選一張。選錯的後果是產出一顆 Windows 拒絕安裝的 MSIX 套件，而錯誤訊息不指向這個原因。見 [`docs/adr/0014`](../adr/0014-signing-certificate-is-identified-by-thumbprint-only.md)。

新增 `list-certs` 子指令，把兩個個人存放區裡真正能簽章的憑證連同主體、有效期與指紋列出來。配置精靈把兩種來源做成二選一，存放區那邊是下拉選單。

檔案模式（`.pfx` 加上一個存放密碼的環境變數）保留，因為雲端代簽用不了存放區——但建置紀錄現在會明白寫出密碼將出現在命令列上這件事。

**MSIX 的降版會先問過**

MSIX 模式下把舊版蓋到新版上會失敗（`0x80073D06`），唯一的走法是先移除已安裝的套件，而 Windows 移除 MSIX 應用程式時會連同它的資料一起清掉。安裝端現在會在部署之前先比較版本，用傳統引擎既有的那套三分法警示，並說明繼續下去會清掉該應用程式的資料。靜默安裝（`/S`）不詢問直接執行——同一份部署腳本從傳統引擎換成 MSIX 不該突然開始失敗——並把發生了什麼寫進 `/LOG=` 紀錄檔。同名但發行者不同的套件只告知會並存，不代為移除。見 [`docs/adr/0015`](../adr/0015-msix-downgrade-asks-the-user-except-in-silent-mode.md)。

**WebView2 Runtime 偵測**

三個進入點在開視窗之前都會先檢查 WebView2 Runtime。沒有它的時候，原本的行為是一個永遠不會出現的視窗。下載回來的載入器在執行之前先驗數位簽章——發行者與信任鏈都驗。

**配置精靈改版**

加上引擎選擇器與 MSIX 欄位，選定引擎之後不相容的欄位就地反應。選用功能依依賴順序收進四個可收合的大區，表單不再一次把所有欄位攤開。

### 錯誤修正

**子行程的輸出被丟掉**

十三個呼叫端以系統地區編碼解碼子行程輸出。在繁體中文系統（cp950）上，只要出現一個該編碼解不出來的位元組，整段輸出就消失——而且在 `capture_output=True` 那種形態下是無聲的：回傳碼照樣正確，stdout 卻是 `None`。結果是打包回報成功但輸出不見了，或者失敗時錯誤訊息指向解碼而不是真正的原因。

這條管線的另一半在這一版自己的建置過程中現形：前面那個修正產生的替代字元**寫**不進 cp950 的主控台，直接讓建置中止。兩個方向現在都處理了。兩半在 CI 上都碰不到，因為 runner 是英文環境。

**MSIX 加上安裝密碼保護會編出裝不起來的安裝檔**

這兩個功能加在一起會產出一顆在任何機器上都裝不起來的套件。現在在打包階段就擋下來。

**副檔名的字元從來沒有被驗證**

任何字串都收，然後一路帶進 ProgID、圖示名稱與 MSIX 關聯名稱，等到那邊才撞上平台自己的限制。規則收進 `file_extension` 模組，「什麼算合法的副檔名」只有一個定義處。

**資安修正**

解壓補上落點檢查（項目路徑跑出目標資料夾的一律拒絕），檔案 URI 補上百分比編碼。

**已安裝的 MSIX 套件從來沒有被偵測**

安裝流程只讀傳統模式的解除安裝登錄表，MSIX 那一側根本沒有分支。現在會依套件身分查詢，並把查到的情況說清楚。

**打包機器缺少 `winrt-*` 綁定套件**

工具回報編譯成功，產出一顆在任何機器上都裝不起來的安裝檔。現在在打包階段攔下。`CLAUDE.md` 裡「CI 綠燈不代表本機編出來的東西可用」那條規則就是這個缺陷換來的——CI 每次都明確安裝那幾個套件，因此它結構上不可能抓到。

另外修正：磁碟空間訊息在不足 1 GB 時顯示成 `0 MB`；CLI 的終端機輸出只認得 `<br>`、其餘標籤照原樣印出；MSIX 的組裝目錄沒有清掉；以及 `init` 範本的 `signing` 區塊只列了一種憑證來源，且留空時會弄壞預設值。

### 改善

`install_engine`、`msix_settings`、`png_size`、`cert_subject`、`packaging_core` 的對外訊息全部改成可翻譯的 key，共用同一套機制，CLI 加上 `--lang`。更換專案圖示。文件依用途重新整理成 `docs/adr/`、`docs/proposals/`、`docs/investigations/` 三類。

開發工具方面新增虛擬機驅動，涵蓋 Windows 10 1809 LTSC 與繁體中文的 Windows 11，並以租約機制協調占用，避免兩個同時在跑的 session 互相還原掉對方的快照。

### 文件

十一份 ADR（0005–0015）。四份調查紀錄，分別是 MSIX 稽核、子行程輸出的解碼修正、綁定套件缺失的打包階段攔截，以及 CI 與本機虛擬機的能力對比。規格文件改寫 §8.27、新增 §8.38；`CONTEXT.md` 補上 MSIX 相關詞彙；`CLI_USAGE.md` 記載六個子指令。`CLAUDE.md` 補上測試執行方式、換行符處理，以及哪些事情必須用本機虛擬機而不能靠 CI。

### 測試

1654 個測試，全數通過（`v0.15.0` 當時是 852 個）。

### 已知限制

- MSIX 套件必須由目標機器信任的憑證簽章。安裝檔不寫入信任存放區，因此自簽憑證要由使用者自己安裝——這是明確劃定的界線，不是漏掉的功能（[`docs/adr/0005`](../adr/0005-installer-never-installs-certificates-into-trust-stores.md)）。
- 檔案模式簽章的密碼仍然會出現在命令列上。要避開它就改用存放區模式。
- 靜默模式下的降版會清掉應用程式的資料，而這件事只能事後從紀錄檔得知。
- 降版偵測需要打包階段寫入的套件版本號。這一版之前編出的安裝檔維持原本的行為。

### 待辦

- MSIX 引擎目前只在 Windows 10 1809 LTSC 與 Windows 11 25H2 兩種環境實機驗證過，其餘版本尚未涵蓋。

---

## Full commit list / 完整變更（commit）

```
7817dd6 fix(cli): 主控台編不出的字元不再讓整個編譯中止
83f6c39 docs: 清掉一輪過時資訊
5c2c135 fix(cli): init 範本的 signing 列出兩種憑證來源，留空即等於沒啟用
0fc2513 ci(msix): 補上憑證存放區模式的驗證
7ebf3a1 docs(spec): 規格文件補上憑證存放區模式與 MSIX 的降版處置
41f4179 fix(msix): 靜默降版的告知沒有進到 /LOG= 紀錄檔
f496752 docs: 補上 S1 的完整紀錄，稽核的待辦清單結案
a88d3a2 feat(msix): 降版改成部署前先問過使用者
a9894b0 feat(gui): 配置精靈的簽章憑證改成二選一，存放區那邊做成下拉選單
862ca5f feat(cli): 新增 list-certs，把可簽章的憑證與指紋列出來
8bc6d0a feat(signing): signing 接上憑證存放區模式，密碼不再上命令列
9c1f1a3 feat(signing): 新增憑證存放區的定位模組（cert_store）
a7570be docs(adr): 記下憑證只以指紋指認、以及 MSIX 降版要問過使用者
68900d0 fix(msix): 依實機量測更正降版說明，同版本重裝其實會成功
347bb6e docs: 補上 D1 兩處介面改動的實測結果
0eb39ee refactor(engine): 讓 D1 那一類缺口不會再發生
a4a5089 feat(security): 下載回來的 WebView2 載入器執行前先驗數位簽章
97d2fe5 fix(msix): 已安裝的同名套件從來沒有被偵測，接上查詢並把失敗說清楚
95cd8b7 fix(security): 解壓補上落點檢查，檔案 URI 補上百分比編碼
00a0a38 fix(file-assoc): 副檔名的字元從未被驗證，規則收斂成 file_extension
c60c30d fix(engine): MSIX 加上安裝密碼保護會編出無法安裝的安裝檔
cc5b90b refactor(vms): 機器清單改由 vm-lease 保管
d72c9d9 docs: CLAUDE.md 補上測試執行方式與換行符的規範
cb9f957 chore: .claude 改為只收本專案自己的 skill
b56a20c refactor(vms): 占用協調改用獨立的 vm-lease
1525b0c fix(vms): 占用協調補上三道保護——不可分割的占用、租約編號、事件紀錄
9ff1dda feat(vms): 虛擬機占用協調，避免同時在跑的多個 session 互相還原掉對方的工作
8a0b281 docs(adr): MSIX 全機器使用者範圍改以明確欄位啟用，另立 ADR-0013
c50049d docs(adr): ADR-0012 的自動繼續前提經 1809 實機驗證成立
84d5fc8 feat(installer): 三個進入點在開視窗之前偵測 WebView2 Runtime
9fbd25e feat(installer): 新增 WebView2 Runtime 的偵測與取得模組
bbb0e39 docs: 為九份較長的文件補上目錄，並更正掃描出的過時敘述
712ba33 docs: 規格文件的 Backlog 改用「已完成之待辦」，並移除兩項不再適用的
8fcf429 feat(tools): 大檔案改走虛擬光碟，並阻止客體在長時間操作中睡著
b3fab0b fix(installer_core): 磁碟空間不足的訊息依大小換單位，不再顯示為 0 MB
996fa5a docs: F06／F08／F09 的實機驗收完成，三項修正皆成立
5912c64 feat(tools): 虛擬機的機器清單支援多種起始情境
72bed7a docs: 依實測更正虛擬機的執行權限敘述，並補上 1809 側載預設值的驗證結果
92b5c7f Merge pull request #1 from lx-kvn/claude/busy-antonelli-772c97
29f1a22 docs: CLAUDE.md 記下「CI 驗不到的事情，本機有虛擬機可以驗」
5766da4 docs: 攔截已在缺少 winrt-* 的環境實測，待辦移入已完成之待辦
4378ea8 fix(builder_cli): 終端機輸出去掉所有標籤，不是只認得 <br>
7e70b25 docs: 依實測縮小「UAC 互動無法模擬」的範圍，四段之中三段可驗
e071f43 docs: 補上兩項連帶處置完成後的 CI 結果（三個 job 全數通過）
24d67fa docs: 依實測更正無人值守環境下要求提權的失敗形態
bc7865f docs: 補上 MSIX 綁定套件缺陷的實機驗收結果與兩項連帶處置
5301f5d fix(builder): MSIX 的組裝目錄改成用完即清
cb9fc40 ci: 需要系統管理員權限的兩個欄位另立 job，修正無效的測試設定
2182fcf fix(packaging): 打包機器缺少 winrt-* 綁定套件時擋下 MSIX 引擎的編譯
89b271e docs: 1809 的兩項實機驗證已完成，移入「已完成之待辦」
cfc74b4 feat(tools): 新增 1809 的 MSIX 驗證腳本
c383870 docs(adr): 已完成的待辦移入「已完成之待辦」，並補上 ADR-0010 第三項
b31c895 Merge branch 'claude/peaceful-fermat-7a292b'
db0e6e2 docs: 依實測更正缺少 WebView2 Runtime 時的行為描述
4fc5002 fix: 其餘九個模組的子行程輸出也補上解碼參數
6910b76 fix(builder): 子行程輸出改以 UTF-8 解碼，避免編譯失敗時訊息整段消失
88fe12e docs: 記錄 CI 與本機虛擬機的能力對比與分工原則
b64287d refactor(tools): 虛擬機驅動改為機器清單，納入 Windows 11 中文環境
1979661 fix(tools): 客體就緒改以實際指令判斷，並支援在桌面工作階段執行
962d99c feat(tools): 新增 1809 驗證虛擬機的驅動模組
1552a00 refactor(packaging_core): 訊息改為可翻譯的 key，四個來源至此全部完成
0a10dfc docs: 待辦第 2、3 項改寫——驗證環境改用 VMware，第 3 項改以目的命名
5548ea9 docs: 待辦新增兩項驗證環境——1809 虛擬機與 Windows 沙箱
d6c147f docs: 規劃文件開頭換成現況摘要，原本第一行寫著「尚未實作」
8ec5a4f docs: 設定欄位的命名與形狀已全數定案，補上文件
a82814a docs: 待辦補上 packaging_core 訊息 key 化的實際範圍與陷阱
1f940f1 refactor(msix_settings): 訊息改為可翻譯的 key
44bc6a3 refactor(png_size, cert_subject): 訊息改為可翻譯的 key
82221e5 refactor(messages): 抽出共用的訊息翻譯機制，install_engine 改用它
a218cbc docs: 使用說明書補上 MSIX 引擎與新版面，待辦第 2、3、4 項
bbe644a feat(ui): 說明類彈窗點空白處即關閉
352b140 ci: MSIX 引擎的端到端驗證併入常態 workflow，刪除一次性探針
6ad318e fix: 第四類的說明接上接收端，原本誰都收不到
9abb50b feat(gui): 選定引擎後，表單上的不相容欄位就地反應
17cf315 feat(gui): 表單依依賴順序重排，選用功能收進四個可收合的大區
dfc7bc3 refactor(install_engine): 相容性訊息改為可翻譯的 key，CLI 加上 --lang
7c03b1c feat(gui): 配置精靈加上引擎選擇器與 msix 欄位
2399a72 feat(gui): 配置精靈支援 MSIX 引擎，並修正一組互相污染的測試
345561e chore(branding): 更換專案圖示為深色圓角方框加資料夾與箭頭
4a3a5c1 fix(builder): 工作目錄的資源檢查移到打包與簽章之前
0a1aed6 feat(builder): 補上 MSIX 一體式路徑，憑證在本機時 pack 自己串完三步
4252d85 feat(png_size): 圖示的尺寸驗證與 msix.icons 個別覆蓋
7a1a1b4 feat(ui): MSIX 模式的拖曳目的地換圖示、不可點選、改顯示說明文字
2d16f6e feat(builder): MSIX 模式的安裝檔，兩截式流程完整跑通
0acd33f feat(msix_install): MSIX 模式的安裝流程協調，並在 installer_core 最上層分流
9960dc7 feat(msix_deploy): 請求系統部署/移除 MSIX 套件，接縫自第一天成立
19ed2f9 feat(cli): 新增 pack-msix，兩截式流程的第一步可以實際產出 .msix
4a170fe feat(msix_package): 組裝 MSIX 套件目錄並呼叫 makeappx 打包
316197e feat(msix_manifest): 產生 MSIX 套件清單與多語系顯示名稱的資源來源檔
21cb1f6 docs: 定案清單中的三項宣告形式，清單產生不再有前置條件
a116676 ci: 探針擴充三題，檔案關聯圖示的退回行為與多語系顯示名稱
b8fc0b1 docs: CI 探針結果，五項未驗證的前提全部成立
e33bdea fix(ci): 探針的套件移除改用探測方式呼叫，清理失敗不再中止整條 run
e41732c ci: 新增 MSIX 部署探針 workflow，回答五項未驗證的前提
fc43f5d feat(cert_subject): 從簽章憑證自動讀出發行者，並於打包階段比對
6197108 feat(msix_settings): msix 設定區塊的驗證與版本號四段轉換
3918ba9 feat(install_engine): 引擎選擇與 MSIX 設定相容性檢查
21f78d0 docs: MSIX 引擎第一版只提供當前使用者範圍，並拆出「使用者範圍」這個概念
6862463 docs: 逐項重新檢查設定欄位的分類歸屬，no_admin_install 的預設值歸類有誤
fa281ec docs: 查證第五輪的兩項待查前提，最低 Windows 版本與應用程式項目數量
c26f03f feat(sdk_tools): SDK 工具的定位與取得，既有 signtool 檢索一併統一
78d92b8 docs: 決定 MSIX 套件清單各欄位的來源，並補上分類漏掉的第四類
dd1996a docs: 決定 SDK 工具的取得方式，不自動下載
f801eac docs: MSIX spike 補上 PyInstaller 相容性驗證，第一輪列的主要風險解除
9b23d4e docs: MSIX spike 第一輪結果，本機可驗證的部分全數通過
e9cca63 docs: MSIX 輸出功能完成產品範圍決策，並修正第一輪規劃的誤判
f843d71 docs: 規格文件補上 ui/ 的檔案結構與載入順序（§3.1）
35a6702 docs: 做完的規劃搬進 docs/investigations/，跟還沒定案的分開
```
