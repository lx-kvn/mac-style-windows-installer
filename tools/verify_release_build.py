"""發布前對真實產物的煙霧測試：裝得起來、跑得動、移得乾淨嗎。

這支工具把 `/released` 編出來的那顆 `Setup_<應用程式>_v<版本號>.exe` 送進
一台乾淨的虛擬機，靜默安裝、確認落地的內容、實際執行裝好的 CLI、再靜默
移除並確認清乾淨。

**為什麼 CI 取代不了它**（見 `CLAUDE.md`「CI 驗不到的事情」）：

- CI 的 runner 是英文的。這裡用的 `win11` 是繁體中文、字碼頁 950 的環境，
  而這個專案的訊息幾乎都是中文。編碼相關的失敗只在這種機器上現形——
  v0.16.0 的建置就是被這一類問題中斷的。
- CI 的行程本來就已提升。這裡用 `standard_user` 情境，帳號本身不在
  Administrators 群組，等同一般使用者從自己的桌面雙擊安裝檔。
- CI 每次都明確安裝所有相依套件，因此「打包機器少裝一個套件會產出什麼」
  它必然測不到。這裡的機器沒有任何開發環境，執行的是產物本身。

驗的是產物，不是原始碼：測試套件與 CI 驗的是「這份程式碼對不對」，這裡驗
的是「編出來的這三顆檔案在真實機器上會發生什麼」。兩者回答的問題不同。

判準與客體腳本的性質由 `tests/test_verify_release_build.py` 釘住；驅動虛擬
機的部分不進測試，因為真的跑一次要數分鐘。
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import vms


PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"

Result = collections.namedtuple("Result", "name verdict detail")

POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# 客體端的落腳處。使用者目錄而不是 C:\Windows\Temp：後者由工作階段 0 的
# 提升權限放入的檔案，未提升的桌面工作階段讀不到（實際踩過，症狀是客體
# 回報結束碼 0xFFFD0000，看起來像「沒有輸出」）。
DEFAULT_GUEST_DIR = r"C:\Users\User"

_REPORT_NAME = "release_report.txt"
_SCRIPT_NAME = "verify_release.ps1"


def parse_report(text):
    """把客體寫回的 `key=value` 報告解析成字典。

    只切第一個等號：值本身可能含有等號（PATH 的內容、憑證主體都是）。
    格式與 `tools/verify_msix_1809.py` 相同，兩者各自定義而不共用——共用會
    讓「客體回報格式」成為兩支工具之間的隱性契約，而它們的客體腳本本來就
    是各寫各的。
    """
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    return found


def _missing(report, keys):
    return [k for k in keys if k not in report]


_INSTALL_KEYS = ("install_exit", "install_dir_exists", "main_exe_exists",
                 "cli_exe_exists", "uninstall_entry", "path_contains")


def evaluate_install(report):
    """安裝階段的判準。

    結束碼與落地內容分開檢查：安裝到一半才失敗時檔案也會在，只看檔案會把
    那種情形判成成功。
    """
    name = "安裝：靜默安裝並落地"
    if _missing(report, _INSTALL_KEYS):
        return Result(name, INCONCLUSIVE,
                      "客體沒有回報：" + "、".join(_missing(report, _INSTALL_KEYS)))

    problems = []
    if report["install_exit"] != "0":
        problems.append("安裝程式結束碼為 " + report["install_exit"] + "，不是 0")
    for key, description in (
            ("install_dir_exists", "安裝目錄不存在"),
            ("main_exe_exists", "main_exe_exists：主程式不在安裝目錄裡"),
            ("cli_exe_exists", "cli_exe_exists：CLI 版不在安裝目錄裡"),
            ("uninstall_entry", "解除安裝登錄表項目未建立"),
            ("path_contains", "path_contains：安裝目錄沒有加進 PATH"),
    ):
        if report[key] != "True":
            problems.append(description)

    if problems:
        return Result(name, FAIL, "；".join(problems))
    return Result(name, PASS, "安裝成功，兩顆 exe、登錄表項目與 PATH 皆到位。")


def evaluate_cli_runs(report):
    """裝好的 CLI 真的執行得起來。

    這一項的價值在於它是唯一能抓到「exe 存在、一執行就缺模組」的檢查。那種
    缺陷在打包機器上完全看不出來（那台什麼都裝了），而它一路走到終端使用者
    手上才出現——這個專案實際發生過一次。
    """
    name = "執行：安裝後的 CLI 跑得動"
    if _missing(report, ("cli_exit", "cli_output")):
        return Result(name, INCONCLUSIVE, "客體沒有回報 CLI 的執行結果。")

    if report["cli_exit"] != "0":
        return Result(name, FAIL,
                      "結束碼 " + report["cli_exit"] + "，輸出：" + report["cli_output"])
    if not report["cli_output"]:
        return Result(name, FAIL, "結束碼為 0，但沒有任何輸出——它沒有真的跑到印說明那一步。")
    return Result(name, PASS, "CLI 正常執行並印出說明。")


_UNINSTALL_KEYS = ("uninstall_exit", "install_dir_gone", "uninstall_entry_gone",
                   "path_cleaned")


def evaluate_uninstall(report):
    name = "移除：靜默解除安裝並清乾淨"
    if _missing(report, _UNINSTALL_KEYS):
        return Result(name, INCONCLUSIVE,
                      "客體沒有回報：" + "、".join(_missing(report, _UNINSTALL_KEYS)))

    problems = []
    if report["uninstall_exit"] != "0":
        problems.append("解除安裝結束碼為 " + report["uninstall_exit"] + "，不是 0")
    for key, description in (
            ("install_dir_gone", "install_dir_gone：安裝目錄仍然存在"),
            ("uninstall_entry_gone", "uninstall_entry_gone：登錄表項目殘留"),
            ("path_cleaned", "path_cleaned：PATH 沒有清乾淨"),
    ):
        if report[key] != "True":
            problems.append(description)

    if problems:
        return Result(name, FAIL, "；".join(problems))
    return Result(name, PASS, "移除成功，目錄、登錄表與 PATH 皆已清除。")


def guest_script(setup_name, app_name, main_exe, cli_exe,
                 guest_dir=DEFAULT_GUEST_DIR):
    """產生客體端要跑的 PowerShell。

    每量到一項就立刻附加寫進報告檔，不在記憶體裡累積到最後才寫出：客體在
    中途卡住時，整份報告會從來沒有被寫出來，而那個結果與「腳本根本沒有執行」
    完全無法區分（實際踩過一次）。逐行附加至少留下卡在哪一步。
    """
    report = os.path.join(guest_dir, _REPORT_NAME).replace("/", "\\")
    setup = os.path.join(guest_dir, setup_name).replace("/", "\\")
    cli_out = os.path.join(guest_dir, "cli_output.txt").replace("/", "\\")

    return f"""$ErrorActionPreference = 'Continue'
$report = '{report}'
Set-Content -Path $report -Value '# 發布產物煙霧測試' -Encoding UTF8

function Note($key, $value) {{
    Add-Content -Path $report -Value ("{{0}}={{1}}" -f $key, $value) -Encoding UTF8
}}

$installDir = Join-Path $env:LOCALAPPDATA 'Programs\\{app_name}'

# --- 安裝 ---
$p = Start-Process -FilePath '{setup}' -ArgumentList '/S' -Wait -PassThru
Note 'install_exit' $p.ExitCode
Note 'install_dir_exists' (Test-Path $installDir)
Note 'main_exe_exists' (Test-Path (Join-Path $installDir '{main_exe}'))
Note 'cli_exe_exists' (Test-Path (Join-Path $installDir '{cli_exe}'))

$key = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{app_name}'
Note 'uninstall_entry' (Test-Path $key)

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $userPath) {{ $userPath = '' }}
Note 'path_contains' ($userPath -split ';' -contains $installDir)

# --- 執行裝好的 CLI ---
# 目的是確認它啟動得起來，不是驗證某個子指令的行為：exe 存在卻一執行就
# 缺模組，是這個專案實際出過的事故形態。
$cliPath = Join-Path $installDir '{cli_exe}'
if (Test-Path $cliPath) {{
    $c = Start-Process -FilePath $cliPath -ArgumentList '--help' -Wait -PassThru `
        -RedirectStandardOutput '{cli_out}' -NoNewWindow
    Note 'cli_exit' $c.ExitCode
    $text = ''
    if (Test-Path '{cli_out}') {{
        # 不指定 -Encoding：CLI 的輸出被導向檔案時，Python 用的是系統地區
        # 編碼（中文機器上是 cp950），以 UTF8 讀會得到一整片亂碼，而那看
        # 起來像產物壞了，其實只是這裡讀錯。
        $text = (Get-Content '{cli_out}' -Raw)
    }}
    if ($null -eq $text) {{ $text = '' }}
    # 報告是一行一項，輸出裡的換行要壓平，過長的也截斷。
    $flat = ($text -replace "`r?`n", ' ').Trim()
    if ($flat.Length -gt 300) {{ $flat = $flat.Substring(0, 300) }}
    Note 'cli_output' $flat
}}

# --- 移除 ---
$uninstaller = Join-Path $installDir 'uninstall.exe'
if (Test-Path $uninstaller) {{
    $u = Start-Process -FilePath $uninstaller -ArgumentList '--silent' -Wait -PassThru
    Note 'uninstall_exit' $u.ExitCode
}} else {{
    Note 'uninstall_exit' 'no-uninstaller'
}}
Note 'install_dir_gone' (-not (Test-Path $installDir))
Note 'uninstall_entry_gone' (-not (Test-Path $key))
$userPath2 = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $userPath2) {{ $userPath2 = '' }}
Note 'path_cleaned' (-not ($userPath2 -split ';' -contains $installDir))
Note 'done' 'True'
"""


def guest_home(vm):
    """客體端的落腳目錄，依實際登入的帳號決定。

    兩台機器的起始情境用不同帳號（`win11` 的標準使用者情境是 `User`，
    `win1809` 的預設情境是 `Tester`）。寫死其中一個時，另一台會把檔案放進
    一個不存在的家目錄，而 vmrun 回報的是複製失敗，訊息不會提到帳號。
    """
    user = getattr(getattr(vm, "machine", None), "user", None)
    if not user:
        return DEFAULT_GUEST_DIR
    return r"C:\Users" + "\\" + user


def run(vm, setup_path, app_name, work_dir, main_exe="mswi-gui.exe",
        cli_exe="mswi-cli.exe", guest_dir=None):
    """把安裝檔送進客體、跑完一輪、把結果帶回來。

    不負責還原快照、開機與關機：那是呼叫端的事，這裡只做「在一台已經可用的
    機器上量這件事」。
    """
    guest_dir = guest_dir or guest_home(vm)
    setup_name = os.path.basename(setup_path)
    remote_setup = os.path.join(guest_dir, setup_name).replace("/", "\\")
    vm.copy_in(setup_path, remote_setup)

    local_script = os.path.join(work_dir, _SCRIPT_NAME)
    vms.write_guest_script(
        local_script,
        guest_script(setup_name, app_name, main_exe, cli_exe, guest_dir=guest_dir))
    remote_script = os.path.join(guest_dir, _SCRIPT_NAME).replace("/", "\\")
    vm.copy_in(local_script, remote_script)

    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", remote_script)

    local_report = os.path.join(work_dir, _REPORT_NAME)
    text = ""
    try:
        vm.copy_out(os.path.join(guest_dir, _REPORT_NAME).replace("/", "\\"),
                    local_report)
        with open(local_report, encoding="utf-8") as f:
            text = f.read()
    except Exception as error:  # 客體沒寫出報告：回報量不到，不是拋例外
        text = ""
        print("取回報告失敗：" + str(error), file=sys.stderr)

    report = parse_report(text)
    return [evaluate_install(report), evaluate_cli_runs(report),
            evaluate_uninstall(report)]


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="把發布產物送進虛擬機做一輪煙霧測試（裝、跑、移）。")
    parser.add_argument("setup", help="要驗的 Setup_<應用程式>_v<版本號>.exe")
    parser.add_argument("--app-name", default="mac-style-windows-installer")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--profile", default="standard_user",
                        help="起始情境，預設是真正的標準使用者")
    parser.add_argument("--work-dir", default=None, help="放腳本與報告的本機目錄")
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.setup))
    # 目錄不存在時自己建：失敗會發生在已經還原快照、開好機之後，白等一輪。
    os.makedirs(work_dir, exist_ok=True)
    vm = vms.connect(args.machine, profile=args.profile,
                     purpose="發布產物煙霧測試：" + os.path.basename(args.setup))
    try:
        with vms.preserved_tab(vm.machine.vmx):
            vms.fresh_boot(vm)
            results = run(vm, args.setup, args.app_name, work_dir)
            vm.stop()
    finally:
        vms.release(args.machine)

    worst = PASS
    for result in results:
        print(f"[{result.verdict}] {result.name}：{result.detail}")
        if result.verdict == FAIL:
            worst = FAIL
        elif result.verdict == INCONCLUSIVE and worst != FAIL:
            worst = INCONCLUSIVE
    return 0 if worst == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
