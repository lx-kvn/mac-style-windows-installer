# CLAUDE.md

## 版本 tag（`v*`）只能透過 `/released` skill 產生

**絕對不要**在沒有明確執行 `/released` 這個 skill 的情況下，自己打
`git tag v<版本號>` 或 push 任何 `v*` 開頭的 tag——即使使用者只是叫你
「打包」、「編一份 exe 出來測試」、「重新編一次安裝檔」之類的請求，那些
都只是本機測試用的建置動作，不代表使用者要發布新版本。

`v*` tag 是這個 repo 判斷「這是一次正式發布」的依據（未來 GitHub Actions
的自動建置流程也會用 `v*` tag 當觸發條件），打錯 tag 會造成版本紀錄混亂、
甚至誤觸發不該發生的自動化流程。

只有當使用者明確要求執行 `/released`（或明確說要發布新版本、要建立
GitHub Release）時，才照那個 skill 裡的完整流程（版本號確認、測試、
編譯、打包、Release Notes、commit、tag、push、建立 GitHub Release，
每一步都有明確的使用者確認關卡）走到打 tag 這一步。單純的「打包來測試」
請求，只做打包，不要順便打 tag、不要 push。
