"""在真實機器上驗證 MSIX 的全機器使用者範圍（ADR-0013 第三段的驗收）。

單元測試把系統那一端全部換成替身，因此有四件事只有在真的機器上才問得到：

1. **`provision_package_for_all_users_async` 這條路走不走得通**。ADR-0013
   背景第一項只確認了綁定套件「暴露」這幾個方法，沒有確認我們串起來的
   順序（stage → 查家族名稱 → 佈建）在系統那一端會被接受。
2. **雜湊那一關擋不擋得住**。那個內部旗標任何人都能打，而它會以提權身分
   佈建參數指定的那一份套件。
3. **靜默安裝未提權時會不會降級**，以及降級的說明有沒有真的寫進 `/LOG=`
   ——那是無人值守情境下這件事唯一的出口。
4. **靜默安裝已提權時會不會直接佈建**（第六題）。這一輪同時量到一件既有
   的事，見下方。

**這一輪量得到、也量不到的**：能驗到「佈建紀錄確實建立了、當前使用者拿得
到」；「別的帳號登入之後會不會拿到」驗不完整——`Clean` 快照上啟用中的本機
帳號只有 `Tester` 一個，要驗那件事得在測試過程中另建帳號，而新建的帳號與
快照裡原有的未必相同（設定檔、群組）。該限制記在 ADR-0013 的待辦。

**UAC 那個視窗不在這支工具的範圍內**：安全桌面不接受合成輸入，`SendInput`
按不到它。這裡改為分別驗證它兩側的東西——提權之後子行程做的事（第一輪），
以及取不到權限時的降級（第三輪）。

## 既有的一件事，順帶量到

提權的行程替當前使用者註冊會被系統拒（ADR-0013 背景第五項，
`0x80070005`）。因此以管理員身分執行 MSIX 安裝檔的靜默安裝，整個安裝的
結束碼是非零的——佈建成功，替執行者自己註冊那一步失敗。這在全機器範圍
出現之前就是如此，不是這個功能造成的，因此照實記錄、不併進判定。

判準與客體腳本的性質由 `tests/test_verify_msix_all_users.py` 釘住；驅動
虛擬機的部分不進測試，因為真的跑一輪要好幾分鐘。
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import packaging_core

# `tools.vms` 不在最上層匯入：它要求 `vm_lease`，而那個套件由另一個 repo
# 提供、只裝在開發機上。放在最上層的話，這個模組在 CI 上連匯入都會失敗，
# 判準與客體腳本那幾十項測試因此一項都跑不到——而那些正是「拿到客體回報之後
# 怎麼判定」的全部保障，在乾淨機器上驗過才有意義。
#
# 這一點與 `measure_msix_scope.py` 等幾支不同：那幾支的測試以
# `unittest.SkipTest` 在 CI 上整份跳過。此處改為延後匯入，讓判準真的被 CI
# 驗到；驅動虛擬機的那幾個函式仍然只在開發機上跑得起來。

PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"

Result = collections.namedtuple("Result", "name verdict detail")

POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# 提權的行程寫進 C:\Windows\Temp 的檔案，未提權的桌面工作階段讀不到（實際
# 踩過，症狀是客體回報 0xFFFD0000，看起來像「沒有輸出」）。一輪裡兩種權限
# 都要讀寫同一份報告，因此放公共目錄。
GUEST_DIR = r"C:\Users\Public"
REPORT = GUEST_DIR + r"\all_users_report.txt"
CHILD_REPORT = GUEST_DIR + r"\child_report.txt"
SILENT_LOG = GUEST_DIR + r"\silent_install.log"

# 子行程回報「套件與這次安裝內嵌的那一份不符」用的結束碼
# （`msix_provision.EXIT_VERIFY_FAILED`）。這裡寫死而不匯入那個模組：
# 這支工具驗的是**編出來的那顆 exe**，而它裡面的常數是編譯當下的那一份。
# 匯入產品程式碼會讓「兩邊一致」變成無條件成立，那樣就沒有驗到東西
# （這個專案出過的事故形態：測試斷言模組自己的常數）。
EXPECTED_VERIFY_FAILED_EXIT = "11"

_WRONG_DIGEST = "0" * 64


def parse_report(text):
    """把客體寫回的 `key=value` 報告解析成字典。

    只切第一個等號：值本身含有等號是常態（狀態字串、命令列）。開頭的 BOM
    要剝掉——Windows PowerShell 5.1 建立檔案時會寫入一個，不剝的話第一個鍵
    會變成 `\ufeff...`，查表只會查不到，看起來像客體沒有回報那一步。
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


def evaluate_child_provisions(report):
    """第一輪：那個內部旗標啟動的子行程真的把套件登記給了整台機器嗎。"""
    name = "提權的子行程完成佈建"
    absent = _missing(report, ("good_before", "good_exit", "good_after"))
    if absent:
        return Result(name, INCONCLUSIVE, f"客體沒有回報：{', '.join(absent)}")
    if "provisioned" in report["good_before"]:
        # 機器上已經有佈建紀錄時，「事後有紀錄」證明不了是這一次留下的。
        return Result(name, INCONCLUSIVE,
                      f"前置狀態不乾淨（{report['good_before']}）")
    if report["good_exit"] != "0":
        return Result(name, FAIL,
                      f"結束碼 {report['good_exit']}："
                      + (report.get("good_report") or "子行程沒有留下原因"))
    if "provisioned:" not in report["good_after"]:
        return Result(name, FAIL,
                      "回報成功，但機器上查不到佈建紀錄"
                      f"（事後狀態：{report['good_after']}）")
    return Result(name, PASS, f"事後狀態：{report['good_after']}")


def evaluate_digest_guard(report):
    """第一輪的另一半：雜湊不符的套件會被拒絕。"""
    name = "雜湊不符即拒絕佈建"
    absent = _missing(report, ("bad_before", "bad_exit", "bad_after"))
    if absent:
        return Result(name, INCONCLUSIVE, f"客體沒有回報：{', '.join(absent)}")
    if "provisioned" in report["bad_after"]:
        return Result(name, FAIL,
                      "雜湊不符卻仍然佈建了"
                      f"（事後狀態：{report['bad_after']}）")
    if report["bad_exit"] != EXPECTED_VERIFY_FAILED_EXIT:
        # 拒絕了，但理由不是雜湊——例如根本沒讀到檔案。那樣的話這一關沒有
        # 被驗到，而結果看起來一樣。
        return Result(name, FAIL,
                      f"拒絕的結束碼是 {report['bad_exit']}，"
                      f"不是雜湊不符的 {EXPECTED_VERIFY_FAILED_EXIT}")
    return Result(name, PASS, "以雜湊不符的結束碼拒絕，且沒有留下佈建紀錄")


def evaluate_silent_downgrade(report):
    """第二輪：靜默安裝未提權時降級、寫進紀錄、結束碼仍是 0。"""
    name = "靜默安裝未提權時降級為當前使用者範圍"
    absent = _missing(report, ("silent_exit", "silent_state", "silent_provisioned",
                               "silent_log"))
    if absent:
        return Result(name, INCONCLUSIVE, f"客體沒有回報：{', '.join(absent)}")
    if report["silent_exit"] != "0":
        return Result(name, FAIL,
                      f"結束碼是 {report['silent_exit']}——降級之後的安裝是"
                      "成功且可用的，非零會讓部署腳本把它當成失敗")
    if "user:" not in report["silent_state"]:
        return Result(name, FAIL,
                      f"應用程式沒有安裝給執行的使用者（{report['silent_state']}）")
    if "provisioned" in report["silent_provisioned"]:
        return Result(name, FAIL, "未提權卻留下了佈建紀錄——權限判斷有誤")
    if "只安裝給目前這位使用者" not in report["silent_log"]:
        return Result(name, FAIL,
                      "紀錄檔沒有說明降級這件事——無人值守時它是唯一的出口")
    return Result(name, PASS, "降級、寫進紀錄、結束碼 0")


def evaluate_silent_elevated(report):
    """第三輪：靜默安裝已提權時直接佈建，不跳 UAC。"""
    name = "靜默安裝已提權時直接佈建"
    absent = _missing(report, ("elevated_exit", "elevated_provisioned"))
    if absent:
        return Result(name, INCONCLUSIVE, f"客體沒有回報：{', '.join(absent)}")
    if "provisioned:" not in report["elevated_provisioned"]:
        return Result(name, FAIL,
                      f"沒有留下佈建紀錄（{report['elevated_provisioned']}）")
    # 結束碼照實記錄但不併進判定：提權的行程替當前使用者註冊會被系統拒
    # （ADR-0013 背景第五項），那在這個功能出現之前就是如此。
    return Result(name, PASS,
                  f"佈建紀錄：{report['elevated_provisioned']}；"
                  f"整個安裝的結束碼 {report['elevated_exit']}"
                  "（提權的行程無法替當前使用者註冊，見背景第五項）")


def _preamble(identity, with_provisioned):
    """兩件每支腳本都要有的事：往報告檔追加，以及取樣目前的狀態。

    追加而不覆寫：一輪裡有提權與未提權兩支腳本各自寫同一份報告，覆寫的話
    後跑的那一支會把前一支的結果清掉，而缺步驟的判定是「無法判定」——看起來
    像機器有問題，不像腳本互相覆蓋。
    """
    provisioned = ("""
  $prov = Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue |
          Where-Object { $_.DisplayName -eq '%s' }
  if ($prov) { $parts += "provisioned:$($prov.Version)" }""" % identity
                   if with_provisioned else "")
    return """$ErrorActionPreference = 'Continue'

function Note($line) {
  $line | Out-File -FilePath '%s' -Append -Encoding UTF8
}

function State {
  $parts = @()
  $pkg = Get-AppxPackage -Name '%s' -ErrorAction SilentlyContinue
  if ($pkg) { $parts += "user:$($pkg.Version):$($pkg.Status)" }%s
  if ($parts.Count -eq 0) { 'none' } else { $parts -join '+' }
}

function Flatten($path) {
  # -Encoding UTF8 是必要的：安裝檔把紀錄與報告都寫成 UTF-8，而 Windows
  # PowerShell 5.1 的 Get-Content 預設以系統的 ANSI 字碼頁解碼（這台機器是
  # cp950）。少了它讀回來的是一整段亂碼，而判定要找的是一句中文——一次其實
  # 正確的行為會被判成失敗，理由與產品無關（2026-09-08 實際踩到）。
  if (Test-Path $path) { ((Get-Content $path -Raw -Encoding UTF8) -replace "`r?`n", ' | ') }
  else { '' }
}
""" % (REPORT, identity, provisioned)


def child_round_script(setup_exe, package_path, digest, identity):
    """第一輪（提權）：先用錯的雜湊跑一次，再用對的跑一次。

    **順序不能顛倒**。反過來的話，比對不符那一次是在「已經有佈建紀錄」的
    機器上跑的，而它的判準正是「事後沒有佈建紀錄」——那個判準會無條件通過。
    上一輪量測踩過同一種陷阱：腳本順序讓失敗回的是另一個錯誤碼。
    """
    return _preamble(identity, with_provisioned=True) + """
$setup = '%(setup)s'
$package = '%(package)s'

Note ("bad_before=" + (State))
$p = Start-Process -FilePath $setup -ArgumentList @(
  "/PROVISION=$package", "/PROVISION-HASH=%(wrong)s") -Wait -PassThru
Note ("bad_exit=" + $p.ExitCode)
Note ("bad_after=" + (State))

Remove-Item '%(child_report)s' -ErrorAction SilentlyContinue
Note ("good_before=" + (State))
$p = Start-Process -FilePath $setup -ArgumentList @(
  "/PROVISION=$package", "/PROVISION-HASH=%(digest)s",
  "/PROVISION-REPORT=%(child_report)s") -Wait -PassThru
Note ("good_exit=" + $p.ExitCode)
Note ("good_after=" + (State))
Note ("good_report=" + (Flatten '%(child_report)s'))
""" % {"setup": setup_exe, "package": package_path, "digest": digest,
       "wrong": _WRONG_DIGEST, "child_report": CHILD_REPORT}


def silent_round_script(setup_exe, identity, log_path):
    """第二輪（未提權）：靜默安裝，看它降不降級。

    這裡不問佈建紀錄——`Get-AppxProvisionedPackage -Online` 需要管理員權限，
    而這一輪整個重點就是它跑在未提權的工作階段裡。在這裡問只會拿到一個
    看起來像「沒有佈建」的錯誤，與真正的答案難以區分。改由
    `silent_check_script()` 在提權的工作階段回答。
    """
    return _preamble(identity, with_provisioned=False) + """
$setup = '%(setup)s'
Remove-Item '%(log)s' -ErrorAction SilentlyContinue

Note ("silent_before=" + (State))
$p = Start-Process -FilePath $setup -ArgumentList @('/S', "/LOG=%(log)s") -Wait -PassThru
Note ("silent_exit=" + $p.ExitCode)
Note ("silent_state=" + (State))
Note ("silent_log=" + (Flatten '%(log)s'))
""" % {"setup": setup_exe, "log": log_path}


def silent_check_script(identity):
    """第二輪的提權補問：未提權那一次有沒有留下佈建紀錄。"""
    return _preamble(identity, with_provisioned=True) + """
$parts = @()
$prov = Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq '%(identity)s' }
if ($prov) { $parts += "provisioned:$($prov.Version)" }
if ($parts.Count -eq 0) { Note ("silent_provisioned=none") }
else { Note ("silent_provisioned=" + ($parts -join '+')) }
""" % {"identity": identity}


def elevated_round_script(setup_exe, identity, log_path):
    """第三輪（提權）：靜默安裝，行程本身已提權時應該直接佈建。"""
    return _preamble(identity, with_provisioned=True) + """
$setup = '%(setup)s'
Remove-Item '%(log)s' -ErrorAction SilentlyContinue

Note ("elevated_before=" + (State))
$p = Start-Process -FilePath $setup -ArgumentList @('/S', "/LOG=%(log)s") -Wait -PassThru
Note ("elevated_exit=" + $p.ExitCode)
Note ("elevated_provisioned=" + (State))
Note ("elevated_log=" + (Flatten '%(log)s'))
""" % {"setup": setup_exe, "log": log_path}


def _push(vm, work_dir, name, source):
    from tools import vms

    local = os.path.join(work_dir, name)
    vms.write_guest_script(local, source)
    remote = GUEST_DIR + "\\" + name
    vm.copy_in(local, remote)
    return remote


def _collect(vm, work_dir, suffix):
    local = os.path.join(work_dir, f"all_users_{suffix}.txt")
    try:
        vm.copy_out(REPORT, local)
        with open(local, encoding="utf-8") as f:
            return parse_report(f.read())
    except Exception as error:
        print(f"取回 {suffix} 那一輪的報告失敗：{error}", file=sys.stderr)
        return {}


def run(vm, setup_local, package_local, digest, identity, work_dir, prepare=None):
    """三輪，每輪之間還原快照。

    佈建紀錄無法從系統介面移除，帶著上一輪的紀錄量下一輪會量到另一件事
    （`measure_msix_scope.py` 已經因此吃過一次無法判定）。
    """
    report = {}

    def send_materials():
        remote_setup = GUEST_DIR + "\\" + os.path.basename(setup_local)
        remote_package = GUEST_DIR + "\\" + os.path.basename(package_local)
        vm.copy_in(setup_local, remote_setup)
        vm.copy_in(package_local, remote_package)
        return remote_setup, remote_package

    remote_setup, remote_package = send_materials()
    script = _push(vm, work_dir, "all_users_child.ps1",
                   child_round_script(remote_setup, remote_package, digest, identity))
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", script, check=False)
    report.update(_collect(vm, work_dir, "child"))

    if prepare:
        prepare()
        remote_setup, _remote_package = send_materials()

    script = _push(vm, work_dir, "all_users_silent.ps1",
                   silent_round_script(remote_setup, identity, SILENT_LOG))
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", script, interactive=True, check=False)
    script = _push(vm, work_dir, "all_users_silent_check.ps1",
                   silent_check_script(identity))
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", script, check=False)
    report.update(_collect(vm, work_dir, "silent"))

    if prepare:
        prepare()
        remote_setup, _remote_package = send_materials()

    script = _push(vm, work_dir, "all_users_elevated.ps1",
                   elevated_round_script(remote_setup, identity, SILENT_LOG))
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", script, check=False)
    report.update(_collect(vm, work_dir, "elevated"))

    return report, [evaluate_child_provisions(report),
                    evaluate_digest_guard(report),
                    evaluate_silent_downgrade(report),
                    evaluate_silent_elevated(report)]


def _trust_certificate(vm, cer_local, work_dir):
    """讓客體信任這張自簽憑證。未受信任的套件無法部署，這是前置不是量測。"""
    remote = GUEST_DIR + "\\" + os.path.basename(cer_local)
    vm.copy_in(cer_local, remote)
    script = _push(vm, work_dir, "trust_cert.ps1", """$ErrorActionPreference = 'Stop'
Import-Certificate -FilePath '%(cer)s' -CertStoreLocation Cert:\\LocalMachine\\TrustedPeople | Out-Null
Import-Certificate -FilePath '%(cer)s' -CertStoreLocation Cert:\\LocalMachine\\Root | Out-Null
""" % {"cer": remote})
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", script)


def main(argv=None):
    import argparse
    import hashlib

    from tools import vms

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的
    # 字元（實際踩過：客體讀錯編碼寫回一段亂碼）。少了這一行，一輪已經
    # 跑完的量測會在印出報告的那一步崩潰，結果完全拿不到。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    parser = argparse.ArgumentParser(
        description="驗證 MSIX 全機器使用者範圍的安裝端（ADR-0013 第三段）。")
    parser.add_argument("setup", help="all_users 打開的 Setup exe")
    parser.add_argument("package", help="同一次建置產出的已簽章 .msix")
    parser.add_argument("--identity", default="MswiProbe.ScopeProbe")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--cer", default=None,
                        help="測試憑證（.cer），未受信任的套件無法部署")
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.setup))
    os.makedirs(work_dir, exist_ok=True)

    with open(args.package, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()

    vm = vms.connect(args.machine, profile=args.profile,
                     purpose="驗證 MSIX 全機器範圍的安裝端（ADR-0013）")

    def prepare():
        vms.fresh_boot(vm)
        if args.cer:
            _trust_certificate(vm, args.cer, work_dir)

    try:
        with vms.preserved_tab(vm.machine.vmx):
            prepare()
            report, results = run(vm, args.setup, args.package, digest,
                                  args.identity, work_dir, prepare=prepare)
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
