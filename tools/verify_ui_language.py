"""驗證安裝精靈的介面語言真的跟著系統語言走。

介面語言不給使用者選，由 `installer_core` 依系統語言自動偵測
（`lang_detect.detect_system_language()`）。CI 的 runner 是英文的，但它沒有
互動桌面，跑不起那個畫面；兩台虛擬機裡有畫面的那台是繁體中文。也就是說
「英文系統上介面是不是英文」從來沒有被任何地方驗過——而那是這個工具面對
非中文使用者時的全部門面。

## 兩輪互為對照

| 輪次 | 系統語言 | 畫面上應該有 | 不應該有 |
| --- | --- | --- | --- |
| 中文 | zh-TW（那台的預設） | 安裝目的地、建立桌面捷徑 | Install Location |
| 英文 | en-US（`Set-Culture` 改過） | Install Location、Create a desktop shortcut | 安裝目的地 |

兩輪缺一不可，理由不是「多驗一點」：**「畫面上沒有中文」在一個什麼字都讀不
到的情況下無條件成立**。因此每一輪同時要求「有另一種語言的字」，而另一輪
證明這套讀法在這台機器上讀得到東西。

## 實測抓到的東西

2026-09-09 第一次跑完整兩輪就抓到一個真的缺陷：英文系統上完成畫面的
標題是 `Installation Complete`，內文卻是一句中文「安裝成功」。成因是
`installer_core` 回給畫面的 `message` 寫死中文，而前端只在它與標題一字
不差時才不顯示（`messageBeyondTitle()`）——中文系統上兩者剛好一樣，因此
這個缺陷在中文機器上看不出來。同一個成因下還有十幾句錯誤訊息（磁碟空間
不足、程式執行中、檔案驗證不過……）同樣寫死中文。

判準因此多一条：完成畫面上也不能混進另一種語言的字。這一條在產品那邊
修好之前會紅——那是它該做的事。

## 英文那一輪怎麼變成英文的

`Set-Culture en-US` 改的是使用者地區設定，也就是 `GetUserDefaultLocaleName`
回傳的那一個（`lang_detect` 讀的正是它）。改動只留在那一輪的快照裡，下一輪
還原就沒了。

**不重新開機**：這台虛擬機的快照是「Tester 已登入的桌面」，重啟之後沒有人
登入，而拖曳要發生在一個真的桌面工作階段上——重啟過的那一輪連安裝精靈的視窗
都沒出現。改成不重啟：新的行程啟動時就會讀到新的地區設定，而安裝檔本來就是
改完之後才啟動的。

地區設定改成功了沒有，由**另一個行程**讀回來：改動不會反映到發出改動的那個
行程自己身上，在同一支腳本裡問 `CurrentCulture` 量到的永遠是舊值（第一版
因此回報 zh-TW）。

畫面上的字經由輔助使用介面讀回來（見 `drive_installer_gui` 的 `dump_text`）：
WebView2 把網頁內容掛在那棵樹上，因此讀到的是使用者眼睛看到的那幾個字，
不是程式碼裡的常數。

判準由 `tests/test_verify_ui_language.py` 釘住；驅動虛擬機的部分不進測試，
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

# 取自 `ui/index.html` 的翻譯表，兩個都出現在主畫面上。
ZH_MUST_HAVE = ("安裝目的地", "建立桌面捷徑")
EN_MUST_HAVE = ("Install Location", "Create a desktop shortcut")

# 裝完之後那一頁。它在此之前只有截圖，沒有任何斷言——而截圖不是判準。
ZH_RESULT_MUST_HAVE = ("安裝成功", "安裝完成後立即執行程式")
EN_RESULT_MUST_HAVE = ("Installation Complete", "Launch the app after installation")
# 每一輪同時找另一種語言的字：只找「有沒有自己這邊的」的話，讀不到東西時
# 會與「讀到了但語言不對」分不出來。
ZH_MUST_NOT_HAVE = EN_MUST_HAVE
EN_MUST_NOT_HAVE = ZH_MUST_HAVE

POWERSHELL = drive.POWERSHELL


def _read_something(report, name):
    if report.get("window_found") != "True":
        return Result(name, INCONCLUSIVE,
                      "安裝精靈的視窗沒有出現，這一輪什麼都沒讀到。")
    if not report.get("window_text", "").strip():
        return Result(name, INCONCLUSIVE,
                      "畫面上一個字都沒讀到——輔助使用介面沒有回應，"
                      "而那種情況下「沒有出現某種語言」無條件成立。")
    return None


def _judge(report, name, must_have, must_not_have, result_must_have,
           result_must_not_have):
    """主畫面與完成畫面各看一次。

    完成畫面在此之前只有截圖，沒有任何斷言。它只在裝成功之後
    才出現，因此這一項只在真的讀到那一頁的時候才成立——讀不到時判為
    無法判定，不拿「沒有錯的字」當成通過。
    """
    text = report.get("window_text", "")
    missing = [word for word in must_have if word not in text]
    intruders = [word for word in must_not_have if word in text]
    if missing or intruders:
        detail = []
        if missing:
            detail.append("畫面上找不到：" + "、".join(missing))
        if intruders:
            detail.append("卻出現了另一種語言的：" + "、".join(intruders))
        return Result(name, FAIL, "；".join(detail))

    after = report.get("window_text_after", "")
    if "window_text_after" not in report:
        return Result(name, PASS, "主畫面上的字是預期的那個語言")
    if not after.strip():
        return Result(name, INCONCLUSIVE,
                      "主畫面對了，但完成畫面一個字都沒讀到——那一頁有沒有出現"
                      "還沒有定論。")
    lost = [word for word in result_must_have if word not in after]
    if lost:
        return Result(name, FAIL,
                      "完成畫面上找不到：" + "、".join(lost))
    # 完成畫面也要沒有另一種語言的字。這一條抓到過真的缺陷（見模組
    # 說明「實測抓到的東西」）：英文系統上那一頁的標題是英文，內文卻是一句
    # 中文。中文系統上看不出來，因為兩者剛好一字不差。
    leaked = [word for word in result_must_not_have if word in after]
    if leaked:
        return Result(name, FAIL,
                      "完成畫面上混進了另一種語言的：" + "、".join(leaked))
    return Result(name, PASS, "主畫面與完成畫面上的字都是預期的那個語言")


def evaluate_chinese(report):
    """對照組：那台機器本來就是繁體中文，介面應該是中文。

    這一輪同時證明**這套讀法在這台機器上讀得到東西**——它不通過的話，英文
    那一輪的「沒有中文」有可能只是什麼都沒讀到。
    """
    name = "對照組：中文系統上介面是中文"
    stopped = _read_something(report, name)
    if stopped:
        return stopped
    return _judge(report, name, ZH_MUST_HAVE, ZH_MUST_NOT_HAVE,
                  ZH_RESULT_MUST_HAVE, EN_RESULT_MUST_HAVE)


def evaluate_english(report, control_passed):
    """英文系統上介面應該整個換成英文。"""
    name = "英文系統上介面是英文"
    stopped = _read_something(report, name)
    if stopped:
        return stopped
    locale = report.get("locale", "")
    if locale and not locale.lower().startswith("en"):
        return Result(name, INCONCLUSIVE,
                      f"系統語言仍然是 {locale}，這一輪量的還是中文那一輪。")
    if not control_passed:
        return Result(name, INCONCLUSIVE,
                      "中文那一輪沒有通過，這套讀法在這台機器上讀不讀得到"
                      "東西還沒有定論。")
    return _judge(report, name, EN_MUST_HAVE, EN_MUST_NOT_HAVE,
                  EN_RESULT_MUST_HAVE, ZH_RESULT_MUST_HAVE)


SWITCH_SCRIPT = r"""$ErrorActionPreference = 'Stop'
# Set-Culture 改的是使用者地區設定，也就是 GetUserDefaultLocaleName 回傳的
# 那一個——lang_detect 讀的正是它。
Set-Culture en-US
"""

# 改動不會反映到發出改動的那個行程自己身上，因此另外開一個行程來讀。第一版
# 在同一支腳本裡問 CurrentCulture，量到的永遠是舊值（實測回報 zh-TW）。
READ_LOCALE_SCRIPT = r"""$ErrorActionPreference = 'Continue'
$name = (Get-ItemProperty 'HKCU:\Control Panel\International').LocaleName
"$name" | Out-File -FilePath 'C:\Users\Public\locale.txt' -Encoding UTF8
"""


def _run_guest_script(vm, work_dir, name, text):
    from tools import vms

    local = os.path.join(work_dir, name)
    vms.write_guest_script(local, text)
    remote = drive.GUEST_DIR + "\\" + name
    vm.copy_in(local, remote)
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", remote, interactive=True, check=False)


def _switch_to_english(vm, work_dir, log=print):
    """把客體的使用者地區設定換成 en-US。

    不重新開機：這台虛擬機的快照是「Tester 已登入的桌面」，重啟之後沒有人
    登入，而拖曳要發生在一個真的桌面工作階段上——重啟過的那一輪連安裝精靈的
    視窗都沒出現（2026-09-09 實際踩到）。新的行程啟動時就會讀到新的地區設定，
    而安裝檔本來就是改完之後才啟動的。
    """
    with drive.stage("把系統語言換成英文", log=log):
        _run_guest_script(vm, work_dir, "switch_locale.ps1", SWITCH_SCRIPT)
        # 另開一個行程去讀，見上面的說明。
        _run_guest_script(vm, work_dir, "read_locale.ps1", READ_LOCALE_SCRIPT)


def _read_locale(vm, work_dir):
    """把客體實際的地區設定取回來。取不到就留空，判準會當作沒有這項資訊。"""
    local = os.path.join(work_dir, "locale.txt")
    try:
        vm.copy_out(drive.GUEST_DIR + "\\locale.txt", local)
        with open(local, encoding="utf-8") as f:
            return f.read().lstrip("\ufeff").strip()
    except Exception:
        return ""


def _round(vm, work_dir, label, setup_local, app_name, english=False, log=print):
    from tools import vms

    vms.fresh_boot(vm)
    round_dir = os.path.join(work_dir, label)
    os.makedirs(round_dir, exist_ok=True)
    if english:
        _switch_to_english(vm, round_dir, log=log)
    # drive.run() 回傳（報告, 它自己的判定）。這裡只要報告——它那個判定問的
    # 是拖曳有沒有觸發安裝，與語言無關。
    report, _its_own_verdict = drive.run(
        vm, setup_local, app_name, round_dir, main_exe="app.exe",
        dump_text=True, dump_text_after=True, log=log,
        screenshot=os.path.join(round_dir, "screen.png"))
    if english:
        report["locale"] = _read_locale(vm, round_dir)
    return report


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的字元。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    from tools import vms

    parser = argparse.ArgumentParser(
        description="驗證安裝精靈的介面語言跟著系統語言走。")
    parser.add_argument("setup", help="要測的 Setup_...exe")
    parser.add_argument("--app-name", default="UninstallProbe")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.setup))
    os.makedirs(work_dir, exist_ok=True)

    vm = vms.connect(args.machine, purpose="驗證介面語言")
    try:
        with vms.preserved_tab(vm.machine.vmx):
            chinese = _round(vm, work_dir, "zh", args.setup, args.app_name)
            english = _round(vm, work_dir, "en", args.setup, args.app_name,
                             english=True)
            vm.stop()
    finally:
        vms.release(args.machine)

    control_result = evaluate_chinese(chinese)
    results = [control_result,
               evaluate_english(english, control_result.verdict == PASS)]

    for label, report in (("中文", chinese), ("英文", english)):
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
