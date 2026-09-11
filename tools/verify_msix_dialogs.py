"""驗證 MSIX 引擎的兩個互動分支：降版詢問，以及傳統→MSIX 遷移。

兩個都只在互動安裝才會走到，而既有的自動化全部走靜默路徑：

- **降版詢問**——這台電腦上的版本比較新時要先問過。它與傳統引擎的降版不同：
  MSIX 降版會把應用程式的資料一併清掉，因此那一頁的訊息要說出這件事，而
  「問過」本身是不能省的一步（見 `msix_install._downgrade_question()`）。
  靜默安裝不問、直接做。
- **傳統→MSIX 遷移**——中途改採 MSIX 的專案，其既有使用者都處於「已安裝
  傳統模式版本」的狀態，那一份要在交付系統部署之前先被移除
  （見 `msix_install` 檔頭的第二輪決議第九項）。

## 四輪

| 輪次 | 前置狀態 | 動作 | 預期 |
| --- | --- | --- | --- |
| 對照組 | 乾淨 | 開起來直接拖 | 裝起來，**沒有**那一頁 |
| 取消 | 已裝 2.0.0 | 按「取消」，再拖 | 版本仍是 2.0.0 |
| 繼續 | 已裝 2.0.0 | 按「仍要繼續安裝」，再拖 | 版本變成 1.0.0 |
| 遷移 | 已裝傳統版 | 按「仍要繼續安裝」，再拖 | 傳統那份不見了，套件裝上了 |

**順序是「先按再拖」，不是反過來。** 那幾個對話框是安裝檔一開起來就問的
（前端的說明：問題一定要在觸發安裝之前問完，webview 的前端沒有辦法在 Python
呼叫中途回答問題）。2026-09-09 的第一版寫成先拖再按，結果那一下拖在對話框的
遮罩上、什麼都沒碰到，而按完按鈕之後也沒有人再拖一次——三輪都回報「按到了、
卻什麼都沒發生」。抓到它的是「按繼續」那一輪：少了它，「按取消版本不變」會
一路綠著，而那個綠與「兩下都沒按到」長得一模一樣。

對照組不是多餘的：少了它，「取消」那一輪的「版本沒變」有可能只是這顆安裝檔
在這台機器上根本裝不起來。而「繼續」那一輪證明那一頁不是壞掉——只有「取消」
的話，一個點不動的按鈕會讓它照樣通過。

每一輪都量前後兩次版本：只量後面那次的話，「版本是 2.0.0」與「這一輪什麼都
沒發生」長得一樣。

## 實測抓到的東西

2026-09-11 四輪跑完，降版那三輪全過（不問、問了按取消、問了按繼續）。
**遷移那一輪抓到殘留**：MSIX 裝上了、舊版的檔案也清掉了，但
`%LOCALAPPDATA%\Programs\<App>\` 這個目錄還在，裡面剛好剩一支
`uninstall.exe`。

成因：遷移時以 `--upgrade` 去跑舊的解除安裝程式，而
`self_delete.schedule_if_needed()` 對這個旗標不排背景自我刪除——那是故意的，
因為傳統換傳統的更新裡，那段 `rmdir /s /q` 有可能把剛複製進去的新檔案
一併砂掉。但傳統換 MSIX 沒有任何東西會複製回那個資料夾，於是留下一支
孤兒解除安裝程式：使用者雙擊它時，要移除的那份已經不在了。

這一條在產品那邊處理之前會紅——那是它該做的事。

## 材料

同一個身分、版本不同的兩顆 MSIX 安裝檔，外加一顆同名的傳統安裝檔，都以一張
自簽憑證簽章；客體要先信任那張憑證（作法沿用
`tools/verify_msix_all_users.py`）。

判準由 `tests/test_verify_msix_dialogs.py` 釘住；驅動虛擬機的部分不進測試，
因為真的跑一輪要好幾分鐘。
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_core

from tools import drive_installer_gui as drive

PASS = drive.PASS
FAIL = drive.FAIL
INCONCLUSIVE = drive.INCONCLUSIVE

Result = collections.namedtuple("Result", "name verdict detail")

POWERSHELL = drive.POWERSHELL

# 那一頁的標題，取自 `ui/index.html` 的翻譯表。
DOWNGRADE_TITLE = "這台電腦上的版本比較新"

# 對話框上的兩顆按鈕按**名字**叫，不按座標（見 `drive_installer_gui` 的
# `invoke_before_drag`）：找到之後把焦點移過去、按空白鍵，走的是使用者用鍵盤
# 操作的同一條路。第一版按座標，點得到、卻分不出「按到了但沒作用」與「根本
# 沒按到那一顆」。名字取自 `ui/index.html` 的翻譯表。
CANCEL_BUTTON = "取消"
CONTINUE_BUTTON = "仍要繼續安裝"

# 傳統引擎的更新詢問，遷移那一輪會先看到它。
UPGRADE_TITLE = "目前安裝的版本較新或相同"


def _reached_the_drag(report, name):
    required = ("window_found", "drag_sent", "package_version_before",
                "package_version_after")
    missing = [key for key in required if key not in report]
    if missing:
        return Result(name, INCONCLUSIVE, "客體沒有回報：" + "、".join(missing))
    if report["window_found"] != "True":
        return Result(name, INCONCLUSIVE,
                      "安裝精靈的視窗沒有出現，這一輪什麼都沒測到"
                      + (f"（桌面上有：{report['windows_seen']}）"
                         if report.get("windows_seen") else ""))
    if report["drag_sent"] != "True":
        return Result(name, INCONCLUSIVE, "滑鼠事件沒有送出。")
    if (report.get("before_drag_found") == "True"
            and report.get("before_drag_pressed") != "True"):
        # 那顆按鈕在畫面上，卻沒有按下去——這一輪什麼都沒測到，
        # 與「那一頁沒出現」是兩件事。
        return Result(name, INCONCLUSIVE,
                      "找到了那顆按鈕，卻沒有按下去"
                      + (f"（{report['before_drag_focus_error']}）"
                         if report.get("before_drag_focus_error") else ""))
    return None


def _asked(report):
    """那一頁有沒有真的出現。

    看的是**按鈕找不找得到**：畫面上的文字在按過之後就換掉了，而
    `window_text_after` 是拖完才讀的——那時候對話框早已關閉。按鈕找得到代表
    它當時確實在畫面上。
    """
    return report.get("before_drag_found") == "True"


def evaluate_control(report):
    """對照組：乾淨的機器上裝 1.0.0，應該直接裝起來，而且不會問降版。"""
    name = "對照組：乾淨機器上裝得起來且不問降版"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if report["package_version_before"]:
        return Result(name, INCONCLUSIVE,
                      "這一輪開始前機器上就有這個套件（版本 "
                      f"{report['package_version_before']}），不是乾淨的前置狀態。")
    if not report["package_version_after"]:
        return Result(name, FAIL,
                      "拖了之後套件沒有裝上——問題出在這顆安裝檔或這台機器，"
                      "不在那一頁。")
    if _asked(report):
        return Result(name, FAIL,
                      "機器上什麼都沒有，卻出現了降版詢問——那一頁的觸發條件"
                      "是錯的。")
    return Result(name, PASS,
                  f"裝上了 {report['package_version_after']}，沒有問降版")


def _downgrade_preconditions(report, name):
    if not report["package_version_before"]:
        return Result(name, INCONCLUSIVE,
                      "這一輪開始前套件沒有裝上，前置狀態沒有建立起來——"
                      "「版本沒變」因此證明不了任何事。")
    if not _asked(report):
        return Result(name, FAIL,
                      f"畫面上沒有「{DOWNGRADE_TITLE}」那一頁的按鈕——降版沒有"
                      "問過，而這一輪的結果不是那一頁造成的"
                      + (f"（畫面上有：{report['before_drag_seen']}）"
                         if report.get("before_drag_seen") else "") + "。")
    return None


def evaluate_cancel(report, control_passed):
    """按「取消」之後版本不該變。"""
    name = "降版時按取消，版本不變"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if not control_passed:
        return Result(name, INCONCLUSIVE,
                      "對照組沒有通過，「版本沒變」有可能只是這顆安裝檔在這台"
                      "機器上裝不起來。")
    stopped = _downgrade_preconditions(report, name)
    if stopped:
        return stopped
    before, after = report["package_version_before"], report["package_version_after"]
    if after != before:
        return Result(name, FAIL,
                      f"按了取消，版本卻從 {before} 變成 {after}。")
    return Result(name, PASS, f"問過了，按取消之後版本仍是 {after}")


def evaluate_continue(report):
    """按「仍要繼續安裝」之後版本要真的退回去。

    少了這一輪會留一個漏洞：那一頁的按鈕點不動時，「按取消版本不變」照樣
    通過。
    """
    name = "降版時按繼續，版本真的退回去"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    stopped = _downgrade_preconditions(report, name)
    if stopped:
        return stopped
    before, after = report["package_version_before"], report["package_version_after"]
    if after == before:
        return Result(name, FAIL,
                      f"按了「仍要繼續安裝」，版本仍然是 {after}——那顆按鈕"
                      "沒有作用，而「按取消版本不變」那一輪會因此照樣通過。")
    return Result(name, PASS, f"版本從 {before} 退回 {after}")


def evaluate_migration(report):
    """裝 MSIX 之前，傳統模式的那一份要先被移除。"""
    name = "傳統模式的既有安裝會先被移除"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if report.get("legacy_dir_before") != "True":
        return Result(name, INCONCLUSIVE,
                      "這一輪開始前傳統模式那一份沒有裝起來——「舊的不見了」"
                      "不需要任何人動手就成立。")
    if not _asked(report):
        return Result(name, FAIL,
                      "畫面上沒有出現更新詢問——既有的那一份沒有被偵測到，"
                      "而這一輪的結果不是遷移造成的。")
    if not report["package_version_after"]:
        return Result(name, FAIL, "MSIX 套件沒有裝上。")
    if report.get("legacy_dir_exists") != "False":
        return Result(name, FAIL,
                      "MSIX 裝上了，但傳統模式的安裝目錄還在——那一份沒有被"
                      "移除，機器上因此同時留著兩份"
                      + (f"（裡面還有：{report['legacy_left_behind']}）"
                         if report.get("legacy_left_behind") else "（裡面是空的）")
                      + "。")
    return Result(name, PASS,
                  "傳統模式那一份不見了，MSIX 套件裝上了 "
                  + report["package_version_after"])


TRUST_SCRIPT = r"""$ErrorActionPreference = 'Stop'
Import-Certificate -FilePath 'C:\Users\Public\probe.cer' `
    -CertStoreLocation 'Cert:\LocalMachine\TrustedPeople' | Out-Null
Import-Certificate -FilePath 'C:\Users\Public\probe.cer' `
    -CertStoreLocation 'Cert:\LocalMachine\Root' | Out-Null
"""


def _query_script(identity, legacy_dir, out_name):
    return f"""$ErrorActionPreference = 'Continue'
$package = Get-AppxPackage -Name '{identity}'
$version = ''
if ($package) {{ $version = $package.Version }}
# 路徑用雙引號：單引號會讓 $env:LOCALAPPDATA 原封不動地被當成目錄名。
# 先算進變數再組字串，不在陣列裡用 `+` 串接——那樣寫實測被拆成兩個元素，
# 回報變成 `legacy=` 與 `True` 兩行，判準永遠讀到空字串。
$legacy = Test-Path "{legacy_dir}"
# 目錄裡剩什麼也一併回報：「資料夾還在」與「資料夾還在而且裡面有東西」是
# 兩種不同的殘留，處置也不同。
$left = ''
if ($legacy) {{
    $left = (Get-ChildItem -Force "{legacy_dir}" | ForEach-Object {{ $_.Name }}) -join ' / '
}}
$lines = @("version=$version", "legacy=$legacy", "left=$left")
$lines | Out-File -FilePath 'C:\\Users\\Public\\{out_name}' -Encoding UTF8
"""


def _run_script(vm, work_dir, name, text, elevated=False):
    from tools import vms

    local = os.path.join(work_dir, name)
    vms.write_guest_script(local, text)
    remote = drive.GUEST_DIR + "\\" + name
    vm.copy_in(local, remote)
    # 信任憑證要系統管理員權限，走背景（已提升）那一條；查詢套件要以桌面上
    # 那位使用者的身分問，因為套件是註冊給他的。
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", remote, interactive=not elevated, check=False)


def _probe(vm, work_dir, identity, legacy_dir, label):
    """量一次「套件版本」與「傳統目錄在不在」。"""
    out_name = f"probe_{label}.txt"
    _run_script(vm, work_dir, f"probe_{label}.ps1",
                _query_script(identity, legacy_dir, out_name))
    local = os.path.join(work_dir, out_name)
    version, legacy, left = "", "", ""
    try:
        vm.copy_out(drive.GUEST_DIR + "\\" + out_name, local)
        with open(local, encoding="utf-8") as handle:
            for line in handle.read().lstrip("\ufeff").splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "version":
                    version = value.strip()
                elif key.strip() == "legacy":
                    legacy = value.strip()
                elif key.strip() == "left":
                    left = value.strip()
    except Exception as error:
        print("取回套件狀態失敗：" + str(error), file=sys.stderr)
    return version, legacy, left


def _round(vm, work_dir, label, setup_local, identity, legacy_dir,
           cer, prerequisite=None, press_first=None, log=print):
    from tools import vms

    vms.fresh_boot(vm)
    round_dir = os.path.join(work_dir, label)
    os.makedirs(round_dir, exist_ok=True)

    with drive.stage(f"[{label}] 信任那張自簽憑證", log=log):
        vm.copy_in(cer, drive.GUEST_DIR + "\\probe.cer")
        _run_script(vm, round_dir, "trust.ps1", TRUST_SCRIPT, elevated=True)

    if prerequisite:
        with drive.stage(f"[{label}] 先把前置狀態裝起來", log=log):
            remote = drive.GUEST_DIR + "\\" + os.path.basename(prerequisite)
            vm.copy_in(prerequisite, remote)
            vm.run_program(remote, "/S", interactive=True, check=False)

    before_version, before_legacy, _before_left = _probe(
        vm, round_dir, identity, legacy_dir, "before")
    report, _its_own_verdict = drive.run(
        vm, setup_local, "MsixProbe", round_dir, main_exe="app.exe",
        dump_text_after=True, invoke_before_drag=press_first,
        settle_seconds=45, log=log,
        screenshot=os.path.join(round_dir, "screen.png"))
    after_version, after_legacy, after_left = _probe(
        vm, round_dir, identity, legacy_dir, "after")
    report["package_version_before"] = before_version
    report["package_version_after"] = after_version
    report["legacy_dir_before"] = before_legacy
    report["legacy_dir_exists"] = after_legacy
    report["legacy_left_behind"] = after_left
    return report


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的字元。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    from tools import vms

    parser = argparse.ArgumentParser(
        description="驗證 MSIX 的降版詢問與傳統→MSIX 遷移。")
    parser.add_argument("setup_old", help="版本較舊的 MSIX 安裝檔")
    parser.add_argument("setup_new", help="版本較新的 MSIX 安裝檔")
    parser.add_argument("setup_legacy", help="同名的傳統模式安裝檔")
    parser.add_argument("cer", help="簽章憑證的 .cer，客體要先信任它")
    parser.add_argument("--identity", default="MswiMsixProbe.DragTest")
    parser.add_argument("--legacy-dir",
                        default=r"$env:LOCALAPPDATA\Programs\MsixProbe")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.setup_old))
    os.makedirs(work_dir, exist_ok=True)

    def one(label, **kw):
        return _round(vm, work_dir, label, args.setup_old, args.identity,
                      args.legacy_dir, args.cer, **kw)

    vm = vms.connect(args.machine, purpose="驗證 MSIX 的降版詢問與遷移")
    try:
        with vms.preserved_tab(vm.machine.vmx):
            control = one("control")
            cancel = one("cancel", prerequisite=args.setup_new,
                         press_first=CANCEL_BUTTON)
            proceed = one("continue", prerequisite=args.setup_new,
                          press_first=CONTINUE_BUTTON)
            # 遷移那一輪畫面上先出現的是傳統引擎的更新詢問，要按過才輪得到拖曳。
            migration = one("migration", prerequisite=args.setup_legacy,
                            press_first=CONTINUE_BUTTON)
            vm.stop()
    finally:
        vms.release(args.machine)

    control_result = evaluate_control(control)
    results = [control_result,
               evaluate_cancel(cancel, control_result.verdict == PASS),
               evaluate_continue(proceed),
               evaluate_migration(migration)]

    for label, report in (("對照組", control), ("取消", cancel),
                          ("繼續", proceed), ("遷移", migration)):
        print(f"=== {label} 這一輪的回報 ===")
        for key in sorted(report):
            print(f"{key} = {report[key]}")
        print()

    print("=== 判定 ===")
    worst = PASS
    for result in results:
        print(f"[{result.verdict}] {result.name}：{result.detail}")
        if result.verdict != PASS:
            worst = result.verdict
    return 0 if worst == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
