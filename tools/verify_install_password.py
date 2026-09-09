"""驗證安裝密碼那一關會出現、會擋住、打錯會被拒、打對可以繼續。

那一關只出現在**互動安裝**：靜默安裝走的是 `/PASSWORD=`，密碼由命令列給，
那一頁根本不會出現（見 `installer_core.run_silent_install()`）。既有的自動化
全部走靜默路徑，因此畫面上那一關在此之前沒有被碰過——而它是這個專案唯一一個
「輸入錯了就不該讓你繼續」的欄位。

## 四輪，以及為什麼一輪都不能少

| 輪次 | 材料 | 動作 | 預期 |
| --- | --- | --- | --- |
| 對照組 | 沒有密碼的安裝檔 | 直接拖曳 | 裝起來 |
| 擋住 | 有密碼的安裝檔 | 不輸入，直接拖曳 | **沒有**裝起來 |
| 錯的密碼 | 有密碼的安裝檔 | 輸入錯的再拖 | **沒有**裝起來 |
| 正確密碼 | 有密碼的安裝檔 | 輸入對的再拖 | 裝起來 |

- **對照組**證明拖曳這套機制在這台機器上有效。它不通過的話，「擋住」那一輪
  的「沒有裝起來」有可能只是拖曳沒作用。
- **錯的密碼**證明那個欄位真的在驗。少了這一輪，「正確密碼裝得起來」與
  「隨便打什麼都裝得起來」分不出來。
- **正確密碼**證明那一關不是壞掉。少了這一輪會留一個漏洞：頁面壞掉、按鈕
  點不動時，前兩輪照樣通過。

每一輪都另外看 `install_dir_before`：拖曳之前就裝著的話，「裝起來了」不是
這一輪的功勞。

## 座標

輸入框與「確定」以視窗矩形的比例表示，與 `drive_installer_gui.ICON_AT`
同一套。值量自 2026-09-09 在 win11 上拍的那一頁截圖（量法見
`tools/verify_uninstall_drag.py` 的說明：先由畫面上的卡片邊界定出視窗矩形，
再把要的位置換算成比例）。

判準由 `tests/test_verify_install_password.py` 釘住；驅動虛擬機的部分不進
測試，因為真的跑一輪要好幾分鐘。
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

# 打包探針時用的密碼，以及一串一定不對的。兩者不能相同——相同的話第三輪
# 量到的是第四輪的結果。
PROBE_PASSWORD = "probepass123"
WRONG_PASSWORD = "definitelynotit"

# 密碼輸入框與「確定」在視窗矩形裡的相對位置（量測見模組說明）。
FIELD_AT = (0.501, 0.536)
SUBMIT_AT = (0.580, 0.673)


def _reached_the_drag(report, name):
    required = ("window_found", "drag_sent", "install_dir_before",
                "install_dir_exists", "main_exe_exists")
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
    if report["install_dir_before"] == "True":
        return Result(name, INCONCLUSIVE,
                      "拖曳之前安裝目錄就已經存在，這一輪量不出拖曳的效果。")
    return None


def _installed(report):
    return (report.get("install_dir_exists") == "True"
            and report.get("main_exe_exists") == "True")


def _typed(report, name):
    """按鍵真的送出去了嗎。沒送的話這一輪與「不輸入直接拖」是同一輪。"""
    if report.get("typed") != "True":
        return Result(name, INCONCLUSIVE,
                      "按鍵沒有送出去，這一輪與「不輸入直接拖」沒有差別。")
    return None


def evaluate_control(report):
    """對照組：沒有密碼的安裝檔，拖曳應該直接觸發安裝。

    這一輪不是在驗密碼，而是在驗**拖曳這套機制在這台機器上有效**。
    """
    name = "對照組：沒有密碼時拖曳會裝起來"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if not _installed(report):
        return Result(name, FAIL,
                      "沒有密碼的安裝檔拖了也沒裝起來——問題出在拖曳或這台"
                      "機器，不在密碼那一關。")
    return Result(name, PASS, "拖曳觸發了安裝")


def evaluate_gate(report, control_passed):
    """擋住：有密碼的安裝檔，什麼都不輸入就拖，不該裝起來。"""
    name = "沒輸入密碼時那一關擋得住"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if not control_passed:
        return Result(name, INCONCLUSIVE,
                      "對照組沒有通過，這裡的「沒裝起來」有可能只是拖曳"
                      "在這台機器上沒有作用。")
    if _installed(report):
        return Result(name, FAIL,
                      "沒有輸入密碼卻裝起來了——畫面上那一關沒有擋住。")
    return Result(name, PASS, "沒有輸入密碼時拖曳沒有觸發安裝")


def evaluate_wrong(report, control_passed):
    """錯的密碼：輸入一串不對的，不該讓它過。"""
    name = "打錯密碼會被拒絕"
    stopped = _typed(report, name) or _reached_the_drag(report, name)
    if stopped:
        return stopped
    if not control_passed:
        return Result(name, INCONCLUSIVE,
                      "對照組沒有通過，這裡的「沒裝起來」有可能只是拖曳"
                      "在這台機器上沒有作用。")
    if _installed(report):
        return Result(name, FAIL,
                      "打錯密碼也裝起來了——那個欄位收下了任何東西，"
                      "「正確密碼可以裝」因此什麼都證明不了。")
    return Result(name, PASS, "打錯密碼之後拖曳沒有觸發安裝")


def evaluate_accept(report):
    """正確密碼：輸入對的之後，拖曳應該像沒有密碼一樣正常裝起來。"""
    name = "輸入正確密碼之後可以繼續安裝"
    stopped = _typed(report, name) or _reached_the_drag(report, name)
    if stopped:
        return stopped
    if not _installed(report):
        return Result(name, FAIL,
                      "輸入了正確的密碼，拖曳仍然沒有裝起來——那一關壞了，"
                      f"點的位置是 {report.get('post_click_at', '未回報')}")
    return Result(name, PASS, "輸入正確密碼之後拖曳觸發了安裝")


def _round(vm, work_dir, label, setup_local, app_name, password=None, log=print):
    """還原快照、送安裝檔進去、走一次畫面、取回結果。

    `password` 為 None 表示這一輪不碰那個欄位（對照組與「擋住」那一輪）。
    """
    from tools import vms

    vms.fresh_boot(vm)
    round_dir = os.path.join(work_dir, label)
    os.makedirs(round_dir, exist_ok=True)
    click = FIELD_AT if password is not None else None
    # drive.run() 回傳（報告, 它自己的判定）。這裡只要報告——它那個判定問的是
    # 「拖曳有沒有觸發安裝」，而這四輪裡有兩輪的正確答案正好是「沒有」。
    report, _its_own_verdict = drive.run(
        vm, setup_local, app_name, round_dir, main_exe="app.exe",
        click_before_drag=click, type_before_drag=password,
        click_after_typing=SUBMIT_AT if password is not None else None,
        log=log, screenshot=os.path.join(round_dir, "screen.png"))
    return report


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的字元。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    from tools import vms

    parser = argparse.ArgumentParser(
        description="驗證安裝密碼那一關會出現、會擋住、打錯會被拒、打對可以繼續。")
    parser.add_argument("setup_with_password", help="有密碼保護的安裝檔")
    parser.add_argument("setup_without_password", help="沒有密碼的安裝檔（對照組）")
    parser.add_argument("--app-with", default="PwProbe")
    parser.add_argument("--app-without", default="UninstallProbe")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(
        os.path.abspath(args.setup_with_password))
    os.makedirs(work_dir, exist_ok=True)

    vm = vms.connect(args.machine, purpose="驗證安裝密碼那一關")
    try:
        with vms.preserved_tab(vm.machine.vmx):
            control = _round(vm, work_dir, "control",
                             args.setup_without_password, args.app_without)
            gate = _round(vm, work_dir, "gate",
                          args.setup_with_password, args.app_with)
            wrong = _round(vm, work_dir, "wrong",
                           args.setup_with_password, args.app_with,
                           password=WRONG_PASSWORD)
            accept = _round(vm, work_dir, "accept",
                            args.setup_with_password, args.app_with,
                            password=PROBE_PASSWORD)
            vm.stop()
    finally:
        vms.release(args.machine)

    control_result = evaluate_control(control)
    passed = control_result.verdict == PASS
    results = [control_result,
               evaluate_gate(gate, passed),
               evaluate_wrong(wrong, passed),
               evaluate_accept(accept)]

    for label, report in (("對照組", control), ("擋住", gate),
                          ("錯的密碼", wrong), ("正確密碼", accept)):
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
