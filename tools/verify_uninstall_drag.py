"""驗證「把圖示拖到垃圾桶」真的會解除安裝，而且落點是有作用的。

解除安裝的唯一觸發點是那個手勢——依 ADR-0002 決定四，介面上不另外提供
「跳過拖曳」的按鈕。而既有的自動化全部走 `--silent`：CI 的移除步驟、
`/released` 的驗收都是那一條。也就是說，介面上唯一能移除程式的動作，在此
之前沒有被任何自動化碰過。

## 兩輪，以及為什麼兩輪都需要

| 輪次 | 落點 | 預期 |
| --- | --- | --- |
| 誘餌 | 視窗裡的空白處 | 東西**還在** |
| 正式 | 垃圾桶 | 東西不見了 |

「不見了」這種斷言特別容易永遠成立，因此兩件事一定要成立才算數：

- **前後量出不同的值**——同一個 `Test-Path` 在拖曳前後各量一次
  （`install_dir_before` 與 `main_exe_exists`，由 `drive_installer_gui`
  的腳本回報）。前面那次不是 True 的話這一輪判為無法判定：一台從來沒裝過
  的機器上，「不見了」本來就是真的。

## 拖完不是終點：還要按「完成」

2026-09-09 的第一輪實測量到一個中間狀態：`app.exe` 已經不見了，安裝
目錄卻還在。成因是解除安裝程式自己就住在那個目錄裡，整個目錄由
`self_delete` 排出來的背景指令刪掉，而那段指令要等使用者在完成畫面
按下「完成」才會排（見 `uninstall.py` 的 `finish_and_exit()`）。因此這裡
多按一下那顆按鈕、再量一次——順便把完成畫面也驗進來了。「完成」的
位置量自那一輪自己拍的截圖。
- **誘餌**——同樣的起點、同樣完整的滑鼠事件，只有落點不同。這一輪沒過的
  話，正式那一輪的「不見了」說明不了是垃圾桶造成的，任何一種「拖了就移除」
  的壞法都長得一樣。

## 座標怎麼來的

兩個端點以視窗矩形的比例表示，與 `drive_installer_gui.ICON_AT` 同一套。
值由 `docs/screenshots/uninstaller-zh.png` 換算而來：那張與安裝端的
`installer-drag-zh.png` 是同樣 600x420 的無邊框視窗、同樣的取景（兩張量到
的視窗卡片都是 x 53..678、y 36..441）。

換算方式本身有對照組：同一套算式套回安裝那張，重現出 `ICON_AT`／
`TARGET_AT` 這組在虛擬機上獨立量到的值，誤差在 0.001 以內。沒有這個對照
就沒辦法分辨「算對了」與「算式錯了但看起來像對的」。對照由
`tests/test_verify_uninstall_drag.py` 釘住。

判準同樣由那份測試釘住；驅動虛擬機的部分不進測試，因為真的跑一輪要好幾
分鐘。
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

# 視窗標題由 uninstall.py 寫死（無邊框視窗，畫面上看不到它）。
WINDOW_TITLE = "解除安裝"

# 應用程式圖示與垃圾桶在視窗矩形裡的相對位置（換算過程見模組說明）。
ICON_AT = (0.287, 0.442)
TRASH_AT = (0.716, 0.442)

# 誘餌的落點：視窗下緣的空白處，兩個圖示都碰不到。
NOWHERE_AT = (0.5, 0.86)

# 完成畫面上那顆「完成」。量自 2026-09-09 那一輪自己拍的截圖。
DONE_AT = (0.501, 0.668)

# 解除安裝比安裝久：要把整個目錄拿掉、再把自己刪掉。
SETTLE_SECONDS = 30


def _reached_the_drag(report, name):
    """視窗有沒有出現、滑鼠事件有沒有送出——兩者缺一，這一輪就沒有量到東西。"""
    required = ("window_found", "drag_sent", "install_dir_before",
                "install_dir_exists")
    missing = [key for key in required if key not in report]
    if missing:
        return Result(name, INCONCLUSIVE, "客體沒有回報：" + "、".join(missing))
    if report["window_found"] != "True":
        return Result(name, INCONCLUSIVE,
                      "解除安裝的視窗沒有出現，這一輪什麼都沒測到"
                      + (f"（桌面上有：{report['windows_seen']}）"
                         if report.get("windows_seen") else ""))
    if report["drag_sent"] != "True":
        return Result(name, INCONCLUSIVE, "滑鼠事件沒有送出。")
    if report["install_dir_before"] != "True":
        # 這一輪開始前就沒裝著的話，「不見了」不需要任何人動手就成立。
        return Result(name, INCONCLUSIVE,
                      "拖曳之前安裝目錄就不存在——前面那次靜默安裝沒有成功，"
                      "這一輪的「不見了」證明不了任何事。")
    return None


def evaluate_decoy(report):
    """誘餌：拖到視窗裡的空白處，東西應該還在。

    這一輪不是在驗解除安裝，而是在驗**落點有作用**。它不通過的話，下一輪的
    「不見了」有可能只是「拖了就移除」。
    """
    name = "誘餌：拖到空白處不會移除"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    # 看的是 `main_exe_exists`，跟正式那一輪同一個訊號：拖完的當下安裝
    # 目錄本來就還在（解除安裝程式自己住在裡面），拿它當判準的話，這一輪
    # 無論發生什麼都會是綠的。
    if report.get("main_exe_exists") != "True":
        return Result(name, FAIL,
                      "拖到空白處也把程式移除了——落點沒有作用，"
                      "垃圾桶那一輪因此量不到垃圾桶的效果。")
    return Result(name, PASS, "拖到空白處沒有移除任何東西。")


def evaluate_removal(report, decoy_passed):
    """正式：拖到垃圾桶上，安裝目錄要消失。"""
    name = "拖到垃圾桶會解除安裝"
    stopped = _reached_the_drag(report, name)
    if stopped:
        return stopped
    if not decoy_passed:
        return Result(name, INCONCLUSIVE,
                      "誘餌那一輪沒有通過，這裡的「不見了」分不出是垃圾桶"
                      "造成的還是拖到哪裡都會發生。")
    if report.get("main_exe_exists") != "False":
        return Result(name, FAIL, "拖到垃圾桶了，但應用程式的檔案還在。")
    # 這裡不看 `install_dir_exists`：解除安裝程式自己就住在那個目錄裡，整個
    # 目錄由背景指令刪掉，而那段指令要等使用者在完成畫面按下「完成」才會排
    # （見 uninstall.py 的 finish_and_exit()）。拖完的當下目錄還在是既定
    # 行為，2026-09-09 第一輪實測量到的正是這個中間狀態。
    if "install_dir_after_finish" not in report:
        return Result(name, INCONCLUSIVE,
                      "沒有量到按下「完成」之後的狀態——那顆按鈕沒按到的話，"
                      "自我刪除本來就不會發生。")
    if report["install_dir_after_finish"] != "False":
        return Result(name, FAIL,
                      "檔案清掉了，但按下「完成」之後整個安裝目錄仍然存在"
                      "——自我刪除沒有發生。")
    return Result(name, PASS,
                  "拖到垃圾桶清掉了程式檔案，按下「完成」之後整個安裝目錄"
                  "也消失了。")


def _round(vm, work_dir, label, setup_local, app_name, target_at, log=print,
           click_after_settle=None):
    """裝起來、開解除安裝程式、拖一次、取回結果。

    每輪之間都還原快照：前一輪留下的狀態會讓這一輪的「拖曳之前」讀到上一次
    的結果。
    """
    from tools import vms

    vms.fresh_boot(vm)
    round_dir = os.path.join(work_dir, label)
    os.makedirs(round_dir, exist_ok=True)

    remote_setup = drive.GUEST_DIR + "\\" + os.path.basename(setup_local)
    with drive.stage(f"[{label}] 送安裝檔進客體", log=log):
        vm.copy_in(setup_local, remote_setup)
    with drive.stage(f"[{label}] 靜默安裝", log=log):
        # 以桌面上那個未提升的使用者身分裝：安裝目錄在他的
        # %LOCALAPPDATA% 底下，換一個工作階段裝就會裝到別人的目錄去。
        vm.run_program(remote_setup, "/S", interactive=True, check=False)

    uninstaller = (r"$env:LOCALAPPDATA\Programs\\" + app_name
                   + r"\uninstall.exe")
    # drive.run() 回傳（報告, 它自己的判定）。這裡只要報告——它那個判定問的是
    # 「拖曳有沒有觸發安裝」，方向與這裡相反。
    report, _its_own_verdict = drive.run(
        vm, setup_local, app_name, round_dir, main_exe="app.exe",
        remote_target=uninstaller, window_title=WINDOW_TITLE,
        icon_at=ICON_AT, target_at=target_at,
        settle_seconds=SETTLE_SECONDS, click_after_settle=click_after_settle,
        log=log,
        screenshot=os.path.join(round_dir, "screen.png"))
    return report


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的字元。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    from tools import vms

    parser = argparse.ArgumentParser(
        description="驗證把圖示拖到垃圾桶會解除安裝，而且落點有作用。")
    parser.add_argument("setup", help="要測的 Setup_...exe")
    parser.add_argument("--app-name", default="UninstallProbe")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.setup))
    os.makedirs(work_dir, exist_ok=True)

    vm = vms.connect(args.machine, purpose="驗證解除安裝的拖曳")
    try:
        with vms.preserved_tab(vm.machine.vmx):
            decoy = _round(vm, work_dir, "decoy", args.setup, args.app_name,
                           NOWHERE_AT)
            trash = _round(vm, work_dir, "trash", args.setup, args.app_name,
                           TRASH_AT, click_after_settle=DONE_AT)
            vm.stop()
    finally:
        vms.release(args.machine)

    decoy_result = evaluate_decoy(decoy)
    results = [decoy_result,
               evaluate_removal(trash, decoy_result.verdict == PASS)]

    for label, report in (("誘餌", decoy), ("垃圾桶", trash)):
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
