"""驗證授權合約在系統語言對不到時，回退到打包時指定的那一份。

合約可以有多種語言，選哪一份的回退順序寫在 `installer_core.get_eula_text()`：
系統語言完全對到 → 打包時指定的預設語言（`eula_default_lang`）→ 字典裡第一筆
→ 空字串。前兩段在真機上從來沒有被驗過——`verify_eula_gate` 驗的是「那一頁
擋不擋得住」，與「顯示的是哪一份」是兩件事。

## 兩輪

| 輪次 | 合約表 | 預期畫面上出現 |
| --- | --- | --- |
| 對照組 | 含 zh-TW | 中文那一份 |
| 回退 | 只有 ja 與 en，`eula_default_lang` 指定 en | 英文那一份 |

回退那一輪的合約表把 `ja` 排在 `en` 前面：這樣「回退到字典第一筆」與「回退到
指定的預設語言」會得到不同的答案，判準才分得出走的是哪一條。兩者順序相同的
話，那一輪不管實作走哪一條都會通過——那種測試永遠是綠的。

對照組證明「畫面上讀得到合約內容」這件事在這台機器上成立，否則回退那一輪的
「沒有出現日文那份」在一個什麼都讀不到的情況下無條件為真。

畫面上的字經由輔助使用介面讀回來（見 `drive_installer_gui` 的 `dump_text`）。
判準由 `tests/test_verify_eula_fallback.py` 釘住；驅動虛擬機的部分不進測試，
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

# 每一份合約裡放的標記。比對標記而不是整段文字：文字換行與空白怎麼落到輔助
# 使用樹上並不受這支工具控制。
MARK_ZH = "EULAMARKZH"
MARK_EN = "EULAMARKEN"
MARK_JA = "EULAMARKJA"


def _read_something(report, name):
    if report.get("window_found") != "True":
        return Result(name, INCONCLUSIVE,
                      "安裝精靈的視窗沒有出現，這一輪什麼都沒讀到。")
    if not report.get("window_text", "").strip():
        return Result(name, INCONCLUSIVE,
                      "畫面上一個字都沒讀到——那種情況下「沒有出現某一份合約」"
                      "無條件成立。")
    return None


def _which(report):
    text = report.get("window_text", "")
    return [mark for mark in (MARK_ZH, MARK_EN, MARK_JA) if mark in text]


def evaluate_control(report):
    """對照組：合約表裡有 zh-TW，那台機器也是繁體中文，應該直接選中它。

    這一輪同時證明**畫面上讀得到合約內容**——它不通過的話，回退那一輪的
    「沒有出現日文那份」有可能只是什麼都沒讀到。
    """
    name = "對照組：系統語言對得到時顯示那一份"
    stopped = _read_something(report, name)
    if stopped:
        return stopped
    found = _which(report)
    if found != [MARK_ZH]:
        return Result(name, FAIL,
                      "系統語言是繁體中文，畫面上卻是 "
                      + ("、".join(found) if found else "一份合約都沒有"))
    return Result(name, PASS, "顯示的是系統語言那一份")


def evaluate_fallback(report, control_passed):
    """回退：對不到系統語言時，要用打包時指定的那一份。"""
    name = "對不到系統語言時回退到指定的預設語言"
    stopped = _read_something(report, name)
    if stopped:
        return stopped
    if not control_passed:
        return Result(name, INCONCLUSIVE,
                      "對照組沒有通過，畫面上讀不讀得到合約內容還沒有定論。")
    found = _which(report)
    if found == [MARK_EN]:
        return Result(name, PASS, "顯示的是指定的預設語言那一份")
    if MARK_JA in found:
        return Result(name, FAIL,
                      f"畫面上出現了 {MARK_JA}——那是合約表裡的第一筆，"
                      "代表沒有走「指定的預設語言」那一條。")
    return Result(name, FAIL,
                  "畫面上找不到任何一份合約——那一頁整個被跳過了，"
                  "而那正是打包時忘記設定合約的後果。")


def _round(vm, work_dir, label, setup_local, app_name, log=print):
    from tools import vms

    vms.fresh_boot(vm)
    round_dir = os.path.join(work_dir, label)
    os.makedirs(round_dir, exist_ok=True)
    # 不拖曳也讀得到那一頁：合約頁擋在拖曳畫面之前，而 dump_text 是在碰滑鼠
    # 之前讀的。drive.run() 仍然會拖一次，那一下拖在合約頁上、不會有作用。
    report, _its_own_verdict = drive.run(
        vm, setup_local, app_name, round_dir, main_exe="app.exe",
        # 合約頁上沒有應用程式的名字，改等那一頁自己的按鈕——不改的話
        # 「等頁面畫好」那一步每輪白等 90 秒到上限。
        dump_text=True, wait_for_text="同意並繼續", log=log,
        screenshot=os.path.join(round_dir, "screen.png"))
    return report


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的字元。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    from tools import vms

    parser = argparse.ArgumentParser(
        description="驗證授權合約的語言回退。")
    parser.add_argument("setup_with_zh", help="合約表裡有 zh-TW 的那顆（對照組）")
    parser.add_argument("setup_fallback", help="合約表裡沒有 zh-TW 的那顆")
    parser.add_argument("--app-with", default="EulaFbWithZh")
    parser.add_argument("--app-fallback", default="EulaFbFallback")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(
        os.path.abspath(args.setup_with_zh))
    os.makedirs(work_dir, exist_ok=True)

    vm = vms.connect(args.machine, purpose="驗證授權合約的語言回退")
    try:
        with vms.preserved_tab(vm.machine.vmx):
            control = _round(vm, work_dir, "control", args.setup_with_zh,
                             args.app_with)
            fallback = _round(vm, work_dir, "fallback", args.setup_fallback,
                              args.app_fallback)
            vm.stop()
    finally:
        vms.release(args.machine)

    control_result = evaluate_control(control)
    results = [control_result,
               evaluate_fallback(fallback, control_result.verdict == PASS)]

    for label, report in (("對照組", control), ("回退", fallback)):
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
