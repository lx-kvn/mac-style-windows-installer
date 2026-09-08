# v0.17.0 — Pre-release Notes

The MSIX engine can now install an application for **every user on the
machine**, which was the last unimplemented feature on the MSIX plan. This
release also adds a real end-user licence agreement, and fixes a defect that
meant MSIX users were never shown the one sentence telling them how to
uninstall.

MSIX 引擎現在可以把應用程式**安裝給這台電腦上的所有使用者**——那是 MSIX
規劃上最後一項還沒實作的功能。這一版另外新增了正式的使用者授權合約，並修正
一個缺陷：MSIX 模式的使用者從來沒看過那句告訴他去哪裡解除安裝的話。

---

## English

### New Features

**Install for every user on the machine (`msix.all_users`)**

MSIX installers have until now only ever installed for the user who ran them.
A new packaging field, `msix.all_users` (a checkbox in the configuration
wizard), asks Windows to register the application for the whole machine.

The shape of this is dictated by a system constraint rather than by
preference: registering an application for the *current* user fails with
`0x80070005` when attempted from an elevated process, while registering it for
the *machine* requires administrator rights. The two therefore cannot happen
under the same token. `Setup.exe` stays unelevated and re-launches itself with
an internal flag for the elevated half; that child verifies the package's
SHA-256 against the one the installer extracted, so the flag cannot be used to
provision anything else.

Four situations all end the same way — installed for the current user only,
with an explanation on the final screen: the user declines the permission
prompt, the account cannot elevate, the elevated step fails, or the install is
silent and unelevated. A downgrade is not an installation failure; the
application is still usable, and stopping would leave the user with nothing.

Turning the field on prints its four caveats at build time: the permission
prompt and the downgrade behaviour, that every other user must start the
application once before their own installation completes, that their file
associations do not take effect until then, and that Settings > Apps cannot
remove the machine-level registration afterwards.

Verified on Windows 11 25H2 with a real installer — see
[`docs/adr/0013`](../adr/0013-msix-all-users-scope-is-an-opt-in-field.md).

**A real end-user licence agreement**

The project previously had only the MIT `LICENSE`. A 17-clause bilingual EULA
now covers the legal position of the tool's core function — it builds
installers that write to other people's machines — and the installer this
project ships for itself now displays it. Governing law is the law of Taiwan;
the court of jurisdiction is the Taichung District Court.

**Two virtual-machine verification tools**

`tools/drive_installer_gui.py` moves the real cursor on a guest desktop and
drags the application icon onto the install target, confirming that the
gesture actually triggers an installation. `tools/measure_msix_scope.py`
measures how Windows behaves when the same package is deployed at different
user scopes. A third, `tools/verify_msix_all_users.py`, was added for this
release's acceptance round.

### Bug Fixes

**MSIX users were never shown how to uninstall**

The MSIX engine produces no uninstaller of its own (ADR-0006), so the success
message — "this application is managed by the Windows packaging engine; to
remove it, go to Settings > Apps" — is the only time the user is told where to
go. The front end passed an empty string where that message belonged, so the
finished screen was word-for-word identical to the traditional engine's.
Silent installs were unaffected: the sentence reached the `/LOG=` file, so the
person who could already read documentation saw it and the person who needed
it did not.

Found while re-shooting the README screenshots. Fixing it exposed two more
layers: those messages were Python string literals that had never been moved
to the translation mechanism introduced in v0.16.0, so an English-language
system received Chinese; and the first fix made the traditional engine's
success screen print its own title twice, which unit tests could not see and a
virtual-machine screenshot did.

**A licence clause quoted a button that does not exist**

The EULA referred to an "Agree and continue" button; the interface says
"Agree & Continue". English interface strings were also inconsistent about
"license" and "licence".

**Three defects in the verification tools, all of which needed a real run**

A drag test ran silently for ten minutes with no way to see where it was
stuck; it now reports per-stage timings and takes ninety seconds.
`FindWindow` returned 0 for 150 consecutive seconds while looking for a
window with a Chinese title that `EnumWindows` was listing at the same
moment — enumeration replaced it, and the window is now found in 8 seconds.
And a guest script read the installer's UTF-8 log using Windows PowerShell's
default ANSI code page, turning it into mojibake and failing a judgment for a
reason that had nothing to do with the product.

### Improvements

The console-encoding guard added in v0.16.0 now also covers the five
virtual-machine tools. They print text written by the guest, which they do not
control; a character the console could not encode crashed one of them on its
final line, discarding a measurement round that had already completed.

### Documentation

README screenshots were re-shot: eighteen images, one set in each language,
including a side-by-side comparison of the traditional and MSIX engines.
Images in tables now use a fixed width instead of stretching with the text
beside them.

`docs/proposals/MSIX輸出規劃.md` moved to `docs/investigations/`. Its
todo list is now empty of features, which is the condition `CLAUDE.md` sets
for that move — the first time that rule has actually been exercised.
References from twenty files were updated; the three in `docs/releases/` were
deliberately left alone, as those are frozen snapshots.

### Testing

1925 tests, up from 1679 at `v0.16.0`. The acceptance round for this release's
main feature ran on a real Windows 11 machine in Traditional Chinese with a
genuinely built installer, restoring a snapshot between each of three rounds —
a provisioning record cannot be removed through the system's own interface, so
carrying one into the next round measures something else.

### Known limitations

- Whether another account gets the application after signing in was not
  measured this round. The virtual machine's clean snapshot has only one
  enabled local account, so measuring it means creating one, and a
  freshly-created account is not necessarily the same as the one in the
  snapshot. The claim rests on an earlier measurement recorded in ADR-0013.
- The UAC prompt itself cannot be automated: Windows' secure desktop does not
  accept synthetic input. Both sides of it were verified separately; the
  "user declines" path is covered by unit tests only.
- Running a silent MSIX installation **as an administrator** exits with code 1.
  Provisioning succeeds and the machine does end up with the application —
  Windows registers it for the operator shortly afterwards — but the
  installer's own attempt to register it fails with `0x80070005`. This
  predates the feature in this release.
- Signing still uses a self-signed certificate. That is a development and
  testing measure, not a distribution one.

---

## 繁體中文

### 新功能

**安裝給這台電腦上的所有使用者（`msix.all_users`）**

MSIX 的安裝檔至今只裝給執行它的那一位使用者。新增的打包欄位
`msix.all_users`（配置精靈裡是一個勾選框）會請 Windows 把應用程式登記給
整台機器。

這個功能的形狀由系統的限制決定，不是偏好：替**當前使用者**註冊在提權的行程
裡會以 `0x80070005` 失敗，而替**整台機器**登記需要管理員權限——兩件事無法在
同一個權限下完成。因此 `Setup.exe` 本體維持未提權，需要的時候帶一個內部旗標
再啟動自己一次，那一次才提權。子行程會比對套件的 SHA-256 是否與安裝檔自己
解壓出來的那一份相同，因此那個旗標無法拿來佈建別的東西。

四種情形收斂到同一個結果——只安裝給目前這位使用者，並在完成畫面說明：使用者
拒絕權限要求、帳號本身無法提權、提權後那一步失敗、以及靜默安裝且未提權。
降級不是安裝失敗：應用程式仍然可用，中止的話使用者手上什麼都沒有。

啟用該欄位時，建置階段會印出它的四項附帶條件：權限要求與降級行為、其他使用者
必須先啟動一次應用程式才算完成安裝、在那之前他們的檔案關聯不生效、以及日後
「設定 → 應用程式」移除不掉機器層級的登記。

已於 Windows 11 25H2 以真正編出來的安裝檔驗證，見
[`docs/adr/0013`](../adr/0013-msix-all-users-scope-is-an-opt-in-field.md)。

**正式的使用者授權合約**

這個專案原本只有 MIT 的 `LICENSE`。新增的十七條中英雙語合約涵蓋這隻工具核心
功能的法律責任——它產出的安裝檔會寫入別人的電腦——而這個專案自己的安裝檔現在
也會顯示它。準據法為中華民國自由地區（臺灣）現行法律，管轄法院為臺灣臺中
地方法院。

**兩支虛擬機驗證工具**

`tools/drive_installer_gui.py` 在客體桌面上移動真正的游標，把應用程式圖示拖到
安裝目的地，確認那個手勢真的會觸發安裝。`tools/measure_msix_scope.py` 量測
同一個套件以不同使用者範圍部署時 Windows 的行為。這一版的驗收另外加了第三支
`tools/verify_msix_all_users.py`。

### 錯誤修正

**MSIX 模式的使用者從來沒看過解除安裝的說明**

MSIX 引擎不產生自己的解除安裝程式（ADR-0006），因此成功訊息那句「這個應用
程式由 Windows 的套件引擎管理，需要移除時請到『設定 → 應用程式』」是使用者
唯一被告知去哪裡移除的機會。前端在該放那則訊息的位置傳了一個空字串，於是
完成畫面與傳統引擎一字不差。靜默安裝反而不受影響：那句話進得了 `/LOG=`——
看得到的是本來就會讀文件的人，看不到的是真正需要它的人。

替 README 重拍截圖時發現的。修正它又牽出兩層：那些訊息是 Python 裡的字面
中文，從來沒有搬進 v0.16.0 建立的翻譯機制，英文環境的使用者收到的是中文；
而第一版修正讓傳統引擎的成功畫面把自己的標題印了兩次——單元測試看不出這種
事，是虛擬機的截圖顯露的。

**合約引用了一個不存在的按鈕**

EULA 裡寫的是「Agree and continue」，而介面上是「Agree & Continue」。英文介面
的 license／licence 拼法也不一致。

**驗證工具的三處缺陷，都是實際跑起來才現形的**

拖曳測試整趟不出聲跑了十分鐘，看不出卡在哪；現在會分階段報出耗時，整輪降到
九十秒。`FindWindow` 以精確標題尋找中文標題的視窗連續 150 秒回傳 0，而同一
時間 `EnumWindows` 列出的清單裡就有那個標題——改用列舉之後 8 秒就找到。另外
有一支客體腳本以 Windows PowerShell 預設的 ANSI 字碼頁去讀安裝檔寫的 UTF-8
紀錄，讀回來是一整段亂碼，於是一個其實正確的行為被判成失敗，理由與產品無關。

### 改善

v0.16.0 加入的主控台編碼防護現在也涵蓋五支虛擬機工具。它們印的是客體寫回來
的文字，內容不受它們控制；一個主控台編不出來的字元曾讓其中一支在最後一行
崩潰，一輪已經跑完的量測結果因此完全拿不到。

### 文件

README 截圖全部重拍：十八張，中英各一組，並加上傳統引擎與 MSIX 引擎的並排
對比。表格裡的圖改用固定寬度，不再被旁邊的文字長度撐得忽大忽小。

`docs/proposals/MSIX輸出規劃.md` 搬到 `docs/investigations/`。它的待辦清單上
已經沒有功能，那正是 `CLAUDE.md` 為這個搬動所設的條件——這條規則第一次真的被
執行。二十個檔案指過來的路徑一併更新；`docs/releases/` 底下的三處不動，那些是
內容凍結的快照。

### 測試

1925 項，`v0.16.0` 當時是 1679 項。這一版主要功能的驗收在一台真正的繁體中文
Windows 11 上以真正編出來的安裝檔執行，三輪之間各還原一次快照——佈建紀錄無法
從系統介面移除，帶著它量下一輪會量到另一件事。

### 已知限制

- 「另一個帳號登入之後會不會取得該應用程式」這一輪沒有量。虛擬機的乾淨快照上
  啟用中的本機帳號只有一個，要量就得自己建一個，而新建的帳號與快照裡原有的
  未必相同。該項的依據仍是 ADR-0013 裡先前的量測結果。
- UAC 那個視窗本身無法自動化：Windows 的安全桌面不接受合成輸入。它兩側的
  行為各自驗過；「使用者按下取消」那條路徑僅由單元測試涵蓋。
- **以管理員身分**執行 MSIX 的靜默安裝，結束碼是 1。佈建會成功、機器最後也
  確實取得了應用程式——Windows 會在其後替操作者補上註冊——但安裝檔自己那次
  註冊會以 `0x80070005` 失敗。此問題早於這一版的功能存在。
- 簽章仍使用自簽憑證。那是開發與測試手段，不是散布方案。

### 待辦

- 決定「以管理員身分執行靜默安裝時，結束碼要不要反映機器的最終狀態」。
- MSIX 仍未支援的三項：開機自動啟動、Windows 服務、相依元件。三者各自需要
  獨立研究，目前會擋下建置並說明。
- 正式憑證／信任鏈的最終方案——這是唯一擋住實際發布的一項。
- 協助使用者匯入憑證的輔助指令（ADR-0005 決定二），名稱、是否需要提權、
  失敗時的行為皆未定。

---

## Full commit list / 完整變更（commit）

- `ed68c00` fix(msix): 寫給 MSIX 使用者的說明從來沒有人看得到
- `f3aa8bb` docs: 新增使用者授權合約，LICENSE 的著作權人改為 lx-kvn
- `1d6ed17` docs: README 截圖全部重拍，中英各一組，並加上兩種引擎的對比
- `2058d5d` fix(ui): 英文介面的拼法與合約一致，並修正合約引用到一個不存在的按鈕
- `2d7dee3` docs: 清掉三處已經不成立的敘述，都與 WebView2 偵測有關
- `2863e43` feat(tools): 兩支驗證工具——拖曳手勢實測，與全機器範圍的兩項待驗行為
- `289aa8a` docs(adr): ADR-0013 待辦的兩項行為量測完成，全機器範圍不再有前置阻礙
- `5590c26` fix(tools): 兩支驗證工具的三處缺陷，都是實際跑起來才現形的
- `84dbe60` fix(ui): 成功畫面不再把標題原封不動再印一次
- `6d9b99f` docs(readme): 表格裡的截圖改用固定寬度，不再被欄位文字長度撐得忽大忽小
- `bfb5119` docs(screenshots): 更新授權合約頁與安裝完成頁截圖
- `be1195e` docs(screenshots): 重截 installer-done-en.png 為傳統引擎的安裝成功頁
- `3d9be4f` docs(adr): 依實測修正 ADR-0013 決定六——跨範圍也直接部署，不先移除
- `573b3c9` feat(msix): 全機器使用者範圍的打包端——新增 all_users 欄位與建置提示
- `f85e243` feat(msix): 全機器使用者範圍的安裝端——提權子行程、佈建、降級與警示
- `b9fabb3` test(msix): 全機器範圍安裝端的虛擬機驗收，四項全數通過
- `f4f5abf` docs: MSIX輸出規劃 搬進 investigations，指過來的連結一併更新
- `34b8934` fix(tools): 驗證工具延後匯入 vms，判準才會在 CI 上真的被跑到
