---
name: run-test-vm
description: 操作本機的驗證用虛擬機——Windows 10 1809（Enterprise LTSC 2019，17763.316）與 Windows 11 25H2（繁體中文）。還原快照、開機、把檔案送進去、在裡面執行程式、取回結果、截圖。用於驗證 MSIX 的 MinVersion 能否部署、企業版側載預設值、缺少 WebView2 Runtime 時安裝精靈的行為、以及中文環境下的介面——這些 GitHub Actions 都涵蓋不到。使用者要求「開虛擬機」「在 1809 上測」「在中文環境測」或輸入 /run-test-vm 時觸發。
---

# run-test-vm — 本機驗證環境

CI 只有 `windows-latest`、英文、無互動桌面。這兩台補的是它涵蓋不到的部分。

## 機器清單

| 代號 | 版本 | 用途 |
|---|---|---|
| `win1809` | Win10 Enterprise LTSC 2019 · 17763.316 · en-US | MinVersion 部署、側載預設值、缺 WebView2 |
| `win11` | Win11 25H2 · 26200.8037 · **zh-TW** | 中文介面、新版 Windows 的 MSIX 與憑證 |

密碼由環境變數提供，機器清單定義在 `tools/vms.py`（含各自的變數名稱）。

### 起始情境（`profile`）

一張快照代表一種起始情境，**快照與登入帳號成對**——用錯組合時 vmrun 回報的
是認證失敗，不會指向情境選錯。

| 代號 | 機器 | 快照 | 帳號 | 給什麼 |
|---|---|---|---|---|
| `default` | 兩台 | `Clean` | `Tester` | 單一 C 槽，管理員帳號 |
| `two_disks` | `win11` | `Clean_C:/E:` | `Tester` | 多一顆 E:（10 GB），用於跨磁碟安裝 |
| `standard_user` | `win11` | `Clean_User` | `User` | **真正的標準使用者**（不在 Administrators 群組） |
| `standard_user_two_disks` | `win11` | `Clean_User_C:/E:` | `User` | 前兩者兼具 |

```python
vm = vms.connect("win11", profile="standard_user")
```

`User` 與 `Tester` 共用同一個密碼，因此仍只有一個環境變數。

**標準使用者與「未提升的管理員權杖」不同**：`interactive=True` 拿到的是
`Tester` 的未提升權杖，理論上可經 UAC 提升；`standard_user` 的帳號本身就
沒有管理員身分。兩者在「能不能建立服務」上表現相同（皆為存取被拒），但在
「能不能提升」上不同。

## 用法：走 `tools/vms.py`

```python
import sys; sys.path.insert(0, "<repo root>")
from tools import vms

vm = vms.connect("win11")                    # 密碼自環境變數讀，不落地
with vms.preserved_tab(vm.machine.vmx):      # 用完把分頁補回去
    vms.fresh_boot(vm)                       # 還原 → 開機 → 等到真的可用
    vms.write_guest_script(local, script)    # 寫成客體讀得懂的編碼
    vm.copy_in(local, r"C:\Windows\Temp\job.ps1")
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", r"C:\Windows\Temp\job.ps1")
    vm.copy_out(r"C:\Windows\Temp\out.txt", back)
    vm.capture_screen(shot)
    vm.stop()
```

`run_program(..., interactive=True)` 讓程式跑在使用者看得到的桌面上；
`check=False` 讓客體的非零結束碼不算錯誤（驗證「本來就該失敗」時用）。

## 先佔住再動手（多個 session 同時在跑）

這台機器上的虛擬機不只這個 repo 在用——FileLocker repo 也走同一批機器，而使用者
有時會同時開著兩個 agent session。`revertToSnapshot` 是破壞性的：另一邊裝到一半的
安裝程式、正在等的畫面，會在毫無徵兆的情況下被還原掉，事後從症狀也看不出成因
（看起來只像「剛才那步沒生效」）。

協調由獨立的 **vm-lease** 提供（`D:\Github\vm-lease_專案\vm-lease`），不在這個
repo 裡面——同一批機器兩個專案都在用，規則只能有一份。

**每次用之前先看一遍它的使用說明書**，那份文件是進版的，這一節不是：

```bash
cat D:\Github\vm-lease_專案\vm-lease\docs\使用說明書.md
```

租期的意義與失去租約時的處置都調整過，照印象使用容易踩到已經改掉的舊做法。

### 這個 repo 這邊要做的

`connect()` 預設就會取得租約，之後**每一次碰虛擬機的動作都會自動延長**，不需要
自己呼叫。只要先讓自己有名字：

```bash
$env:VM_LOCK_OWNER = "mswi-68"      # 用你的 session 代號
```

沒設會直接失敗並告訴你要設什麼——不接受匿名持有，否則誰都能續租別人的租約。

```python
vm = vms.connect("win11", purpose="裝 MSIX 測側載")   # 佔住，並說明為什麼
...
vms.release("win11")                                  # 用完交回去
```

### 兩種錯誤，處置完全不同

| 遇到 | 意思 | 怎麼辦 |
|---|---|---|
| `VmBusy` | 機器被別人佔著 | **不要自己搶。** 把訊息（含持有者、用途、到期時間）原樣轉給使用者，要不要搶是他的決定 |
| `LeaseLost` | 手上那張已經不是你的了 | **立刻停止操作那台機器。** 另一邊可能正在上面工作 |

把後者當成可重試的暫時性錯誤是最危險的誤讀——那正是這整套機制要避免的情形。

### 租期的意義

預設 5 分鐘，但那不是「你只能用 5 分鐘」，而是「**你 5 分鐘沒碰它才算你走了**」。

**唯一的例外是把畫面交給使用者手動操作**：那段期間沒有任何操作發生，自動延長
不會觸發，要自己借久一點——

```python
vms.connect("win11", purpose="等使用者手動操作", lock_minutes=30)
```

### 查看與強制放掉

```bash
vm-lease who win11        # 現在誰佔著
vm-lease log --lines 30   # 最近發生過什麼
```

**一輪測試如果拿到看起來合理、但其實不對的結果，先看 log**：查那段時間機器有沒有
換過手。這正是當初做這個工具的起因。

使用者想強制放掉時刪掉租約檔即可（`%LocalAppData%\vm-locks\<機器代號>.lock`）。
原持有者下次延長時會收到 `LeaseLost`，不會安靜地把它佔回去。

## 模組沒有代勞的三件事

- **顯示模式預設無畫面**，不必每次問。只有在需要使用者當場伸手操作、或
  使用者主動說想看時才傳 `gui=True`。截圖兩種模式都能用，所以「想留下畫面
  證據」不構成用有畫面模式的理由。
- **客體端請用 `powershell.exe`，不要用 `cmd.exe`**（原因見 REFERENCE.md）。
- **結果要靠檔案帶回來。** vmrun 不轉達客體的輸出，也不轉達客體的結束碼
  ——只知道成敗。要判斷原因就讓客體把結果寫進檔案再 `copy_out()`。

## REFERENCE.md

同目錄的 `REFERENCE.md` 收錄手寫 vmrun 指令的方式、九項實測出來的陷阱，
以及兩台機器各自已確認的環境事實。**平常不需要讀**——上面那套 API 已經把
那些陷阱處理掉了。以下情況才去讀：

- 要繞過模組、直接手寫 vmrun 指令
- 出現不明失敗，要查症狀對應的成因
- 要知道某台機器上有什麼、沒有什麼（版本、語系、WebView2、已裝套件）

## 已知限制

- 消費者版（家用版／專業版）的 Windows 10 1809 安裝媒體已無官方管道可取得，
  其行為無法以此環境驗證。
- 兩台系統皆未啟用。不影響 MSIX 部署，但畫面上有浮水印、個人化設定被鎖。

## 待辦事項

- 實際的驗證情境腳本尚未撰寫。此 skill 目前只涵蓋虛擬機本身的操作方式。
