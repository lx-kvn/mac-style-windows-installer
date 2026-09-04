# run-test-vm 參考資料

`SKILL.md` 是平常用的；這份收錄手寫 vmrun 指令的方式、實測出來的陷阱，以及
兩台機器各自的環境事實。走 `tools/vms.py` 時下面的陷阱都已經被處理掉，不必
先讀完這份才動手。

## 手動下指令

```powershell
$vmrun = "C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
$vmx   = "D:\VMware\Win10-1809-LTSC\Windows10-1809-LTSC.vmx"
$pw    = (Get-ItemProperty HKCU:\Environment -Name WIN1809_VM_PASSWORD).WIN1809_VM_PASSWORD
$g     = @('-T','ws','-gu','Tester','-gp',$pw)

& $vmrun -T ws revertToSnapshot $vmx Clean
& $vmrun -T ws start $vmx nogui                     # 或 gui
& $vmrun @g CopyFileFromHostToGuest $vmx "<主機>" "C:\Windows\Temp\job.ps1"
& $vmrun @g runProgramInGuest $vmx $ps "-NoProfile" "-File" "C:\Windows\Temp\job.ps1"
& $vmrun @g CopyFileFromGuestToHost $vmx "C:\Windows\Temp\out.txt" "<主機>"
& $vmrun @g captureScreen $vmx "out.png"
& $vmrun -T ws stop $vmx hard
```

從登錄讀密碼而不是讀 `$env:`，因為環境變數新設或變更後，既有的行程拿不到
新值。

`win11` 的每一個指令都要多帶 `-vp $vp`（加密密碼取自
`WIN11_VM_ENCRYPTION_PASSWORD`），**連列快照都不例外**：Windows 11 要求
TPM 2.0，VMware 以虛擬 TPM 滿足它，而帶虛擬 TPM 的機器必須加密存放。沒帶時
回以 `A password is required for this operation`。

## 陷阱

九項都是實測撞到的，共同點是**症狀不指向成因**。

1. **送進去的 `.ps1` 必須是 UTF-8 with BOM。** 客體是 PowerShell 5.1，讀無
   BOM 的檔案時以系統 ANSI 解讀（`win11` 的字碼頁是 950），中文被拆成無效
   token，回報的是語法錯誤而不是編碼錯誤。

   ```powershell
   [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($true)))
   ```

2. **一定要先 `revertToSnapshot`。** 其一，殘留狀態上測到的是上一輪留下的
   東西。其二，略過還原直接 `start` 是從硬碟冷開機、**停在鎖定畫面**（實測
   截圖確認），此時沒有互動登入，`-interactive` 會被拒絕。

3. **客體程式預設跑在工作階段 0，畫面上看不到。** 視窗程式不會出現在桌面
   上，截圖也拍不到。要在桌面上跑，把 `-interactive` 放在**程式路徑之前**
   （實測：不加為 SessionId 0，加了為 1）。視窗程式要另外加 `-noWait`，
   否則 vmrun 會一直等到它結束。

   **要跑的程式不能放在 `C:\Windows\Temp`。** 檔案是以工作階段 0 的提升
   權限送進去的，桌面工作階段的使用者讀不到，`-interactive` 啟動時會回報
   `A file was not found`——訊息指向「檔案不存在」，但檔案其實在（同一次
   測試中客體端 `Test-Path` 為真、大小正確）。改放 `C:\Users\<帳號>` 即可
   （實測：同一顆 exe 從 `C:\Windows\Temp` 啟動結束碼 -1，從
   `C:\Users\Tester` 啟動結束碼 0）。

4. **`checkToolsState` 不能拿來判斷就緒。** 同一台回過 `running` 與
   `installed`，而回 `installed` 時客體其實已在正常桌面、指令跑得動。以
   `running` 為唯一條件會空等到逾時（實際發生過，白等兩分鐘）。改成直接試
   一個最便宜的客體指令。

5. **客體端用 `powershell.exe`，不要用 `cmd.exe`。** vmrun 為每個參數各自
   加引號；powershell 接受被引號包住的參數，cmd.exe 不接受被引號包住的 `/c`：

   ```powershell
   ... cmd.exe "/c" "ver"      # 結束碼 1，看起來像指令錯了
   ... cmd.exe "/c ver"        # 結束碼 0
   ```

   非用 cmd.exe 不可時（例如要 `> log.txt 2>&1` 導出錯誤訊息），把整串併成
   單一參數。

6. **`runProgramInGuest` 不轉達客體的結束碼。** 客體回任何非零值，vmrun 一律
   回報 1（實測客體 `exit 3`，主機端拿到 1）。只能判斷成敗——要區分原因就讓
   客體把結果寫進檔案再取回。

7. **無畫面執行會收掉 Workstation 的分頁**（收的當下還會把視窗拉到最前面）。
   書庫不受影響，`inventory.vmls` 全程未被改動，從書庫點兩下即可重開。
   `vms.preserved_tab()` 會把原本開著的分頁補回去，且只補原本就開著的——
   `vmware.exe -t <vmx>` 本身會搶焦點（實測前景由 brave 變 vmware）。

8. **`preferences.ini` 落後現況約十五到二十秒**，判斷剛發生的分頁變化時要把
   這一點算進去。

9. **截圖解析度不固定。** 有畫面模式 2558×1190、無畫面 2558×1186，同一台在
   兩種模式下就差四個像素。比對兩張截圖時不要假設尺寸相同。

10. **客體會在長時間操作途中睡著。** 自動化全程沒有使用者輸入，Windows 因此
    認定客體閒置並進入睡眠。實測：送入一個 2.23 GB 的檔案時客體在傳輸途中
    發出 ACPI S1 睡眠要求，VMware 隨即暫停虛擬機，主機端拿到的錯誤是
    `The virtual machine needs to be powered on`——訊息指向電源狀態，看不出
    成因是客體自己睡著了。`vms.fresh_boot()` 已在還原後呼叫 `keep_awake()`
    關掉四項閒置逾時，設定隨快照丟棄，不必為此重拍快照。

11. **`.vmx` 有自己宣告的編碼**（實測本機兩台都是 `Big5`）。以 UTF-8 讀寫會
    在檔案含非 ASCII 字元時把設定檔寫壞，症狀是虛擬機開不起來。用
    `vms.read_vmx()`／`vms.write_vmx()`，它們照宣告的編碼處理。

12. **`vm.stop()` 是切電源，客體剛做的改動會來不及寫回磁碟。** 它走的是
    `vmrun stop hard`，等同拔插頭。實測：在客體寫入三個登錄表的值，隨即呼叫
    `stop()`、再冷開機，三個值在下一次開機時**完全不存在**（不是空值）——當下
    立即讀回是正確的，因此不是寫入失敗，而是登錄表尚在記憶體中即斷電。改在
    客體端執行 `shutdown.exe /s /t 0`、輪詢 `vmrun list` 等到電源真的關閉，
    同樣的寫入即可存活。**凡是「改設定 → 重開機 → 期待設定生效」的流程都適用**
    （自動登入、服務啟動模式、群組原則）。

13. **`wait_until_ready()` 成立時桌面還沒起來，此時啟動互動式程式會無聲失敗。**
    那個函式驗的是「客體接受得了指令」，工作階段 1 的 explorer 通常還要再約
    三十秒。實測：`vmrun` 回報就緒後隨即以 `interactive=True` 啟動安裝檔，
    `vmrun` 結束碼為 0，但數秒後客體上查無該行程，畫面也沒有任何視窗——沒有
    任何一端報錯。需要互動桌面時，改為輪詢工作階段 1 的 explorer 是否存在，
    出現之後再啟動。

另有一項不是陷阱但常誤判：**`ProductName` 在 Windows 11 上仍寫著
“Windows 10 Pro”**。判斷版本要看 `DisplayVersion` 與 `CurrentBuild`。

## 送大檔案進客體：走光碟，不要走 Tools

`CopyFileFromHostToGuest` 走的是 VMware Tools 的控制通道，那條管線是設計來
傳設定值這類小東西的。實測 GB 級別只有 **1.8 MB/s**——2.23 GB 要跑二十分鐘。

改把檔案做成 ISO 掛給客體讀：

```python
vms.fresh_boot(vm, iso=r"...\payload.iso")   # 掛載 + 冷開機都在裡面
```

實測數字：做一片 4 GB 的 ISO **3.8 秒**（Windows 內建的 IMAPI2，PowerShell
就能叫，不必安裝工具；記得把 `FreeMediaBlocks` 放大，否則會以實體光碟的容量
為上限），冷開機 **17.8 秒**。安裝檔可以直接從光碟執行，複製那一步整個省掉。

**必須冷開機。** `startConnected` 只在冷開機時套用；從記憶體快照恢復時裝置
狀態是從記憶體映像還原的，新掛的媒體不會出現（實測「媒體已載入 = False」，
連在客體內重新開機也無效）。因此 `fresh_boot(iso=...)` 的順序是「還原 →
恢復 → 關機 → 掛載 → 冷開機」；掛載排在關機之後，是因為虛擬機關機時
VMware 會重寫 `.vmx`。

代價：冷開機之後客體停在鎖定畫面，**沒有互動工作階段**，`interactive=True`
會被拒絕。靜默安裝（`/S`，跑在工作階段 0）不受影響。

## 已確認的環境事實

2026-09-03 於各自的 `Clean` 快照上實測。

```
                  win1809                  win11
ProductName       Win10 Ent LTSC 2019      Windows 10 Pro（Win11 的已知寫法）
EditionID         EnterpriseS              Professional
版本              1809 / 17763.316         25H2 / 26200.8037
語系              en-US                    zh-TW（介面與系統皆是，字碼頁 950）
WebView2          未安裝                   已安裝 145.0.3800.97
App Installer     無                       有
已裝套件數        33                       105
還原+開機         約 6 秒                  約 13 秒
```

兩台皆同：客體帳號 `Tester`、`IsInRole(Administrator)` 為 True、寫得進
HKLM 側載機碼與 `LocalMachine\Root` 憑證存放區、`Add-AppxPackage` 可用、
側載相關登錄值皆未設值（跑的是系統預設，這是驗證側載預設值的正確起點）。

`win1809` 不連網路（`ethernet0.startConnected = FALSE`），以確保組建號停在
17763.316；檔案傳輸走 VMware Tools 通道，不需要網路。

### 網路：兩台的網卡都接在 host-only，接上也連不到外網

2026-09-04 實測。兩台的 `ethernet0.connectionType` 皆為 `hostonly`——那是一條
只通到主機的網路，**把 `startConnected` 打開仍然出不了外網**（症狀是 DNS 解析
失敗，訊息不會提到網路類型）。真的需要客體連外時要同時改兩個值：

```
ethernet0.connectionType = "nat"
ethernet0.startConnected = "TRUE"
```

實測改成 NAT 之後客體即可連外（`msftconnecttest.com` 回應 200）。**測完要改
回去**，尤其 `win1809`——連上外網後 Windows Update 會改變它的組建號，而那個
組建號正是這台機器存在的理由。

`win11` 上沒有安裝任何 .NET 執行環境（2026-09-04，由 FileLocker 那邊的
session 回報）。需要它時得把離線安裝檔一起送進去。

### 讓冷開機自動登入（改網路設定時會需要）

`startConnected` 只在冷開機時套用，而冷開機會停在鎖定畫面、沒有互動工作階段
可以顯示視窗。兩者互相牽制，解法是先開自動登入：

```powershell
$k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $k -Name AutoAdminLogon  -Value '1'
Set-ItemProperty -Path $k -Name DefaultUserName -Value 'Tester'
Set-ItemProperty -Path $k -Name DefaultPassword -Value '<密碼>'
```

設定隨快照丟棄，不必為此重拍快照。**但一定要配合陷阱 12 使用**——用 `vm.stop()`
關機會讓這三個值還沒寫回磁碟就斷電，下次開機時它們不是空值，是完全不存在。
