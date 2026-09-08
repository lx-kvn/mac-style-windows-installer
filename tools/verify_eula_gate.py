"""驗證授權合約頁真的出現在畫面上，而且真的擋得住。

合約頁只出現在**互動安裝**：靜默安裝的既定行為就是跳過它（`/S` 視同已經
同意）。因此它在此之前沒有被任何自動化碰過——`/released` 的步驟 6 警告過
「忘了帶 `--config` 時 `eula_texts` 為空，安裝檔會安靜地跳過那一頁、不會
報錯」，卻同時錯誤地宣稱步驟 8 驗得到它（步驟 8 做的是靜默安裝）。

## 三輪，以及為什麼缺一不可

| 輪次 | 材料 | 動作 | 預期 |
| --- | --- | --- | --- |
| 對照組 | 沒有合約的安裝檔 | 直接拖曳 | 裝起來 |
| 擋住 | 有合約的安裝檔 | 直接拖曳 | **沒有**裝起來 |
| 同意 | 有合約的安裝檔 | 先按「同意並繼續」再拖曳 | 裝起來 |

- **沒有對照組**的話，第二輪的「沒有裝起來」什麼都不能說明——拖曳在這台
  機器上沒作用會得到一模一樣的結果。因此對照組沒過時，第二輪判為無法判定
  而不是通過。
- **沒有第三輪**的話會留一個漏洞：合約頁壞掉、按鈕點不動時，第二輪照樣
  通過（檔案確實沒落地）。第三輪要求按下同意之後裝得起來，那個壞法才擋得住。

## 座標

「同意並繼續」的位置以**視窗矩形的比例**表示，與 `drive_installer_gui` 的
`ICON_AT`／`TARGET_AT` 同一套。比例由 2026-09-09 在 win11 上實測得到：視窗
矩形 `(26,26,612,408)`、按鈕中心換算回視窗座標約 `(371, 324)`。量的時候要
注意截圖是實體像素而 `GetWindowRect` 是邏輯像素，那台機器的縮放是 125%，
兩者差 1.25 倍——直接拿截圖的像素當座標會偏掉。

送滑鼠事件的那段 C# 不在這裡複製，走 `drive_installer_gui.guest_script()`
的 `click_before_drag` 參數。

判準由 `tests/test_verify_eula_gate.py` 釘住；驅動虛擬機的部分不進測試，
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

# 「同意並繼續」在視窗矩形裡的相對位置（見模組說明的量測紀錄）。
ACCEPT_AT = (0.588, 0.779)


def _installed(report):
    return (report.get("install_dir_exists") == "True"
            and report.get("main_exe_exists") == "True")


def _reached_the_drag(report, name):
    """視窗有沒有出現、滑鼠事件有沒有送出——兩者缺一，這一輪就沒有量到東西。"""
    if report.get("window_found") != "True":
        return Result(name, INCONCLUSIVE,
                      "安裝精靈的視窗沒有出現，這一輪什麼都沒測到"
                      + (f"（桌面上有：{report['windows_seen']}）"
                         if report.get("windows_seen") else ""))
    if report.get("drag_sent") != "True":
        return Result(name, INCONCLUSIVE, "滑鼠事件沒有送出。")
    return None


def evaluate_control(report):
    """對照組：沒有合約的安裝檔，拖曳應該直接觸發安裝。

    這一輪不是在驗合約，而是在驗**拖曳這套機制在這台機器上有效**。它不通過
    的話，下一輪的「沒有裝起來」有可能只是拖曳沒作用。
    """
    name = "對照組：沒有合約時拖曳會裝起來"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if not _installed(report):
        return Result(name, FAIL,
                      "沒有合約擋著，拖曳卻沒有裝起來——問題在拖曳或這台機器，"
                      "不在合約頁")
    return Result(name, PASS, "拖曳觸發了安裝，這一輪的機制有效")


def evaluate_gate(report, control_passed):
    """有合約時，還沒同意之前拖曳不該有任何作用。"""
    name = "合約頁擋住未同意的安裝"
    if not control_passed:
        return Result(name, INCONCLUSIVE,
                      "對照組沒有通過，「沒有裝起來」有可能只是拖曳在這台機器上"
                      "沒作用——兩者在結果上長得一樣")
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if _installed(report):
        return Result(name, FAIL,
                      "還沒同意就裝起來了——畫面上沒有合約頁擋著")
    return Result(name, PASS, "拖曳沒有觸發安裝，那一頁擋住了")


def evaluate_accept(report):
    """按下「同意並繼續」之後，拖曳應該就裝得起來。

    少了這一項會留一個漏洞：合約頁壞掉、按鈕點不動時，上一項照樣通過——
    檔案確實沒有落地，只是原因完全不同。
    """
    name = "同意之後裝得起來"
    if report.get("pre_click_sent") != "True":
        return Result(name, INCONCLUSIVE, "沒有送出按下同意的那一下。")
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if not _installed(report):
        return Result(name, FAIL,
                      "按下同意之後拖曳仍然沒有裝起來——那顆按鈕沒有作用，"
                      f"點的位置是 {report.get('pre_click_at', '未回報')}")
    return Result(name, PASS, "同意之後拖曳觸發了安裝")


def _round(vm, work_dir, label, setup_local, app_name, click=None):
    """跑一輪：還原快照、送安裝檔進去、驅動畫面、取回報告。

    每輪之間都還原快照——前一輪裝過的東西留著的話，下一輪的
    `install_dir_exists` 會是上一次留下的結果。
    """
    from tools import vms

    vms.fresh_boot(vm)
    round_dir = os.path.join(work_dir, label)
    os.makedirs(round_dir, exist_ok=True)
    # drive.run() 回傳 (報告, 它自己的判定)。這裡只要報告——它那個判定問的是
    # 「拖曳有沒有觸發安裝」，而這三輪裡有一輪的正確答案正好是「沒有」。
    report, _its_own_verdict = drive.run(
        vm, setup_local, app_name, round_dir,
        main_exe="app.exe", click_before_drag=click)
    return report


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的字元。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    from tools import vms

    parser = argparse.ArgumentParser(
        description="驗證授權合約頁會出現、會擋住、同意之後可以繼續。")
    parser.add_argument("setup_with_eula", help="帶授權合約的安裝檔")
    parser.add_argument("setup_without_eula", help="不帶合約的安裝檔（對照組）")
    parser.add_argument("--app-with", default="EulaProbeWith")
    parser.add_argument("--app-without", default="EulaProbeNone")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(
        os.path.abspath(args.setup_with_eula))
    os.makedirs(work_dir, exist_ok=True)

    vm = vms.connect(args.machine, purpose="驗證授權合約頁")
    try:
        with vms.preserved_tab(vm.machine.vmx):
            control = _round(vm, work_dir, "control",
                             args.setup_without_eula, args.app_without)
            gate = _round(vm, work_dir, "gate",
                          args.setup_with_eula, args.app_with)
            accept = _round(vm, work_dir, "accept",
                            args.setup_with_eula, args.app_with,
                            click=ACCEPT_AT)
            vm.stop()
    finally:
        vms.release(args.machine)

    control_result = evaluate_control(control)
    results = [control_result,
               evaluate_gate(gate, control_result.verdict == PASS),
               evaluate_accept(accept)]

    for label, report in (("對照組", control), ("擋住", gate), ("同意", accept)):
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
