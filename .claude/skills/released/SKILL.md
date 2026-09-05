---
name: released
description: 發布這個專案（mac-style-windows-installer）的新版本——決定版本號、跑測試、編譯打包工具 GUI/CLI 兩顆 exe、把它們打包成一份安裝檔、產生 Pre-release Notes 草稿、commit、打 tag、push、建立 GitHub Release。使用者輸入 /released 時觸發。
---

# /released — 發布新版本

這個 skill 一路做到 **建立 GitHub Release 為止**（commit → 打 tag →
push → GitHub Release，含 main 分支跟 tag）。流程中任何一步失敗就整個
中止，不會跳過失敗的步驟繼續做下一步。push 與建立 GitHub Release 都是
對外可見、難以完全還原的動作，**執行前一定要先明確列出這次要做的事情
（push 的分支/tag/遠端；GitHub Release 的 tag/標題/附件），等使用者
確認後才真的執行**（見步驟 12、13）——這是使用者把 tag/push/GitHub
Release 加進這個 skill 時明確要求的，不能因為已經寫進 skill 就跳過
確認、變成完全無人值守。

## 步驟

### 1. 確認工作目錄乾淨

```
git status --short
```

- 完全乾淨（沒有輸出）：直接進下一步。
- 有任何未追蹤/未提交的變更：**列出完整清單**，明確問使用者要怎麼處理，
  給出至少這幾個選項，不要自己猜測或悄悄略過：
  - 這些變更也一併納入這次發布的 commit
  - 使用者想先自己另外處理（暫停整個流程，讓使用者處理完再重新執行 `/released`）
  - 使用者確認這些是可以忽略的東西（例如編輯器暫存檔），繼續往下做，但
    commit 時仍只加入這次發布相關的檔案，不要順手 `git add -A` 撿走使用者
    沒有明確同意要納入的東西
  取得明確答案之前不要繼續下一步。

### 2. 決定版本號

```
git tag --sort=-creatordate
git log <上一個tag>..HEAD --pretty=%s
```

（沒有任何 tag 的話，改用 `git log --pretty=%s` 掃全部歷史。）

依照 Conventional Commits 規則（跟這個 repo 已經確立的慣例一致，見
`COMMIT_CONVENTION.md`）逐一檢視 commit 訊息的類型前綴，決定建議的版本
號升級幅度：

- 有任何 commit 內文包含 `BREAKING CHANGE`：建議 **major**（`X.0.0`）
- 沒有 BREAKING CHANGE，但有任何 `feat:`：建議 **minor**（`x.Y.0`）
- 只有 `fix:`（沒有 `feat:`/BREAKING CHANGE）：建議 **patch**（`x.y.Z`）
- 只有 `docs:`/`style:`/`refactor:`/`test:`/`chore:`/`perf:`/`build:`：
  建議 **patch**，但明確告知使用者「這批 commit 沒有 feat/fix，維護性
  的變更習慣上也可以選擇不特別發版」

印出推算依據（哪些 commit 觸發了哪一級判斷，附上 commit 訊息第一行），
**明確請使用者確認這個建議版本號，或自己指定一個**——不要不問就直接
定案往下做。目前的版本號可以從 `VERSION` 檔案讀到，供對照。

### 3. 寫入 VERSION 檔案

把確認後的版本號（不含 `v` 前綴，例如 `0.8.0`）寫進 repo 根目錄的
`VERSION` 檔案（純文字一行）。

### 4. 跑完整測試套件

```
python -m unittest discover -s tests -p "test_*.py"
```

任何測試失敗，**整個流程到此中止**，把失敗訊息回報給使用者，不要繼續
往下編譯或 commit。

### 5. 編譯打包工具的 GUI + CLI 兩顆 exe

```
python build_config_tool.py --cli --version <版本號> --icon <輸出 exe 用的 ICO，沒有的話跟使用者確認要用哪個> --publisher <發行者名稱，跟步驟 6 用同一個>
```

**務必帶 `--icon`**——沒帶的話編出來的兩顆 exe 會是 PyInstaller 預設
圖示，不是這個專案自己的圖示（v0.8.0 發布時漏帶過一次，事後才發現，
不要重蹈覆轍）。

**務必帶 `--publisher`**——這個參數會被寫進兩顆 exe 的 Win32 VERSIONINFO
資源（CompanyName/LegalCopyright），讓檔案總管「內容 → 詳細資料」頁籤
不再是空白。沒帶就等於發行者留空，VERSIONINFO 裡的 CompanyName 會是
空字串、LegalCopyright 會是 `Copyright © <年份> `（見 version_info.py）。

產出 `mac-style-windows-installer_GUI_v<版本號>.exe` /
`mac-style-windows-installer_CLI_v<版本號>.exe`，位置在 `dist/` 底下。
任何一顆編譯失敗就中止流程。

### 6. 用打包工具自己的 CLI，把這兩顆 exe 打包成一份安裝檔

**先準備一個 repo 之外的暫存資料夾**（例如系統暫存目錄底下的
`installer_builder_app/`，**不要**放在 repo 的 `dist/` 底下——
`builder_cli.py pack` 呼叫的 `builder.py`/`build_all()` 打包主安裝檔時
也會清空自己工作目錄下的 `dist/`，如果暫存資料夾剛好在裡面，還沒被
打包進去就先被清掉了，v0.8.0 發布時踩過這個坑）。

把上一步編出來的兩顆 exe 複製進暫存資料夾，**同時改名成不帶版本號的
固定名稱**（GUI 版固定叫 `mswi-gui.exe`、CLI 版固定叫 `mswi-cli.exe`——
這是這兩支工具在「安裝到使用者電腦上之後」的固定檔名，跟前一步
`dist/` 底下含版本號的建置產物檔名是兩回事，**不要搞混、不要直接複製
原始檔名進去**）：

```
cp dist/mac-style-windows-installer_GUI_v<版本號>.exe <暫存資料夾>/mswi-gui.exe
cp dist/mac-style-windows-installer_CLI_v<版本號>.exe <暫存資料夾>/mswi-cli.exe
```

接著呼叫：

```
python builder_cli.py pack \
  --app-dir <暫存資料夾路徑> \
  --png-icon <拖拽介面用 PNG，沒有的話跟使用者確認要用哪個> \
  --ico-icon <安裝檔封面用 ICO，沒有的話跟使用者確認要用哪個，跟步驟 5 用同一張> \
  --app-name "mac-style-windows-installer" \
  --version <版本號> \
  --publisher <發行者名稱，跟使用者確認> \
  --exe-name "Setup_mac-style-windows-installer_v<版本號>" \
  --main-exe "mswi-gui.exe" \
  --add-to-path \
  --path-target-exe "mswi-cli.exe" \
  --no-admin-install
```

**真實踩過的坑（v0.11.0 發布後使用者實測發現）**：一開始這裡用的是
`--local-appdata-files "mswi-cli.exe"`（只把 CLI 版改裝到
`%LOCALAPPDATA%`，GUI 版留在預設的 Program Files），理由是「GUI 只是
雙擊執行、放哪裡差別不大」——**這個判斷是錯的**。`InstallerBuilder.exe`
執行「開始編譯安裝檔」時，要在**自己所在的資料夾**解壓內嵌資源、寫入
`dist/`/`build/` 這些 PyInstaller 編譯產物（見 docs/使用說明書.md 第 4 節），
裝在 Program Files 底下沒有寫入權限，會直接編譯失敗——使用者實測回報
「連 GUI 也不能編譯打包安裝檔」就是踩到這個。

改成 `--no-admin-install`：讓**整個安裝檔（GUI 跟 CLI 都算在內）**改裝
到 `%LOCALAPPDATA%\Programs\mac-style-windows-installer`，兩者在同一個
使用者自己完全有寫入權限的資料夾底下，不會再有這個問題，也完全不需要
提權/UAC。`--path-target-exe "mswi-cli.exe"` 仍然保留（讓 PATH 只加
`mswi-cli.exe` 所在目錄的語意還在，即使現在 GUI/CLI 已經在同一個目錄，
這樣寫比較清楚意圖），但**不要再帶 `--local-appdata-files`**——`
no_admin_install` 開啟時安裝根目錄本身就已經是 `%LOCALAPPDATA%`，
`local_appdata_files` 是用來在「主程式留在 Program Files」的情境下，
把少數幾支檔案另外搬到使用者目錄，跟 `no_admin_install` 同時用是多餘的。

`main_exe` 是 GUI 版（雙擊/捷徑用），`path_target_exe` 是 CLI 版
（裝完後可以直接在命令列打指令用，見規格文件 §8.14）。**因為
`path_target_exe` 用的是不帶版本號的固定檔名 `mswi-cli.exe`，使用者在
CMD 裡永遠打同一個指令 `mswi-cli`，不會因為升版就要改用法**——這是
刻意的設計，不要為了圖方便又改回帶版本號的檔名。PNG/ICO 圖示、發行者
名稱如果這個 repo 之前沒有現成的可以沿用，跟使用者確認一次，不要自己
隨便選一個。

**最終產物**：GUI exe、CLI exe、安裝檔三個檔案都保留在輸出資料夾，
沿用 `dist/` 底下含版本號的原始檔名（不要用暫存資料夾裡改名過的
`mswi-gui.exe`/`mswi-cli.exe`，那兩個只是拿來餵給打包流程用的中繼檔，
不是最終產物），不要刪除前兩個原始 exe。

**真實踩過的坑（v0.11.0 發布時發現）**：這一步呼叫的 `builder_cli.py pack`
底層一樣是 `builder.py` 的 `build_all()`，開頭會**清空整個 `dist/` 資料夾**
（見 `build_all()` 開頭「每次重新編譯前，先清掉舊的 dist/build 產物」那段）
才開始編譯安裝檔——這代表上一步（步驟 5）編出來、還留在 `dist/` 底下的
`mac-style-windows-installer_GUI_v<版本號>.exe`/`_CLI_v<版本號>.exe`
**會被這一步的建置流程直接砍掉**，不會自動倖存到這一步跑完之後。
正確做法：這一步跑完後，**立刻**把 `dist/Setup_mac-style-windows-installer_v<版本號>.exe`
連同（從暫存資料夾裡改名複製回來，或直接重新編譯一次）的 GUI/CLI exe
一起複製進 `release_output/`（repo 根目錄，不會被 git 追蹤），三個檔案
到齊才算這一步真正完成，不要以為「檔案還在 dist/ 裡」就跳過這個複製
動作，實測發現這樣做上一步的 GUI/CLI exe 早就已經不在了。

### 7. 備份這次的建置產物到本機版本庫

除了 GitHub Release（步驟 13）以外，這個專案另外在
`D:\Github\mac-style-windows-installer_專案\上傳到Github的版本\` 底下手動
保留每個版本的建置產物副本（`v0.5.4`/`v0.6.0`/`v0.7.0` 這幾個既有資料夾），
不透過 git 管理，純粹是本機的備份/歸檔。這一步把上一步產出的三個檔案
複製一份過去：

```
mkdir "D:\Github\mac-style-windows-installer_專案\上傳到Github的版本\v<版本號>"
copy release_output\Setup_mac-style-windows-installer_v<版本號>.exe "D:\Github\mac-style-windows-installer_專案\上傳到Github的版本\v<版本號>\"
copy release_output\mac-style-windows-installer_GUI_v<版本號>.exe "D:\Github\mac-style-windows-installer_專案\上傳到Github的版本\v<版本號>\"
copy release_output\mac-style-windows-installer_CLI_v<版本號>.exe "D:\Github\mac-style-windows-installer_專案\上傳到Github的版本\v<版本號>\"
```

如果 `v<版本號>` 這個資料夾已經存在（例如重跑這個流程修正上一次的失誤），
直接覆蓋裡面的檔案即可，不需要另外確認——這是純本機備份，不是對外可見
的動作，跟步驟 11/12/13 的 push/GitHub Release 性質不同，不用比照那幾步
的確認把關。

### 8. 在虛擬機與 CI 上驗過真實產物（缺一不可）

到這一步為止，被驗證過的只有「原始碼」——步驟 4 的測試套件全部跑在打包
機器上，那台什麼都裝了、是中文環境、而且執行測試的行程權限與一般使用者
不同。**編出來的那三顆檔案在別人的機器上會發生什麼，還沒有任何人知道。**

兩件事都要做，因為它們回答的是不同的問題：

- **CI**：這份程式碼在一台乾淨、英文、全新架設的機器上跑得起來嗎。
- **虛擬機**：編出來的產物在真實的使用者機器上會發生什麼。

#### 8a. 虛擬機：對產物做煙霧測試

```bash
python -m tools.verify_release_build release_output/Setup_<應用程式>_v<版本號>.exe
```

預設跑 `win11`（繁體中文、字碼頁 950）的 `standard_user` 情境——真正的
標準使用者，不在 Administrators 群組。它會靜默安裝、確認兩顆 exe 與登錄表
項目與 PATH 都到位、**實際執行裝好的 CLI**、再靜默移除並確認清乾淨。

執行前先給自己一個名字（虛擬機的占用協調要求具名，見 `run-test-vm` skill）：

```powershell
$env:VM_LOCK_OWNER = "<你的 session 代號>"
```

版本內容有動到 MSIX 引擎、最低 Windows 版本、或安裝流程本身時，`win1809`
（英文環境、Windows 10 LTSC）也跑一次：

```bash
python -m tools.verify_release_build release_output/Setup_...exe --machine win1809 --profile default
```

三項有任何一項不是 `pass` 就中止整個流程。`inconclusive` 也算——那代表這
一輪根本沒有量到東西，不是「大概沒問題」。

**為什麼一定要有這一關**：v0.16.0 發布時，`build_config_tool.py --cli` 在
編譯途中因為主控台編不出某個字元而中止，沒有產出任何 exe；那個路徑在英文的
CI runner 上永遠不會執行到。而更早之前，打包機器少裝五個綁定套件時，工具
回報編譯成功、產出一顆在任何機器上都裝不起來的安裝檔，錯誤一路走到終端
使用者手上才出現。這兩件事測試套件與 CI 都攔不到（見 `CLAUDE.md`「CI 驗
不到的事情」）。

#### 8b. CI：在乾淨的英文機器上跑一遍

`build.yml` 只由 `v*` tag 或手動觸發，**push 到 main 不會觸發它**。也就是
說，什麼都不做的話，CI 的結果會晚於「tag 已經推上去」這件對外可見的事實
——v0.16.0 就是這樣拿到一次紅燈的。因此在打 tag 之前先手動跑一次：

```bash
gh workflow run build.yml --ref main -f version=<版本號>
gh workflow run test-packaging-options.yml --ref main
gh run list --limit 2
```

兩個都要綠。`test-packaging-options.yml` 驗的是打包選項實際落到系統上的
效果（登錄表、服務、排程工作、PATH、檔案關聯、MSIX 引擎與憑證存放區
簽章），跟 `build.yml` 涵蓋的範圍不同。

CI 紅燈時中止流程，修好、重跑，綠了才往下走。**不要因為「本機是綠的」就
判斷 CI 的紅燈可以忽略**——本機與 runner 的差異（語系、權限、已安裝的
套件）正是這一關存在的理由。實際發生過的三種紅燈：測試模組匯入了只有
本機才裝的相依套件、測試未指定語言卻斷言中文字串、以及斷言「不包含」的
測試在英文機器上無條件通過（後者比失敗更糟，因為它不會被發現）。

### 9. 產生 Release Notes 草稿

在 `docs/releases/` 底下新增 `PRE-RELEASE_NOTES_v<版本號>.md`，格式沿用
這個專案已經確立的雙語（英文 + 繁體中文）慣例，可以參考 `docs/releases/`
裡既有的 `PRE-RELEASE_NOTES_v*.md`
抓格式。內容依 §2 蒐集到的 commit 訊息，按 Conventional Commits 類型
分類整理：

- New Features / 新功能（`feat:`）
- Bug Fixes / 錯誤修正（`fix:`）
- Improvements / 改善（`refactor:`/`perf:`/`style:`）
- Documentation / 文件（`docs:`）
- 其他維護性變更視情況併入合適的分類或獨立列出

標題註明「Pre-release」，結尾附上這次涵蓋的完整 commit 清單（hash + 訊息
第一行），比照既有 Release Notes 的「Full commit list」段落慣例。

### 10. Commit

加入：
- `VERSION`
- `docs/releases/PRE-RELEASE_NOTES_v<版本號>.md`
- 步驟 1 使用者明確同意要一併處理的其他變更（沒有就不加）

**不要**加入 `dist/` 底下的編譯產物（GUI/CLI exe、打包出來的安裝檔）——
這些是發布產物，不是原始碼，而且 `.gitignore` 本來就排除 `dist/`。

commit 訊息依照這個 repo 已經確立的 Conventional Commits 慣例
（見 `COMMIT_CONVENTION.md`）：

```
chore: 發布 v<版本號>

<列出這次版本包含的重點變更，跟 Release Notes 草稿的重點對齊>
```

### 11. 打 tag

```
git tag -a v<版本號> -m "v<版本號>"
```

打在剛剛那個 commit 上（annotated tag，帶訊息，比 lightweight tag 更適合
拿來當發布紀錄）。tag 名稱固定是 `v<版本號>` 前綴（跟 repo 既有的
`v0.7.0`/`v0.6.0`/`v0.5.4` 這些既有 tag 命名慣例一致）。

### 12. Push（執行前務必先明確確認）

先確認遠端資訊：

```
git remote -v
git rev-parse --abbrev-ref HEAD
```

**明確列出這次要 push 的內容**（目前分支名稱、遠端名稱、剛打的 tag
名稱），完整呈現給使用者看過一遍，**取得明確同意後才執行**——push 是
對外可見、別人可能已經 fetch/看到的動作，即使已經寫進這個 skill 裡，
也不能每次自動跳過確認，這是使用者把 tag/push 加進這個 skill 時明確
要求保留的把關步驟。確認後執行：

```
git push <遠端> <目前分支>
git push <遠端> v<版本號>
```

（分支跟 tag 一起 push，避免 tag 指向一個遠端上還看不到的 commit。）

push 完成後，告知使用者：commit、tag、push 都已完成，GitHub 上應該
可以看到 `v<版本號>` 這個 tag 跟對應的 commit 了，接著繼續步驟 13。

### 13. 建立 GitHub Release（執行前務必先明確確認）

用 `gh` CLI（假設已安裝並登入，`/released` 執行前不主動檢查，失敗了
再處理即可）把 `release_output/` 底下這次的三個產物（GUI exe、CLI
exe、Setup 安裝檔）當附件，連同 Release Notes 建立成一個 GitHub
Release，掛在剛剛 push 上去的 `v<版本號>` tag 上。

**明確列出這次要建立的 GitHub Release 內容**（tag、標題、附件檔名跟
大小、Release 說明取自哪裡），完整呈現給使用者看過一遍，**取得明確
同意後才執行**——建立 Release 是對外可見、一旦有人下載附件就很難假裝
沒發生過的動作，即使已經寫進這個 skill 裡，也不能每次自動跳過確認，
這是使用者把這個步驟加進來時明確要求保留的把關步驟。確認後執行：

```
gh release create v<版本號> \
  release_output/mac-style-windows-installer_GUI_v<版本號>.exe \
  release_output/mac-style-windows-installer_CLI_v<版本號>.exe \
  release_output/Setup_mac-style-windows-installer_v<版本號>.exe \
  --title "v<版本號>" \
  --notes-file docs/releases/PRE-RELEASE_NOTES_v<版本號>.md \
  --prerelease
```

`--prerelease`：這個 repo 目前每一輪都還是叫「Pre-release Notes」（見
既有的 `docs/releases/PRE-RELEASE_NOTES_v*.md` 命名慣例），維持跟文件用詞一致，標成
GitHub 的 Pre-release 狀態；如果之後專案進入正式穩定發布階段，這個旗標
要不要拿掉需要使用者另外決定，不要自己判斷「這次夠穩定了」就擅自拿掉。

`gh release create` 找不到指令、沒登入、或執行失敗，直接把錯誤訊息
回報給使用者，不要自己嘗試安裝/登入 `gh`（那是使用者自己環境的事）。

建立完成後，**明確告知使用者**：GitHub Release 已建立，附上
`gh release view v<版本號> --web` 這個指令讓使用者可以自己打開瀏覽器
確認頁面內容跟附件是否正確。
