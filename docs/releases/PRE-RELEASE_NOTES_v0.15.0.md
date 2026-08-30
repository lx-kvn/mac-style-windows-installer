# v0.15.0 — Pre-release Notes

Baseline: `v0.14.1` (2026-08-26) → `v0.15.0`.

**Language: [English](#english) | [繁體中文](#繁體中文)**

> **Code signing**: This project has applied to the [SignPath Foundation](https://signpath.io/solutions/open-source-community) open-source code signing program to provide trusted, signed installers. Signing is not yet active on this release — this note will be updated once the integration is live.
> **簽章聲明**：本專案已申請加入 [SignPath Foundation](https://signpath.io/solutions/open-source-community) 開源簽章方案，用以提供受信任的已簽章安裝檔。這個版本尚未套用簽章，整合完成後會更新這則說明。

---

## English

This release rebuilds the drag-to-install gesture — the project's defining interaction — on a self-rendered implementation, brings the same gesture to the uninstaller, and adds a GUI field for install password protection. It also lands a cross-module consistency review that turned a class of silent failures into visible ones.

### New Features

**Drag-to-install is now self-rendered**

The gesture previously used the browser's built-in HTML5 drag and drop. That path has one limit which cannot be worked around: the image following the cursor is drawn by the operating system, so the page cannot control its appearance, scale or opacity, and cannot intervene when the pointer is released. The project's core identifying interaction was therefore stuck on the one mechanism in the whole interface whose appearance is not adjustable.

It is now driven by pointer events and a spring solver written for this project: press feedback, spring-following motion, a magnet near the destination, bounce-back when nothing is hit, and an absorb animation on a hit. Four design trade-offs behind this (writing the spring instead of pulling in an animation library; springs instead of fixed-length CSS animations; overlap-based hit testing instead of momentum projection; no separate "skip the drag" button) are recorded in [`docs/adr/0002`](../adr/0002-drag-to-install-self-rendered-drag.md).

**The uninstaller uses the same drag**

Dragging the app icon to the trash was still on the built-in HTML5 drag and drop — the mechanism the decision above had already rejected. Both ends now share one implementation (`ui/drag_to_target.js`), with only the destination's own response parameterised through callbacks (the installer's folder swallows; the trash lid opens and closes).

This also filled an accessibility gap that was total rather than partial: the uninstaller's icon had no `tabindex`, no `role` and no keyboard handler at all, and dragging is the only trigger on that screen — meaning the uninstall interface was entirely unusable by keyboard. Mouse and keyboard now go through the same trigger function at both ends.

**Install password protection in the config wizard**

Password protection previously existed only on the command line, through `install_password_env`. The wizard now offers both input modes: name an environment variable, or type the password directly into the form. The direct-entry path passes the password as a separate parameter and never writes it to the config file — config files get committed to repositories and shared with colleagues, and a password stored in one defeats the protection entirely. The config file continues to reject an `install_password` field outright rather than silently ignoring it. See [`docs/adr/0004`](../adr/0004-inline-install-password-is-gui-only.md).

### Bug Fixes

**Install could be triggered a second time mid-flight**

Reported from real use: grab the app icon before the success screen appears, then drop it on the destination after the screen has switched, and the install runs again. Three compounding causes. The absorb animation cleared its own flag before invoking the install, so the icon stayed draggable for the entire install. The pointer handler checked only that flag and had no notion of "installing" or "installed". And the success overlay cannot stop it: a pointer that has already been captured bypasses hit testing entirely, so the destination still had real coordinates underneath.

Fixed at both layers — the front end now has an explicit install state and actively terminates an in-flight drag when the result screen appears; `trigger_installation()` gained re-entrancy protection so a second call cannot start a concurrent install. A worse window than the one reported also existed: a second drop during the progress bar would have started two installs at once.

**The destination icon stayed clickable during install**

Also reported from real use. Choosing a new destination mid-install does not affect where the files actually go — the backend fixes the path when the call is made — but the screen would then show a path that does not match the real install location.

**PATH removal always reported failure**

`uninstall.py`'s `remove_from_path()` is a thin delegate to `system_entries`, and it was missing its `return`. Removal succeeded and PATH was genuinely cleaned, but the uninstall screen unconditionally displayed "failed to remove the install path from the PATH environment variable" — a pure false alarm. Both sides' tests happened to bypass that layer, and the test file even carried a comment declaring these delegates too thin to be worth testing; that judgement was the opening.

**Cross-module consistency review (F01–F15)**

A read-through comparing the installer, uninstaller and packaging tool against each other found fifteen inconsistencies, fixed across five rounds. The most consequential:

- Removal helpers for registry entries, shortcuts, PATH and file associations returned nothing, so failures were logged as successes. They now return a defined value — "the target does not exist after this call" — which makes "the user already removed it by hand" a success and only a genuine failure a failure.
- Four removal points now try both locations (per-machine and per-user). The `no_admin_install` flag recorded in the manifest can disagree with the mode actually used at install time; when it did, entries in the other location were never found and stayed behind permanently.
- `tasklist` process detection was built by string concatenation with `shell=True` in both the installer and uninstaller. An app named `My&App.exe` would break the command apart, and the "the program is still running" guard would silently stop working.
- Disk space is now checked per drive, since `local_appdata_files` can place files on a different drive from the main install.
- Contradictory packaging options and malformed version strings are now rejected at pack time rather than producing a broken installer.

### Improvements

**Shared front-end modules**

Three files now hold what the three screens previously duplicated: `ui/spring.js` (the spring solver), `ui/drag_to_target.js` (the drag gesture), `ui/i18n.js` (the translation mechanism). Consolidating the two spring implementations surfaced a real defect in one of them: the angular spring used for the trash lid lacked the fixed sub-step integration that the positional one documents as necessary, so a dropped frame could make it diverge.

**Workspace overwrite policy inverted**

`ensure_workspace_files()` used to overwrite a fixed list and treat everything else as copy-if-missing, which meant a newly added interface file would keep a stale workspace copy forever. The whitelist now names only the assets a user may customise (`folder_icon.png`, `trash_body.svg`, `trash_lid.svg`); everything else is always overwritten. Adding a new interface file no longer requires remembering to update a list.

**Install-result dialogs are now full-screen views**

The installer's result dialogs were cards floating over the drag screen; they are now full-screen views, consistent with the rest of the flow.

### Documentation

`CONTEXT.md` gained the distinction between drag-to-install and window dragging. Three ADRs were added: the self-rendered drag and its trade-offs (0002), allowing a pre-release suffix in version strings (0003), and inline install passwords being GUI-only (0004). `CLAUDE.md` gained a documentation-writing standard, a rule on confirming before committing, and a note on where documents belong. The specification document gained §8.36 for install password protection.

### Testing

852 tests, all passing (651 at v0.14.1).

---

## 繁體中文

發布基準：`v0.14.1`（2026-08-26）→ `v0.15.0`。這個版本把拖曳安裝——這個專案的識別動作——改建在自繪的實作上，讓解除安裝端也用上同一套手勢，並在配置精靈補上安裝密碼保護的欄位。同時完成一輪跨模組一致性稽核，把一類「安靜地失敗」變成看得見的失敗。

### 新功能

**拖曳安裝改成自繪**

這個手勢原本用瀏覽器內建的 HTML5 拖放。那條路徑有一個繞不過去的限制：跟著游標移動的那張影像由作業系統繪製，網頁端無法控制它的外觀、縮放、透明度，也無法在放開時介入。等於整個專案的核心識別動作，剛好卡在整份介面裡最不能調整外觀的機制上。

現在改由 pointer 事件搭配自行實作的彈簧求解器驅動：按下的即時回饋、跟手的彈簧位移、靠近目的地的磁吸、沒命中時的彈回、命中時的吸入。背後四個取捨（自己寫彈簧而不引入動畫函式庫、用彈簧而非固定長度的 CSS 動畫、採重疊即命中而非依放開速度推算落點、不另外提供「跳過拖曳」按鈕）記錄在 [`docs/adr/0002`](../adr/0002-drag-to-install-self-rendered-drag.md)。

**解除安裝端改用同一套拖曳**

把圖示拖到垃圾桶那個動作原本仍停在瀏覽器內建的拖放上——也就是上面那個決定已經否定掉的機制。兩端現在共用同一份實作（`ui/drag_to_target.js`），只有目的地自己的回應用 callback 參數化（安裝端的資料夾吞一下、解除安裝端的垃圾桶掀蓋闔蓋）。

同時補上一個完全缺席的無障礙缺口：解除安裝端的圖示原本沒有 `tabindex`、沒有 `role`、沒有任何鍵盤事件，而拖曳是那個畫面唯一的觸發點——等於那個介面對鍵盤使用者完全不可用。現在兩端一致，滑鼠與鍵盤走同一個觸發函式。

**配置精靈補上安裝密碼保護**

密碼保護原本只有命令列那條路（`install_password_env`）。配置精靈現在兩種輸入方式都給：指定一個存放密碼的環境變數名稱，或直接在畫面上輸入密碼。直接輸入那條路的密碼以獨立參數傳遞，不會寫進設定檔——設定檔會被存進專案、傳給同事，密碼寫在裡面等於整個保護失效。設定檔仍然對 `install_password` 這個欄位直接回報驗證失敗，不默默忽略。見 [`docs/adr/0004`](../adr/0004-inline-install-password-is-gui-only.md)。

### 錯誤修正

**安裝進行中還能再觸發第二次安裝**

使用者實測回報：在成功畫面出現之前抓住應用程式圖示，畫面切過去之後再放到安裝目的地上，安裝會被觸發第二次。成因有三個且互相疊加。吸入動畫在呼叫安裝之前就把自己的旗標清掉，所以整段安裝期間圖示都還抓得起來。指標事件只檢查那個旗標，沒有任何「安裝進行中／已完成」的狀態被檢查。而成功彈窗擋不住它：已經被捕獲的指標事件完全不經過命中測試，底下的安裝目的地座標仍然是真實的。

前後端兩層都補：前端有明確的安裝狀態，顯示結果畫面時主動終結進行中的拖曳；`trigger_installation()` 補上重入防護，第二次呼叫不會啟動並行的安裝。另外還存在一個比回報的更嚴重的空窗——在進度條期間再放一次，會有兩個安裝同時進行。

**安裝進行中安裝目的地圖示仍可點擊**

同樣由使用者實測回報。安裝中改路徑不會改變檔案實際裝到哪裡（後端拿到的路徑在呼叫當下就固定了），但畫面會顯示一個與實際安裝位置不符的路徑。

**PATH 移除永遠回報失敗**

`uninstall.py` 的 `remove_from_path()` 只是轉手給 `system_entries`，卻漏了 `return`。移除其實成功、PATH 也確實清乾淨了，解除安裝畫面仍然無條件顯示「從環境變數 PATH 移除安裝路徑失敗」——純粹的假警報。兩邊的測試剛好都繞過那一層，測試檔裡甚至還留著一段「只是薄薄一層委派，不再重複測試」的註解，那個判斷正是破口。

**跨模組一致性稽核（F01–F15）**

把安裝端、解除安裝端、打包工具三邊互相對照通讀一遍，找出十五處不一致，分五輪修完。影響較大的幾項：

- 登錄表項目、捷徑、PATH、檔案關聯的移除函式都不回傳值，失敗會被記成成功。現在回傳一個有定義的語義——「這個函式結束之後目標是否確實不存在」——讓「使用者自己手動清過」算成功，只有真正的失敗才算失敗。
- 四個移除點都改成兩個位置（機器層級與使用者層級）都嘗試。manifest 裡記的 `no_admin_install` 可能跟當初實際安裝時用的模式不符，不符時另一個位置的項目完全找不到，會永久殘留。
- 安裝端與解除安裝端偵測行程用的 `tasklist` 都是字串拼接搭配 `shell=True`。`My&App.exe` 這種檔名會讓指令被拆開，「主程式還在執行」這道保護就安靜地失效。
- 磁碟空間改成逐磁碟檢查，因為 `local_appdata_files` 可能把檔案放到跟主安裝目錄不同的磁碟。
- 矛盾的打包選項組合與格式錯誤的版本號改在打包階段就攔下來，不再產出一顆有問題的安裝檔。

### 改善

**前端共用模組**

三份畫面原本各自重複的東西收進三個檔案：`ui/spring.js`（彈簧求解器）、`ui/drag_to_target.js`（拖曳手勢）、`ui/i18n.js`（翻譯機制）。收斂兩份彈簧實作時抓到其中一份的真實缺陷：垃圾桶蓋用的角度彈簧缺少另一份明文記載為必要的固定子步長積分，掉幀時會發散。

**工作目錄的覆蓋策略反轉**

`ensure_workspace_files()` 原本是「固定清單內的覆蓋、其餘缺了才補」，代表新增的介面檔案會讓舊的工作目錄副本永遠留著。現在白名單只列使用者可自訂的資源（`folder_icon.png`、`trash_body.svg`、`trash_lid.svg`），其餘一律覆蓋。新增介面檔案不用再記得更新任何清單。

**安裝結果彈窗改成整頁畫面**

安裝端的結果彈窗原本是浮在拖曳畫面上的卡片，改成整頁畫面，跟流程其餘部分一致。

### 文件

`CONTEXT.md` 補上拖曳安裝與視窗拖曳的詞彙區分。新增三份 ADR：自繪拖曳與其四個取捨（0002）、版本號允許預發布後綴（0003）、直接輸入安裝密碼僅限 GUI（0004）。`CLAUDE.md` 補上註釋與技術規格文件的撰寫規範、commit 前要先問過的規則、文件該放哪裡。規格文件補上 §8.36 安裝密碼保護。

### 測試

852 個測試，全數通過（`v0.14.1` 當時是 651 個）。

### 已知限制

- 拖曳的手感本身無法自動化驗證。模擬的滑鼠與鍵盤事件送不進 pywebview 的 WebView2 內容區，因此每次調整都必須由人在真實視窗上實際拖過才算驗證完成。
- 安裝密碼保護的定位是存取控制（防止安裝檔被誤傳、亂用），不是防範有心人暴力破解的資安機制。

### 待辦

- 人工實機比對安裝端與解除安裝端的拖曳手感是否一致。

---

## Full commit list / 完整變更（commit）

```
abb99de fix: 解除安裝的 PATH 移除結果被轉手時弄丟，導致假的失敗警告
1f219c0 docs: CLAUDE.md 補上 commit 前要先問過的規則
8fcfd63 fix: 安裝開始後安裝目的地圖示一併鎖住
af05991 fix(ui): 密碼關卡的按鈕從「不同意」改成「取消」
189fab9 chore: 測試建置改放 test_output/，不再編造版本號
e285574 docs: 更新 ADR-0002、CONTEXT.md 與規劃文件，反映拖曳兩端已經共用
383914a refactor(ui): 兩份彈簧求解器收斂成 ui/spring.js，順手修掉掉幀會發散的那份
fc0566b refactor(ui): 翻譯機制抽成 ui/i18n.js，三份畫面共用
2a5f48a feat(ui): 解除安裝端改用跟安裝端同一套自繪拖曳
0d8d0bc refactor(ui): 把自繪拖曳抽成 ui/drag_to_target.js，安裝端改為使用它
eac6694 fix(packaging): ui/ 的覆蓋策略改成白名單反轉，新增介面實作不用再記得更新清單
f65db64 fix: 安裝進行中／完成後不再能用拖曳觸發第二次安裝
82b79b6 docs: 修正規劃方向——解除安裝端要搬的是安裝端那套自繪拖曳
d92da33 docs: 補上安裝進行中仍可再次觸發安裝的缺陷規劃，與共用 JS 規劃合併成一份
3c07957 docs: 記下前端共用 JS 抽出的規劃（下一步要做的事）
b7eefc7 docs: 規格文件補上安裝密碼保護（§8.36 與 installer_config.json 欄位）
e607940 docs(config): 拿掉使用說明彈窗裡兩句已經不成立的限制
c695738 feat(config): 配置精靈補上安裝密碼保護欄位（稽核 F14 的 GUI 部分）
3b984be docs: 更新 _create_shortcut() 的說明，捷徑失敗現在會顯示給使用者
9849c0f chore: 文件更正與冗餘清理（稽核第五輪 F14 文件部分、F15）
6acee99 fix: 四個移除點都改成雙位置嘗試、補上兩個預發布版之間的比較（稽核第四輪 F12/F13）
5bf28f1 fix: 把矛盾組合與版本號格式攔在打包階段（稽核第三輪 F09/F10/F11）
69a2f95 fix: 產出物驗證與跨磁碟空間檢查（稽核第二輪 F06/F07/F08）
3ac00ed fix: 讓安裝/解除安裝的失敗不再無聲（稽核第一輪 F04/F01/F02/F03/F05）
ce8c0ae docs: 補上跨模組一致性稽核規劃與 ADR-0003
116410f docs: 補上 ADR-0002，記錄拖曳安裝改自繪的四個取捨
9331e2a fix: 吸入與吞下去的曲線改得更乾脆
99b3750 fix: 修掉安裝目的地圖示放大狀態中途沒人接手造成的停頓
01fae86 feat: 拖曳安裝改成自繪拖曳與彈簧動畫
4e648c5 docs: CONTEXT.md 補上拖曳安裝與視窗拖曳的詞彙區分
b02ca50 style: 安裝端彈窗改成無卡片的整頁畫面
5213759 docs: 補上註釋與技術規格文件的撰寫規範
4fd03f2 fix: 安裝畫面兩個圖示補上鍵盤可操作性
67abcb0 docs: 更新 §10 backlog——把已經實作完成的六項劃掉
```
