"""把「編譯安裝檔」需要的環境離線裝進客體。

## 為什麼要這一段

虛擬機的快照裡**沒有 Python**，也**沒有網路**（實測 `pypi.org` 解析不到）。
而配置精靈按下「開始編譯安裝檔」之後，是去呼叫外部的 `pyinstaller` 指令，
背後那個直譯器還要有 `pywebview`。因此只要驗證涉及「真的編一顆安裝檔
出來」，就得先把整套環境送進去——`pip install` 這條路在那台機器上不存在。

每一輪驗證都從快照重來，所以這一段每輪都要重跑。實測成本約五分鐘：傳
47 MB（VMware Tools 的檔案傳輸實測約 1.8 MB/s）加上安裝。

## 裝在哪、用什麼身分裝

裝到 `C:\\Python313`，不是使用者目錄底下——路徑固定才能在後續步驟裡直接
指名那個直譯器，不必猜使用者名稱與版本目錄。

**不以 `interactive=True` 執行這一段。** 使用者桌面那個工作階段的行程是
一般權限，寫 `C:\\Python313` 會彈出權限詢問窗，而那個窗自動化按不動（見
`.claude/skills/run-test-vm`），整段就停在那裡等一個沒有人會按的按鈕
——2026-09-12 實測，主機端等了十四分鐘沒有回來，客體裡量到 `consent.exe`
掛在工作階段 1。背景模式下同一個指令量到的是 High Mandatory Level，安裝
不會被問。這一段沒有視窗要給人看，落在看不見的工作階段沒有損失。

## PATH 的陷阱

裝完之後，**桌面那個工作階段看不到新的 PATH**：它是登入時定下來的，之後
在別的工作階段裝的東西不會出現在裡面。實測後果是配置精靈照樣跳出「缺少
編譯安裝檔所需的環境」。呼叫端啟動受測程式時要自己把 `PATH_PREFIX` 接到
前面——那也正好讓「補了 PATH／沒補 PATH」成為一組現成的對照。

## 套件檔怎麼來的

主機端先抓好再送進去（`prepare_payload()`）：Python 的離線安裝檔加上
`requirements.txt` 每一項的 wheel。其中 `pywebview` 不能單純用
`pip download`——它的相依套件 `proxy_tools` 只有原始碼、沒有現成的 wheel，
`--only-binary=:all:` 會因此一路退回到 3.4 那種舊版。改用 `pip wheel`，
讓主機當場編出 wheel 再一起送。主機與客體同為 64 位元的 CPython 3.13，
編出來的東西通用。

抓好的東西放在 repo 之外的快取目錄（預設 `%LOCALAPPDATA%` 底下），不進
git：47 MB 的第三方安裝檔不屬於原始碼。
"""
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import drive_installer_gui as drive

PYTHON_VERSION = "3.13.11"
PYTHON_SETUP = "python-%s-amd64.exe" % PYTHON_VERSION
PYTHON_URL = ("https://www.python.org/ftp/python/%s/%s"
              % (PYTHON_VERSION, PYTHON_SETUP))

GUEST_DIR = r"C:\Users\Public\provision"
GUEST_PYTHON = r"C:\Python313\python.exe"
GUEST_REPORT = r"C:\Users\Public\provision_report.txt"

# 呼叫端啟動受測程式前要接在 PATH 前面的東西（見模組說明的「PATH 的陷阱」）。
PATH_PREFIX = r"C:\Python313;C:\Python313\Scripts"

# 客體裡要確認得匯入的東西。pip 回報安裝成功不等於匯入得起來，因此逐一跑過。
_IMPORTS = (
    ("pyinstaller", "import PyInstaller; print(PyInstaller.__version__)"),
    ("pywebview", "import webview; print('yes')"),
    ("pywin32", "import win32api; print('yes')"),
    ("cryptography", "import cryptography; print(cryptography.__version__)"),
    ("winrt", "import winrt.windows.management.deployment; print('yes')"),
)

_SCRIPT_HEAD = r"""$ErrorActionPreference = 'Continue'
$report = 'C:\Users\Public\provision_report.txt'
function Note($k, $v) { Add-Content -Path $report -Value ("{0}={1}" -f $k, $v) -Encoding UTF8 }
Set-Content -Path $report -Value '# 客體建置環境' -Encoding UTF8

$dir = 'C:\Users\Public\provision'
Expand-Archive -Path "$dir\wheels.zip" -DestinationPath "$dir\wheels" -Force
Note 'wheels' (Get-ChildItem "$dir\wheels\*.whl").Count

$args = @('/quiet', 'InstallAllUsers=1', 'TargetDir=C:\Python313',
          'PrependPath=1', 'Include_launcher=1', 'Include_test=0',
          'Include_doc=0', 'AssociateFiles=0', 'Shortcuts=0')
$proc = Start-Process -FilePath "$dir\%s" -ArgumentList $args -Wait -PassThru
Note 'python_setup_exit' $proc.ExitCode

$py = 'C:\Python313\python.exe'
Note 'python_exists' (Test-Path $py)
if (Test-Path $py) {
    Note 'python_version' (& $py -c "import sys; print(sys.version.split()[0])" 2>&1)
    $out = (& $py -m pip install --no-index --find-links "$dir\wheels" `
        -r "$dir\requirements.txt" 2>&1) -join "`n"
    Set-Content -Path "$dir\pip.log" -Value $out -Encoding UTF8
    Note 'pip_tail' (($out -split "`n") | Select-Object -Last 1)
"""

_SCRIPT_TAIL = r"""    # 編譯時呼叫的是 pyinstaller 這支指令，不是模組——分開確認。
    Note 'pyinstaller_exe' (Test-Path 'C:\Python313\Scripts\pyinstaller.exe')
}
Note 'done' 'True'
"""


def guest_script():
    """客體端要跑的那段腳本。"""
    lines = [_SCRIPT_HEAD % PYTHON_SETUP]
    for name, code in _IMPORTS:
        lines.append('    Note \'has_%s\' (& $py -c "%s" 2>&1)\n' % (name, code))
    lines.append(_SCRIPT_TAIL)
    return "".join(lines)


def default_cache_dir():
    base = (os.environ.get("LOCALAPPDATA")
            or os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    return os.path.join(base, "mswi-verify", "build-env-payload")


def prepare_payload(cache_dir=None, requirements=None, log=print,
                    fetch=None, run=None):
    """在主機端備好要送進客體的東西，回傳那個目錄。

    已經備好的不重抓——每輪驗證都會呼叫這個函式，重抓 47 MB 沒有意義。
    fetch/run 是測試接縫。
    """
    cache_dir = cache_dir or default_cache_dir()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    requirements = requirements or os.path.join(repo, "requirements.txt")
    wheels = os.path.join(cache_dir, "wheels")
    os.makedirs(wheels, exist_ok=True)

    setup = os.path.join(cache_dir, PYTHON_SETUP)
    if not os.path.exists(setup):
        log("下載 " + PYTHON_SETUP + "……")
        (fetch or urllib.request.urlretrieve)(PYTHON_URL, setup)

    archive = os.path.join(cache_dir, "wheels.zip")
    if not os.path.exists(archive):
        log("抓套件……")
        runner = run or _run
        # pywebview 的相依套件 proxy_tools 只有原始碼，`pip download
        # --only-binary=:all:` 會因此退回到很舊的 pywebview（實測退到 3.4）。
        # `pip wheel` 會當場把它編成 wheel，版本才留得住。
        runner([sys.executable, "-m", "pip", "wheel", "-w", wheels,
                "-r", requirements])
        import shutil
        shutil.make_archive(os.path.join(cache_dir, "wheels"), "zip", wheels)
    return cache_dir


def _run(command):
    subprocess.run(command, check=True)


def parse_report(text):
    report = {}
    for line in text.lstrip("\ufeff").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            report[key] = value
    return report


def is_ready(report):
    """客體的建置環境到齊了沒有——逐項說出缺了什麼。

    `pip install` 印出 `Successfully installed` 不算數：那只說明檔案落地了。
    這裡看的是每一個套件真的匯入得起來（客體把匯入的輸出原樣寫回來，失敗時
    那一格會是 Python 的錯誤訊息而不是版本號）。
    """
    missing = []
    if report.get("python_exists") != "True":
        missing.append("Python 本身")
    if report.get("pyinstaller_exe") != "True":
        missing.append("pyinstaller 指令")
    for name, _code in _IMPORTS:
        value = report.get("has_" + name, "")
        if not value or "Error" in value or "Traceback" in value:
            missing.append(name)
    return missing


def provision(vm, work_dir, cache_dir=None, log=print, timeout_seconds=600):
    """把環境裝進客體，回傳客體寫回來的報告。

    不等客體那支腳本結束，改成每 15 秒取一次報告：卡住時看得到停在哪一步
    （等到回來為止的話只有「沒有回來」這個資訊），而且每次取檔都會續一次
    虛擬機的租約，長時間安裝途中不會過期。
    """
    cache_dir = prepare_payload(cache_dir, log=log)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(work_dir, exist_ok=True)

    from tools import vms

    script = os.path.join(work_dir, "provision.ps1")
    vms.write_guest_script(script, guest_script())
    vm.run_program(drive.POWERSHELL, "-NoProfile", "-Command",
                   "New-Item -ItemType Directory -Force -Path '%s' | Out-Null"
                   % GUEST_DIR)
    with drive.stage("送套件進客體（約 47 MB）", log=log):
        for name in (PYTHON_SETUP, "wheels.zip"):
            vm.copy_in(os.path.join(cache_dir, name),
                       GUEST_DIR + "\\" + name)
        vm.copy_in(os.path.join(repo, "requirements.txt"),
                   GUEST_DIR + "\\requirements.txt")
        vm.copy_in(script, GUEST_DIR + "\\provision.ps1")
    with drive.stage("裝 Python 與套件", log=log):
        # 不傳 interactive=True，理由見模組說明「裝在哪、用什麼身分裝」。
        vm.run_program(drive.POWERSHELL, "-NoProfile", "-ExecutionPolicy",
                       "Bypass", "-File", GUEST_DIR + "\\provision.ps1",
                       no_wait=True, check=False)

    local = os.path.join(work_dir, "provision_report.txt")
    text = ""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(15)
        try:
            vm.copy_out(GUEST_REPORT, local)
            with open(local, encoding="utf-8") as handle:
                text = handle.read()
            if "done=True" in text:
                return parse_report(text)
        except Exception:
            pass
    raise RuntimeError("裝建置環境逾時，客體停在：\n" + text)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="把編譯安裝檔需要的環境離線裝進客體。")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--payload-only", action="store_true",
                        help="只在主機端備好套件檔，不碰虛擬機。")
    args = parser.parse_args(argv)

    if args.payload_only:
        print(prepare_payload(args.cache_dir))
        return 0

    from tools import vms

    work_dir = args.work_dir or os.path.join(os.path.abspath("."), "provision")
    vm = vms.connect(args.machine, purpose="裝建置環境", lock_minutes=30)
    try:
        with vms.preserved_tab(vm.machine.vmx):
            vms.fresh_boot(vm)
            report = provision(vm, work_dir)
            vm.stop()
    finally:
        vms.release(args.machine)

    for key in sorted(report):
        print(key + " = " + report[key])
    missing = is_ready(report)
    if missing:
        print("\n缺：" + "、".join(missing))
        return 1
    print("\n建置環境到齊。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
