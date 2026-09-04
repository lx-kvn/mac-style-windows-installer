---
name: test-packaging-options
description: 觸發並彙整「測試所有打包功能選項」的 GitHub Actions workflow（test-packaging-options.yml）結果——實際打包一顆涵蓋主要打包欄位的 Setup.exe，靜默安裝到用完即丟的 runner 上，驗證登錄表/服務/排程工作/PATH/檔案關聯等系統層級效果真的發生，再靜默解除安裝確認清乾淨。使用者要求「測試打包選項」「驗證打包功能」或輸入 /test-packaging-options 時觸發。
---

# /test-packaging-options — 驗證所有打包功能選項真的能在真實系統上生效

`tests/test_builder.py`/`tests/test_installer_core_misc.py` 這些單元
測試全部 mock 掉 `subprocess`/`winreg`/pyinstaller，只驗證欄位有沒有
正確串接、邏輯分支對不對，**沒辦法驗證「裝到真實系統上，服務/排程工作/
PATH/檔案關聯真的被建立」這件事**。`.github/workflows/test-packaging-options.yml`
補的就是這一塊：在 GitHub 提供的 `windows-latest` runner 上實際打包、
靜默安裝、用 PowerShell 檢查系統狀態、靜默解除安裝、再檢查清乾淨，
runner 跑完就整個丟棄，不會弄髒任何人的本機環境。

這個 workflow **只用 `workflow_dispatch` 手動觸發**，跟 `build.yml`
完全獨立——不會因為平常 push/打 `v*` tag 就自動跑，不會拖慢或干擾
`/released` 的正式發布流程，也不會被那個流程間接觸發。**執行這個 skill
會真的花掉 GitHub Actions 的執行時間（一次跑完大約幾分鐘），跟本機跑
單元測試不一樣，先讓使用者知道這件事。**

## 步驟

### 1. 確認 workflow 檔案已經在遠端

```
git status --short .github/workflows/test-packaging-options.yml
```

`workflow_dispatch` 型的 workflow 要先存在於 GitHub 上的預設分支，
`gh workflow run` 才找得到——如果這個檔案還沒 commit/push，先提醒使用者，
不要自己順手 commit/push（除非使用者在這次對話裡已經明確要你這麼做）。

### 2. 觸發

```
gh workflow run test-packaging-options.yml
```

### 3. 找到剛觸發的 run，等它跑完

```
gh run list --workflow=test-packaging-options.yml --limit 1
gh run watch <run-id>
```

`gh run watch` 會一直等到 run 真正結束才回傳——這一步照這個 repo 其他
skill 的既有慣例，**中間不要臆測結果，等真正拿到 `gh run watch` 的輸出
才往下走**。這一步通常要等幾分鐘，不需要每隔幾秒自己手動重複查詢。

### 4. 彙整結果回報給使用者

跑完之後：

```
gh run view <run-id>                    # 先看整體是成功還是失敗
gh run view <run-id> --log-failed       # 失敗時，只看失敗步驟的完整輸出
```

`.github/workflows/test-packaging-options.yml` 每一項驗證都會印
`PASS: <項目>`/`FAIL: <項目>`/`WARN: <項目>` 這種固定格式的訊息（見該
workflow 的「驗證安裝後的系統狀態」/「驗證解除安裝後已清乾淨」兩個
step），把這些行整理成一份表格回報：

| 驗證項目 | 結果 |
|---|---|
| ... | PASS / FAIL / WARN |

**WARN 不等於失敗**——`file_associations`（可能受 runner 的 UAC/權杖
狀態影響）跟 `create_restore_point_before_install`（Windows Server 系
runner 通常沒有 System Restore）這兩項如實記錄在 workflow 裡是已知限制，
只警告不讓整個 job 失敗，回報時要照實說明這兩項是「這個 CI 環境測不到，
不代表功能本身有問題」，不要跟真正的 FAIL 混在一起講。

有 FAIL 的話，從 `--log-failed` 的輸出裡找出對應那個 step 的完整
PowerShell 輸出貼給使用者參考，不用整份 log 都貼。

不管成功失敗，都提醒使用者：這次 run 的安裝/解除安裝紀錄已經上傳成
`test-packaging-options-logs` 這個 artifact（`gh run download <run-id>`
可以抓下來），需要更深入排查時可以用。

## 已知限制（如實記錄，不是這個 skill 能修的問題）

- 這個矩陣**不包含** `dependencies`/`custom_dependencies`/
  `bundle_dependencies`（真的觸發外部下載，拖慢 job、依賴網路穩定度）
  跟 `signing`（需要真的憑證檔案，repo 裡沒有能在 CI 安全使用的測試
  憑證）。這幾項的欄位串接邏輯已經有單元測試覆蓋，不在這個 workflow
  的驗證範圍內。
- `create_restore_point_before_install` 在 GitHub 的 Windows Server 系
  runner 上大概率驗證不到真的建立了還原點（System Restore 這類客戶端
  功能通常不存在/預設關閉）——這件事終究要使用者自己在一台真正的
  Windows 用戶端機器上手動確認，這個 skill 沒辦法完全取代那一步。
