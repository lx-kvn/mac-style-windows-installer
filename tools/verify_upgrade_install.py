"""在真實機器上驗證更新覆蓋安裝：裝了舊版之後，再裝一次新版會發生什麼。

這是這個工具**唯一會刪除使用者既有檔案**的路徑：備份舊資料夾 → 呼叫舊版
的解除安裝程式把它移除 → 裝新版 → 中途失敗時把備份復原。在這支工具出現
之前，它從來沒有在任何一台真實機器上被跑過。

`upgrade.py` 的說明裡記著三輪真實抓到的 bug，其中兩輪的症狀不是當場報錯：

- **「時好時壞」**——舊版 uninstall.exe 要不要先關閉檔案總管，取決於磁碟上
  剛好留著哪一份 manifest，而不是使用者這次的選擇。
- **「回報成功但檔案沒有複製完整」**——舊版尾端那段延遲執行的背景自我刪除
  指令，在新版已經開始複製檔案之後才觸發，把整個資料夾連同剛複製好的檔案
  一起砍掉。安裝回報的是成功。

這兩種都會通過「安裝結束碼是 0」這種檢查，因此這裡驗的是**磁碟與登錄表上
的結果**：舊版的檔案有沒有真的消失、新版的有沒有真的在、登錄表寫的是哪個
版本、有沒有留下重複的項目或備份資料夾、PATH 有沒有被加第二次。

## 為什麼一定要在未提權的桌面上跑

`upgrade.py` 有一道安全防護：舊版的登錄表項目在 HKCU、而目前這個行程已經
提權時，它拒絕代為執行那支舊版解除安裝程式——HKCU 是一般使用者就寫得進去
的位置，而已提權的行程去執行那裡指到的東西，等於任意程式碼執行。

**CI 的 runner 本來就是提權的**，因此 `no_admin_install=true` 的升級路徑在
CI 上必定走進那個拒絕分支。而 `no_admin_install=true` 正是這個專案自己的
安裝檔所用的設定。這一關因此只能在虛擬機的未提權桌面上進行
（`interactive=True`）。

判準與客體腳本的性質由 `tests/test_verify_upgrade_install.py` 釘住；驅動
虛擬機的部分不進測試，因為真的跑一輪要好幾分鐘。
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_core

# `tools.vms` 不在最上層匯入：它要求只裝在開發機上的 `vm_lease`，放在最上層
# 會讓這份判準的測試在 CI 上整份跳不起來（見 verify_msix_all_users 的說明）。

PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"

Result = collections.namedtuple("Result", "name verdict detail")

POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# 未提權的桌面工作階段讀不到提權行程寫進 C:\Windows\Temp 的檔案，而這一輪
# 兩支腳本都跑在未提權的桌面上。放公共目錄，與其他幾支驗證工具一致。
GUEST_DIR = r"C:\Users\Public"
REPORT = GUEST_DIR + r"\upgrade_report.txt"

# 走進安全防護那個分支時，安裝紀錄裡會出現的字樣（`upgrade.py` 的拒絕訊息）。
# 這裡比對的是**產物實際印出來的文字**，不是匯入那個模組的常數——匯入會讓
# 「兩邊一致」無條件成立，等於沒有驗到（這個專案出過的事故形態）。
_REFUSAL_MARK = "以系統管理員權限執行"


def parse_report(text):
    """把客體寫回的 `key=value` 報告解析成字典。

    只切第一個等號：值本身含有等號是常態（路徑、PATH 內容）。開頭的 BOM 要
    剝掉——Windows PowerShell 5.1 建立檔案時會寫一個，不剝的話第一個鍵會變成
    `\ufeff...`，查表查不到，看起來像客體沒有回報那一步。
    """
    found = {}
    for line in text.lstrip("\ufeff").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    return found


def _missing(report, keys):
    return [key for key in keys if key not in report]


_UPGRADE_KEYS = ("first_exit", "first_marker_v1", "first_version",
                 "second_exit", "second_marker_v1", "second_marker_v2",
                 "second_version", "entry_count", "backup_leftovers",
                 "path_occurrences", "backup_probe_finds_decoy")


def evaluate_upgrade(report):
    """裝了舊版之後再裝新版，磁碟與登錄表上的結果對不對。"""
    name = "更新覆蓋安裝"
    absent = _missing(report, _UPGRADE_KEYS)
    if absent:
        return Result(name, INCONCLUSIVE, f"客體沒有回報：{'、'.join(absent)}")

    # 第一次就沒裝成的話，第二次其實是一次乾淨安裝——而它會全部通過，看起來
    # 像升級沒問題。那不是失敗，是這一輪沒有量到要量的東西。
    if report["first_exit"] != "0" or report["first_marker_v1"] != "True":
        return Result(name, INCONCLUSIVE,
                      f"舊版沒有裝成（結束碼 {report['first_exit']}、"
                      f"標記檔存在={report['first_marker_v1']}），"
                      "第二次安裝等於一次乾淨安裝，量不到覆蓋")

    if _REFUSAL_MARK in report.get("second_log", ""):
        # 走到安全防護那個分支，代表這一輪的行程是提權的——量到的是另一件事。
        return Result(name, INCONCLUSIVE,
                      "安裝程式走進了「已提權時拒絕代為移除 HKCU 舊版」那個"
                      "分支，這一輪不是在未提權的桌面上跑的")

    if report["backup_probe_finds_decoy"] != "True":
        # 那段搜尋連刻意放好的誘餌都找不到，「沒有殘留」這個結果不能採信。
        return Result(name, INCONCLUSIVE,
                      "備份殘留的搜尋在誘餌上就失敗了，這一項的結果不可信")

    problems = []
    if report["second_exit"] != "0":
        problems.append(f"升級的結束碼是 {report['second_exit']}，不是 0")
    if report["second_marker_v1"] != "False":
        problems.append("舊版的檔案還在——新版是疊上去，不是換掉")
    if report["second_marker_v2"] != "True":
        problems.append("新版的檔案不在安裝目錄裡")
    if report["second_version"] != report.get("expected_version", "1.1.0"):
        problems.append(f"登錄表記的版本是 {report['second_version']}，不是新版")
    if report["entry_count"] != "1":
        problems.append(f"解除安裝登錄表項目有 {report['entry_count']} 筆，應該只有 1 筆")
    if report["backup_leftovers"]:
        problems.append(f"留下了升級用的備份資料夾：{report['backup_leftovers']}")
    if report["path_occurrences"] != "1":
        problems.append(f"PATH 裡的安裝目錄出現 {report['path_occurrences']} 次，應該是 1 次")

    if problems:
        return Result(name, FAIL, "；".join(problems))
    return Result(name, PASS,
                  "舊版檔案已消失、新版就位、登錄表只有一筆且版本正確、"
                  "沒有備份殘留、PATH 沒有被加第二次")


def evaluate_uninstall_after_upgrade(report):
    """升級之後還解除安裝得掉。

    升級留下的到底是「新版本」還是「新版本加上一堆舊版本的殘骸」，這一步
    才問得出來——前一項看的是有沒有多出東西，這一項看的是移得移不乾淨。
    """
    name = "升級之後仍可解除安裝"
    absent = _missing(report, ("uninstall_exit", "install_dir_gone",
                               "entry_count_after", "path_occurrences_after"))
    if absent:
        return Result(name, INCONCLUSIVE, f"客體沒有回報：{'、'.join(absent)}")
    problems = []
    if report["uninstall_exit"] != "0":
        problems.append(f"解除安裝結束碼為 {report['uninstall_exit']}，不是 0")
    if report["install_dir_gone"] != "True":
        problems.append("安裝目錄仍然存在")
    if report["entry_count_after"] != "0":
        problems.append(f"還留著 {report['entry_count_after']} 筆解除安裝登錄表項目")
    if report["path_occurrences_after"] != "0":
        problems.append("PATH 裡還留著安裝目錄")
    if problems:
        return Result(name, FAIL, "；".join(problems))
    return Result(name, PASS,
                  "解除安裝成功，安裝目錄、登錄表項目與 PATH 皆已清除")


def _preamble(app_name):
    """兩支腳本共用的開頭：報告檔、安裝目錄、登錄表位置、讀檔工具。

    往報告檔追加而不覆寫：兩輪各自寫同一份報告，覆寫的話後跑的那一支會把
    前一支的結果清掉，而缺步驟的判定是「無法判定」——看起來像機器有問題，
    不像腳本互相覆蓋。
    """
    return """$ErrorActionPreference = 'Continue'

$app = '%(app)s'
$installDir = Join-Path $env:LOCALAPPDATA ("Programs\\" + $app)
$keyPath = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + $app

function Note($line) {
  $line | Out-File -FilePath '%(report)s' -Append -Encoding UTF8
}

function Flatten($path) {
  # -Encoding UTF8 是必要的：安裝檔把紀錄寫成 UTF-8，而 Windows PowerShell
  # 5.1 的 Get-Content 預設以系統的 ANSI 字碼頁解碼（這台機器是 cp950）。
  # 少了它讀回來是一整段亂碼，判定要找的那句話就找不到（實際踩過）。
  if (Test-Path $path) { ((Get-Content $path -Raw -Encoding UTF8) -replace "`r?`n", ' | ') }
  else { '' }
}

function DisplayVersion {
  foreach ($hive in @('HKCU:', 'HKLM:')) {
    $full = $hive + '\\' + $keyPath
    if (Test-Path $full) {
      $v = (Get-ItemProperty $full -ErrorAction SilentlyContinue).DisplayVersion
      if ($v) { return $v }
    }
  }
  return 'none'
}

function EntryCount {
  # 兩個 hive 都數：舊版可能登記在另一邊（check_existing 就是為此兩邊都查），
  # 只數一邊會把「兩筆重複」讀成「一筆正常」。
  $n = 0
  foreach ($hive in @('HKCU:', 'HKLM:')) {
    if (Test-Path ($hive + '\\' + $keyPath)) { $n = $n + 1 }
  }
  return $n
}
""" % {"app": app_name, "report": REPORT}


def first_install_script(setup_exe, app_name, marker_v1, log_path=None):
    """第一輪：把舊版裝起來，並確認它真的裝成了。"""
    log = log_path or (GUEST_DIR + r"\upgrade_first.log")
    return _preamble(app_name) + """
$log = '%(log)s'
Remove-Item $log -ErrorAction SilentlyContinue

$p = Start-Process -FilePath '%(setup)s' -ArgumentList @('/S', "/LOG=$log") -Wait -PassThru
Note ("first_exit=" + $p.ExitCode)
Note ("first_marker_v1=" + (Test-Path (Join-Path $installDir '%(marker)s')))
Note ("first_version=" + (DisplayVersion))
Note ("first_log=" + (Flatten $log))
""" % {"setup": setup_exe, "marker": marker_v1, "log": log}


def upgrade_script(setup_exe, app_name, marker_v1, marker_v2, log_path=None):
    """第二輪：在舊版還在的情況下裝新版，然後看磁碟與登錄表。"""
    log = log_path or (GUEST_DIR + r"\upgrade_second.log")
    return _preamble(app_name) + """
$log = '%(log)s'
Remove-Item $log -ErrorAction SilentlyContinue

$p = Start-Process -FilePath '%(setup)s' -ArgumentList @('/S', "/LOG=$log") -Wait -PassThru
Note ("second_exit=" + $p.ExitCode)
Note ("second_marker_v1=" + (Test-Path (Join-Path $installDir '%(marker1)s')))
Note ("second_marker_v2=" + (Test-Path (Join-Path $installDir '%(marker2)s')))
Note ("second_version=" + (DisplayVersion))
Note ("entry_count=" + (EntryCount))

# 升級用的備份是中繼物，成功之後應該被丟棄。留著的話使用者的磁碟上多出一份
# 完整的舊安裝，而沒有任何介面提到它。
#
# 名字是 `mswi_upgrade_backup_<pid>`，**不含應用程式名稱**（見 upgrade.backup()）。
# 第一版這裡要求名字同時含 'backup' 與應用程式名，那個條件永遠不成立，這條
# 斷言因此從來驗不到東西——第一次跑就全綠時去對了一次實作才發現。
function FindBackups {
  $found = @()
  foreach ($root in @($env:TEMP, $env:TMP)) {
    if ($root -and (Test-Path $root)) {
      Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'mswi_upgrade_backup*' } |
        ForEach-Object { $found += $_.FullName }
    }
  }
  return ($found | Sort-Object -Unique)
}

Note ("backup_leftovers=" + ((FindBackups) -join ';'))

# 「沒有找到殘留」與「搜尋條件寫錯、什麼都找不到」在結果上長得一樣，而後者
# 真的發生過。先在一個刻意建立的資料夾上跑同一段搜尋，證明它找得到東西；
# 用完立刻刪掉，否則誘餌自己會變成下一輪的殘留。
$decoy = Join-Path $env:TEMP 'mswi_upgrade_backup_decoy'
New-Item -ItemType Directory -Path $decoy -Force | Out-Null
Note ("backup_probe_finds_decoy=" + [bool]((FindBackups) -contains $decoy))
Remove-Item $decoy -Recurse -Force -ErrorAction SilentlyContinue

# PATH 被加第二次的話會隨著每次升級愈來愈長，而症狀要很久以後才顯現。
$userPath = (Get-ItemProperty 'HKCU:\\Environment' -Name Path -ErrorAction SilentlyContinue).Path
$count = 0
if ($userPath) {
  foreach ($part in ($userPath -split ';')) {
    if ($part.TrimEnd('\\') -eq $installDir.TrimEnd('\\')) { $count = $count + 1 }
  }
}
Note ("path_occurrences=" + $count)
Note ("second_log=" + (Flatten $log))
""" % {"setup": setup_exe, "marker1": marker_v1, "marker2": marker_v2, "log": log}


def uninstall_script(app_name, log_path=None):
    """第三輪：升級之後還解除安裝得掉嗎。"""
    log = log_path or (GUEST_DIR + r"\upgrade_uninstall.log")
    return _preamble(app_name) + """
$log = '%(log)s'
$uninstaller = Join-Path $installDir 'uninstall.exe'
if (Test-Path $uninstaller) {
  $u = Start-Process -FilePath $uninstaller -ArgumentList @('--silent', "/LOG=$log") -Wait -PassThru
  Note ("uninstall_exit=" + $u.ExitCode)
} else {
  Note ("uninstall_exit=no-uninstaller")
}
# 解除安裝會排程一個背景指令刪掉 uninstall.exe 自己（見 self_delete.py），
# 給它幾秒鐘完成，下面那一項才會準確。
Start-Sleep -Seconds 8
Note ("install_dir_gone=" + (-not (Test-Path $installDir)))

# 用**同一組**函式再量一次登錄表項目與 PATH。兩件事同時成立：解除安裝真的
# 清乾淨了（新的涵蓋），以及升級那一輪量到的 1 不是「這段程式碼永遠回傳的
# 東西」——備份那一項就是這樣被抓到的（條件寫錯，永遠是空的）。
Note ("entry_count_after=" + (EntryCount))
$userPath = (Get-ItemProperty 'HKCU:\\Environment' -Name Path -ErrorAction SilentlyContinue).Path
$count = 0
if ($userPath) {
  foreach ($part in ($userPath -split ';')) {
    if ($part.TrimEnd('\\') -eq $installDir.TrimEnd('\\')) { $count = $count + 1 }
  }
}
Note ("path_occurrences_after=" + $count)
Note ("uninstall_log=" + (Flatten $log))
""" % {"log": log}


def _push(vm, work_dir, name, source):
    from tools import vms

    local = os.path.join(work_dir, name)
    vms.write_guest_script(local, source)
    remote = GUEST_DIR + "\\" + name
    vm.copy_in(local, remote)
    return remote


def _run(vm, script):
    # interactive=True：這一輪的全部意義就在於它跑在未提權的桌面上。
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", script, interactive=True, check=False)


def run(vm, setup_v1, setup_v2, app_name, marker_v1, marker_v2, work_dir):
    """三輪，同一次開機：裝舊版 → 裝新版 → 解除安裝。

    不還原快照：這一輪要驗的正是「機器上已經有東西」時的行為，中間還原
    等於把前提清掉。
    """
    remote_v1 = GUEST_DIR + "\\" + os.path.basename(setup_v1)
    remote_v2 = GUEST_DIR + "\\" + os.path.basename(setup_v2)
    vm.copy_in(setup_v1, remote_v1)
    vm.copy_in(setup_v2, remote_v2)

    _run(vm, _push(vm, work_dir, "upgrade_first.ps1",
                   first_install_script(remote_v1, app_name, marker_v1)))
    _run(vm, _push(vm, work_dir, "upgrade_second.ps1",
                   upgrade_script(remote_v2, app_name, marker_v1, marker_v2)))
    _run(vm, _push(vm, work_dir, "upgrade_uninstall.ps1",
                   uninstall_script(app_name)))

    local = os.path.join(work_dir, "upgrade_report.txt")
    try:
        vm.copy_out(REPORT, local)
        with open(local, encoding="utf-8") as f:
            report = parse_report(f.read())
    except Exception as error:
        print(f"取回報告失敗：{error}", file=sys.stderr)
        report = {}

    return report, [evaluate_upgrade(report),
                    evaluate_uninstall_after_upgrade(report)]


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的字元
    # （實際踩過：客體讀錯編碼寫回一段亂碼）。少了這一行，一輪已經跑完的
    # 量測會在印出報告的那一步崩潰，結果完全拿不到。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    from tools import vms

    parser = argparse.ArgumentParser(
        description="驗證更新覆蓋安裝：裝了舊版之後再裝新版會發生什麼。")
    parser.add_argument("setup_v1", help="舊版的安裝檔")
    parser.add_argument("setup_v2", help="新版的安裝檔（同一個 app_name）")
    parser.add_argument("--app-name", default="MswiUpgradeProbe")
    parser.add_argument("--marker-v1", default="v1.txt")
    parser.add_argument("--marker-v2", default="v2.txt")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.setup_v1))
    os.makedirs(work_dir, exist_ok=True)

    vm = vms.connect(args.machine, profile=args.profile,
                     purpose="驗證更新覆蓋安裝")
    try:
        with vms.preserved_tab(vm.machine.vmx):
            vms.fresh_boot(vm)
            report, results = run(vm, args.setup_v1, args.setup_v2,
                                  args.app_name, args.marker_v1, args.marker_v2,
                                  work_dir)
            vm.stop()
    finally:
        vms.release(args.machine)

    print("=== 客體回報 ===")
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
