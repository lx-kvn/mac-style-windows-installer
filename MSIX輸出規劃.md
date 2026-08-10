# MSIX 輸出規劃（研究/決策記錄）

**狀態：尚未實作，這輪只做研究與方向決策，見 `規格文件.md` §10 未來待辦清單。**

## 動機

現在的安裝檔本體是 PyInstaller 打包的一顆自訂 exe（`Setup_XXX.exe`），
所有落地檔案、寫登錄表、解除安裝清理都是這個工具自己動手做（見
`installer_core.py`/`uninstall.py`）。這個做法彈性最大，但解除安裝乾不
乾淨完全取決於這個工具自己有沒有把每一筆寫入都記進
`install_manifest.json`——理論上只要漏記一筆，就會殘留。

MSIX 是 Windows 目前唯一「保證乾淨解除安裝」的原生機制：整個安裝改由
系統的容器化套件引擎接管，所有寫入都在系統層級被追蹤，解除安裝時系統
自己保證清空，不依賴應用程式自己記清單記得夠不夠完整。這份文件研究
「如果要讓輸出格式支援 MSIX，可以怎麼做、要付出什麼代價」。

## 兩條路線

### 路線 A：純原生 MSIX

使用者對 `.msix`/`.msixbundle` 按兩下（或走安裝網址協定），交給系統內建
的 **App Installer** 處理整個安裝流程。

**代價**：安裝畫面完全由 Windows 自己畫（Publisher/版本/「安裝」按鈕的
制式對話框），開發者沒有任何客製化空間——**不能保留現在這套「把圖示拖進
資料夾」的拖拽介面**，也沒有 EULA 頁面可以放。MSIX 的容器沙盒模型還會
擋掉現在好幾個既有功能：

- `pre_install_script`/`post_install_script`（沙盒內不能任意執行外部腳本）
- `custom_install_dir`（自訂安裝路徑，MSIX 應用只能裝在系統管理的套件目錄）
- `--uac-admin`/相依元件靜默安裝（沙盒內沒有提權這回事，`bundle_dependencies`
  現在會執行外部安裝程式，這在 MSIX 容器內基本上做不到）
- `no_admin_install`/`local_appdata_files` 這類「跨 hive 選安裝位置」的
  彈性設計，在 MSIX 裡沒有意義（套件位置由系統決定）

### 路線 B：混合式 bootstrapper

打包出來的東西表面上還是一顆 exe（跟現在一樣的 bootstrapper 性質），但
內嵌的不是「app 本體檔案 + `installer_core.py` 邏輯」，而是一份
`.msix`/`.msixbundle` 檔案本身——概念上跟現在用 `--add-data` 把
`uninstall.exe`/`app_contents` 塞進 exe 資源裡是同一招，只是這次塞進去
的是整包 MSIX。

exe 執行時的工作從「自己動手複製檔案、寫登錄表」變成：把內嵌的 `.msix`
解壓到暫存路徑 → 呼叫 Windows 的部署 API
（`Windows.Management.Deployment.PackageManager.AddPackageAsync`）讓系統
自己把這包 MSIX 裝進去。真正落地檔案這件事完全交給 OS 的套件引擎，
「保證乾淨解除安裝」這個特性也是從這裡來的。

**代價**：

1. **雙層簽章信任鏈**——現在 `signing` 參數只是幫 exe 簽 Authenticode；
   MSIX 額外要求「這包 MSIX 的簽章憑證」必須被目標電腦信任，不然
   `AddPackageAsync` 直接失敗。如果不是走 Store 上架，等於 bootstrapper
   exe 還要先想辦法把憑證裝進使用者電腦的「受信任的人」憑證存放區
   （這一步本身就要系統管理員權限）。
2. **WinRT API 是全新的技術堆疊**——現在整個專案是 ctypes/pywin32 這條
   路線（`Rstrtmgr.dll`、`winreg`、Win32 API 直接呼叫），
   `PackageManager.AddPackageAsync` 屬於 WinRT，Python 這邊要另外引入
   `winsdk`/`pywinrt` 這類綁定套件才呼叫得到，跟現在整個程式碼庫慣用的
   手法是兩套不同的東西，不是加個參數就能接上。
3. 容器沙盒的功能限制（見路線 A 那份清單）**依然存在**——差別只在於
   「使用者看到的安裝畫面」還是自訂的，但 app 本體實際落地之後，一樣要
   活在 MSIX 的沙盒規則底下，路線 A 列出的那些功能限制對路線 B 同樣成立。

## 簽章要求（跟一般 exe 的 Authenticode 簽章不一樣）

MSIX 套件**必須**數位簽章，而且簽章憑證的 Subject 必須跟
`AppxManifest.xml` 裡宣告的 Publisher 完全一致，不然系統的部署引擎
（`AddPackageAsync`／App Installer）會直接拒絕安裝——這跟現有 `signing`
參數（選填、只影響 SmartScreen 警告，不簽也能執行）是完全不同等級的
硬性要求。

**憑證等級不需要到 EV**：EV（Extended Validation）憑證的價值只在
「讓一般 exe 立刻拿到 SmartScreen 信譽」，MSIX 只在乎憑證鏈不鏈得到
Windows 信任的根，一般等級的 OV（Organization Validation）憑證就夠用，
不需要為了 MSIX 特地去買貴很多的 EV 憑證。

**憑證要「被信任」的三條路**：

1. **走 Microsoft Store 上架**——Store 審核通過後用 Store 自己的憑證重新
   簽過，使用者端完全不用額外處理，是最省事的一條路。
2. **用公開 CA 簽發的正式 OV 程式碼簽章憑證**——只要鏈到系統內建的信任
   根，裝機端一樣不用額外處理。
3. **自簽/企業內部 CA**——使用者電腦必須先把憑證手動裝進「受信任的人」
   憑證存放區（或群組原則批次佈署），單機側載給一般使用者不現實，只
   適合企業內網統一佈署。

**具體服務選項——SignPath**：查證屬實，[SignPath](https://signpath.io/solutions/open-source-community)
支援 MSIX/AppX 的「深度簽章」（deep signing），跟前面 §10 待辦清單 #12
提的 Azure Trusted Signing 是同一類雲端代簽服務，可以透過 GitHub Actions
等 CI/CD 整合自動簽。更重要的是：**SignPath Foundation 對符合資格的
開源專案提供免費簽章**（[資格條件](https://signpath.org/terms.html)：
公開原始碼 + 採用受認可的開源授權），這個專案是 MIT 授權、公開在
GitHub 上，很可能直接符合資格。

**但有一個取捨要先想清楚**：SignPath Foundation 免費方案發出的憑證，
Subject（也就是簽章顯示的發行者）固定是 `SignPath Foundation`，不是
這個專案自己的名字或 `lx.k`——套用到 MSIX 上，代表 `AppxManifest.xml`
的 Publisher 欄位就得填 `SignPath Foundation`，使用者安裝時看到的
發行者也是這個名字，不是專案自己的品牌。如果介意這一點，就要考慮自費
買一張 OV 憑證（不用 EV，成本比想像中低很多），換取發行者名稱可以是
自己的名字。這個取捨要留到真的要動工時再定案，不是這輪要決定的事。

## 推薦方向

**如果之後真的要做，走路線 B（混合式 bootstrapper）**，不要做路線 A。

理由：這個專案的核心賣點就是「mac 風格拖拽安裝」的 UX 本身（連專案名稱
都叫 `mac-style-windows-installer`），路線 A 會直接把整個安裝畫面的主控
權交給系統，等於放棄這個專案的核心識別，拿到的「保證乾淨」這個好處，
用「這個工具存在的理由都不見了」去換，不划算。

路線 A（純原生 MSIX）可以留著，但只定位成**額外的、平行的輸出選項**
（例如給想上架 Microsoft Store、或企業內側載部署的使用者），不是取代
現有的 `Setup_XXX.exe` 拖拽流程。

## 如果真的要動工，需要先解決的問題

這些是「之後要開工前」必須先有答案的，這次不解，只列出來避免遺漏：

1. **功能取捨怎麼談**：`pre_install_script`/`post_install_script`/
   `custom_install_dir`/相依元件靜默安裝 這幾個現有功能，在 MSIX 模式下
   要嘛整個不支援、要嘛需要另外設計沙盒相容的替代方案（例如相依元件
   改成引導使用者去 Microsoft Store 另外裝，而不是這個安裝程式自己裝）。
   這個範圍需要先跟需求方對過，不是工程問題，是產品範圍問題。
2. **簽章/信任鏈的最終方案**：是否要接上 Microsoft Store 上架（那麼
   簽章信任問題由 Store 解決），還是隻做企業側載（需要自己處理受信任的
   人憑證存放區佈署，通常要搭配群組原則批次部署，不適合個人使用者
   單機側載）。這個決定會大幅影響工程複雜度，必須先定案。細節見上方
   「簽章要求」一節——如果不走 Store，SignPath Foundation 的開源免費
   方案是個候選，但要先決定能不能接受發行者名稱顯示成
   `SignPath Foundation` 而不是自己的品牌，不能接受的話就要編列預算買
   自己的 OV 憑證。
3. **WinRT 綁定套件的可行性驗證**：`winsdk`/`pywinrt` 這類套件能不能在
   PyInstaller 打包後的環境正常運作（WinRT 元件通常靠 COM 啟動，
   PyInstaller 的 `--onefile` 模式對這類原生互操作套件偶爾會有相容性
   問題），這個要先花一輪 spike 驗證，不能假設能直接用。
4. **MSIX 套件本身怎麼產生**：這個工具現在的輸入是「使用者選一個資料夾
   當 app 內容」，MSIX 需要一份 `AppxManifest.xml` + 用 `makeappx.exe`
   打包——這一步的產生邏輯要另外設計，不是把現有的檔案複製邏輯改個
   輸出格式就好。

## 已知限制（如實記錄）

- 這份文件只涵蓋研究跟方向決策，沒有任何程式碼變更。
- 上面列的取捨/代價都是基於目前公開文件跟這次討論的推論，實際動工前
  仍需要做 spike 驗證（尤其是 WinRT 綁定套件在 PyInstaller 打包環境下
  的相容性），不排除到時候發現代價比這裡估計的更高。
