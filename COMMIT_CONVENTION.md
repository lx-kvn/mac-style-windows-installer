# Commit 訊息規範

這個專案採用 [Conventional Commits](https://www.conventionalcommits.org/) 的精神，
但**描述本身維持繁體中文**（跟 `docs/規格文件.md`、`CONTEXT.md` 一致），只有最前面的
「類型」用英文關鍵字——這樣 `git log --oneline` 一眼就能看出這批 commit 在幹嘛，
不用點開每個 diff 才知道。

目前只是文件約定，**沒有用 git hook 強制檢查**，靠自己（或協作者）手動遵守。
如果之後想加上自動檢查，可以再另外討論。

## 格式

```
<類型>(<範圍，選填>): <簡短描述>

<詳細說明，選填，說明「為什麼」而不只是「做了什麼」>
```

- **類型**：見下方清單，固定用英文小寫。
- **範圍**：選填，這次改動主要影響哪個模組/檔案，例如 `(installer_core)`、`(file_assoc)`。範圍不明確、或改動橫跨很多檔案時可以省略。
- **簡短描述**：一行講清楚做了什麼，繁體中文，不用句號結尾。
- **詳細說明**：需要交代背景、根因、取捨時才寫，這個專案過去的 commit 訊息大多屬於這種——保留這個習慣，對照 `docs/規格文件.md` 記錄「為什麼」的風格。

## 類型清單

| 類型 | 用途 |
|---|---|
| `feat` | 新增功能 |
| `fix` | 修 bug |
| `docs` | 只改文件（README、規格文件、註解），不影響程式邏輯 |
| `style` | 純格式調整（縮排、空白、排版），不影響程式碼實際運作的意義（不是 CSS 的 style） |
| `refactor` | 重構：程式碼結構調整、行為不變，既不是修 bug 也不是加功能 |
| `test` | 新增或修改測試 |
| `chore` | 雜項維護，例如更新依賴套件、調整 `.gitignore`、建置設定 |
| `perf` | 效能優化 |
| `build` | 建置流程本身的改動（`builder.py`、`build_config_tool.py`、PyInstaller 參數） |

## 範例

比照這個專案過去的 commit 風格，改成加上類型前綴：

```
fix: 修正登錄表寫入失敗被 print() 靜默吞掉的問題

_register_file_associations()、_register_uninstall_entry()、_add_to_path_env()
原本失敗時只用 print() 回報，但 Setup_XXX.exe 是 --noconsole 編譯、print()
無效，導致登錄表實際寫入失敗時安裝仍回報「安裝成功」。現在讓例外往外拋，
交給 trigger_installation() 既有的回滾機制處理。
```

```
feat(gui_config): 新增「更新覆蓋安裝時關閉檔案總管」打包選項

有些應用程式會註冊 Windows 檔案總管殼層擴充功能，只要 explorer.exe 還活著
就會鎖住這支 DLL，更新覆蓋安裝時覆寫/刪除會失敗。這個選項讓開發者自己決定
要不要在無人值守的更新流程裡暫時關閉/重啟檔案總管。
```

```
docs: 重新梳理 README，截圖改依使用者體驗分組
```

```
test: 補齊 _backup_existing_install() 對 TEMP 環境變數為空字串的測試
```

## 跟版本號的關係（未來可以考慮，目前沒有自動化）

如果之後想串接 `semantic-release` 之類的工具自動判斷版本號、產生 changelog，
這套類型前綴就是那類工具讀取的依據，大致對應：

- `fix` → patch 版本（`0.7.0` → `0.7.1`）
- `feat` → minor 版本（`0.7.0` → `0.8.0`）
- commit 內文出現 `BREAKING CHANGE:` → major 版本（`0.7.0` → `1.0.0`）

目前這個專案的版本號（`v0.6.0`、`v0.7.0` 這類 tag）還是手動決定，這份文件先把
類型規範立起來，自動化的部分留到真的有需要再做。
