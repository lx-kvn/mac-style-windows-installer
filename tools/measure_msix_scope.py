"""量測 MSIX 的全機器使用者範圍：ADR-0013 待辦的兩項行為。

`ADR-0013` 決定全機器範圍以 `msix.all_users` 啟用，但兩項行為在作成該決定的
那一輪量測中沒有結論，因此列為「實作前必須先驗證」：

1. **決定六的「同範圍直接更新」**——已知限制第四項：於已有使用者註冊的狀態下
   重新佈建曾失敗、亦曾成功，差異未查明。
2. **背景第五項的根因**——提權的行程替當前使用者註冊會以 `0x80070005` 失敗，
   當時只確認了現象。決定三（先提權佈建、再降回未提權註冊）就是為了繞過它。

## 為什麼分成好幾支客體腳本

佈建（`Provision-AppxPackage` 那條路）需要系統管理員權限，而替當前使用者
註冊在提權狀態下會失敗——那正是要量的事情之一。兩者因此不可能在同一個行程
裡完成：提權的部分由 `run_program()`（背景、已提升）執行，未提權的部分由
`run_program(interactive=True)`（使用者桌面、未提升）執行。

## 為什麼每一步都要記錄前置狀態

上一輪的結果之所以無法採信，是因為同一個情境兩次得到不同結果，而當時沒有
記錄「做這件事之前系統處於什麼狀態」。因此這裡每個階段都連同 `before=` 一起
寫回，缺了就判為無法判定，不猜。

判準與腳本的性質由 `tests/test_measure_msix_scope.py` 釘住。
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import vms


HOLDS = "holds"                  # 待驗的敘述成立
BROKEN = "broken"                # 待驗的敘述不成立
NOT_REPRODUCED = "not-reproduced"  # 現象沒有重現
INCONCLUSIVE = "inconclusive"    # 量不到，與「量到失敗」不同

Verdict = collections.namedtuple("Verdict", "name verdict detail")

POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# 提權的行程寫進 C:\Windows\Temp 的檔案，未提權的桌面工作階段讀不到（實際
# 踩過，症狀是客體回報 0xFFFD0000，看起來像「沒有輸出」）。兩種權限都要
# 讀寫同一份報告，因此放公共目錄。
GUEST_DIR = r"C:\Users\Public"
REPORT = GUEST_DIR + r"\msix_scope_report.txt"
IDENTITY = "MswiProbe.ScopeProbe"


def parse_report(text):
    """把客體寫回的報告解析成一連串階段紀錄。

    格式是每個階段數行 `key=value`，以單獨一行 `--` 結束。值可能含等號
    （錯誤訊息裡就有），因此只切第一個。最後一個階段沒有結束標記時照樣回傳
    ——客體在那一步掛掉時，前面量到的東西不該一起消失。
    """
    steps = []
    current = {}
    for line in text.splitlines():
        line = line.strip()
        if line == "--":
            if current:
                steps.append(current)
                current = {}
            continue
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        current[key.strip()] = value.strip()
    if current:
        steps.append(current)
    return steps


def _by_name(steps):
    return {step.get("step"): step for step in steps}


def evaluate_same_scope_update(steps):
    """決定六：既有安裝與本次安裝的使用者範圍相同時，直接部署即可。

    兩種範圍各量一次。任一邊被拒絕，決定六就不成立——那代表「同範圍」也必須
    先移除，而決定六正是以「不必」為前提寫的。
    """
    name = "決定六：同範圍直接更新"
    found = _by_name(steps)
    required = ("user_update_same_scope", "provision_update_same_scope")
    missing = [key for key in required if key not in found]
    if missing:
        return Verdict(name, INCONCLUSIVE, "客體沒有回報：" + "、".join(missing))

    refused = [key for key in required if found[key].get("result") != "ok"]
    if refused:
        detail = "；".join(
            f"{key} 被拒（前置狀態 {found[key].get('before', '未記錄')}）："
            f"{found[key].get('detail', '')}" for key in refused)
        return Verdict(name, BROKEN, detail)
    return Verdict(name, HOLDS,
                   "兩種範圍的同範圍更新皆直接成功，不需要先移除。")


def evaluate_elevated_registration(steps):
    """背景第五項：提權會使「替當前使用者註冊」失敗，以及那是為什麼。

    只知道失敗不算完成——待辦要的是根因，因此另外要求部署紀錄裡取得了系統
    給的說明。同一份套件在未提權時必須成功，否則失敗的原因可能與權限無關
    （憑證不受信任、套件損毀都會失敗），那樣的結果無從歸因。
    """
    name = "背景第五項：提權導致註冊失敗"
    found = _by_name(steps)
    required = ("register_elevated", "register_unelevated", "appx_log_elevated")
    missing = [key for key in required if key not in found]
    if missing:
        return Verdict(name, INCONCLUSIVE, "客體沒有回報：" + "、".join(missing))

    if found["register_unelevated"].get("result") != "ok":
        return Verdict(name, INCONCLUSIVE,
                       "未提權的註冊也失敗了，因此提權那次的失敗無法歸因於權限："
                       + found["register_unelevated"].get("detail", ""))

    if found["register_elevated"].get("result") == "ok":
        return Verdict(name, NOT_REPRODUCED,
                       "提權的行程這次註冊成功，現象沒有重現。決定三的形狀是"
                       "為了繞過這個現象而設計的，需重新檢視。")

    reason = found["appx_log_elevated"].get("detail", "")
    if not reason:
        return Verdict(name, INCONCLUSIVE,
                       "失敗重現了，但部署紀錄沒有帶回原因——待辦要的是根因，"
                       "只有錯誤碼等於這一項沒有做。")
    return Verdict(name, HOLDS,
                   "提權註冊失敗（" + found["register_elevated"].get("detail", "")
                   + "），部署紀錄的說明：" + reason)


def _preamble():
    """每支腳本共用的開頭：報告以附加方式寫，中途卡住至少留得下卡在哪一步。"""
    return f"""$ErrorActionPreference = 'Continue'
$report = '{REPORT}'
$identity = '{IDENTITY}'

function State() {{
    # 這一步開始之前，系統處於什麼狀態。上一輪的結果之所以無法採信，就是
    # 缺了這一項。
    $user = Get-AppxPackage -Name $identity -ErrorAction SilentlyContinue
    $prov = Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue |
        Where-Object {{ $_.DisplayName -eq $identity }}
    $parts = @()
    if ($user) {{ $parts += "user:$($user.Version):$($user.Status)" }}
    if ($prov) {{ $parts += "provisioned:$($prov.Version)" }}
    if ($parts.Count -eq 0) {{ return 'none' }}
    return ($parts -join '+')
}}

function Note($step, $before, $result, $detail) {{
    Add-Content -Path $report -Value "step=$step" -Encoding UTF8
    Add-Content -Path $report -Value "before=$before" -Encoding UTF8
    Add-Content -Path $report -Value "result=$result" -Encoding UTF8
    $flat = ("$detail" -replace "`r?`n", ' ').Trim()
    if ($flat.Length -gt 500) {{ $flat = $flat.Substring(0, 500) }}
    Add-Content -Path $report -Value "detail=$flat" -Encoding UTF8
    Add-Content -Path $report -Value '--' -Encoding UTF8
}}
"""


def elevated_script(package_v1, package_v2):
    """提權的部分：佈建、同範圍再佈建、以及提權下的註冊嘗試。"""
    return _preamble() + f"""
# 這支由背景執行，行程已提升。

# 乾淨狀態下佈建一次，作為後續各步的基準。
$before = State
try {{
    Add-AppxProvisionedPackage -Online -PackagePath '{package_v1}' -SkipLicense `
        -ErrorAction Stop | Out-Null
    Note 'provision_clean' $before 'ok' ''
}} catch {{
    Note 'provision_clean' $before 'fail' $_.Exception.Message
}}

# 同範圍（皆為佈建）的版本更替：決定六說這裡直接部署即可。
$before = State
try {{
    Add-AppxProvisionedPackage -Online -PackagePath '{package_v2}' -SkipLicense `
        -ErrorAction Stop | Out-Null
    Note 'provision_update_same_scope' $before 'ok' ''
}} catch {{
    Note 'provision_update_same_scope' $before 'fail' $_.Exception.Message
}}

# 提權狀態下替當前使用者註冊——背景第五項說這裡會失敗。
$before = State
try {{
    Add-AppxPackage -Path '{package_v1}' -ErrorAction Stop
    Note 'register_elevated' $before 'ok' ''
}} catch {{
    Note 'register_elevated' $before 'fail' $_.Exception.Message
}}

# 系統給的訊息是一句概括的話，原因在部署紀錄裡。
$before = 'n/a'
try {{
    $log = Get-AppxLog -All -ErrorAction Stop |
        Where-Object {{ $_.Message -match '{IDENTITY}' -or $_.Message -match '0x80070005' }} |
        Select-Object -Last 6 | ForEach-Object {{ $_.Message }}
    Note 'appx_log_elevated' $before 'ok' ($log -join ' | ')
}} catch {{
    Note 'appx_log_elevated' $before 'fail' $_.Exception.Message
}}
"""


def unelevated_script(package_v1, package_v2):
    """未提權的部分：替當前使用者註冊，以及同範圍的版本更替。"""
    return _preamble() + f"""
# 這支由使用者桌面執行，行程未提升。

$before = State
try {{
    Add-AppxPackage -Path '{package_v1}' -ErrorAction Stop
    Note 'register_unelevated' $before 'ok' ''
}} catch {{
    Note 'register_unelevated' $before 'fail' $_.Exception.Message
}}

# 同範圍（皆為當前使用者）的版本更替。
$before = State
try {{
    Add-AppxPackage -Path '{package_v2}' -ErrorAction Stop
    Note 'user_update_same_scope' $before 'ok' ''
}} catch {{
    Note 'user_update_same_scope' $before 'fail' $_.Exception.Message
}}

# 上一輪出現不一致的那個情境：已有使用者註冊時再佈建一次。這一步在未提權
# 下必然失敗（佈建需要提權），量的是它失敗的形態與提權那次是否不同。
$before = State
try {{
    Add-AppxProvisionedPackage -Online -PackagePath '{package_v1}' -SkipLicense `
        -ErrorAction Stop | Out-Null
    Note 'provision_after_user_register_unelevated' $before 'ok' ''
}} catch {{
    Note 'provision_after_user_register_unelevated' $before 'fail' $_.Exception.Message
}}
"""


def all_scripts(package_v1, package_v2):
    return [elevated_script(package_v1, package_v2),
            unelevated_script(package_v1, package_v2)]


def run(vm, package_v1_local, package_v2_local, work_dir):
    """把兩顆套件送進客體，依序跑提權與未提權兩支腳本，取回報告。"""
    remote_v1 = GUEST_DIR + "\\" + os.path.basename(package_v1_local)
    remote_v2 = GUEST_DIR + "\\" + os.path.basename(package_v2_local)
    vm.copy_in(package_v1_local, remote_v1)
    vm.copy_in(package_v2_local, remote_v2)

    scripts = [
        ("elevated", elevated_script(remote_v1, remote_v2), False),
        ("unelevated", unelevated_script(remote_v1, remote_v2), True),
    ]
    for name, source, interactive in scripts:
        local = os.path.join(work_dir, f"msix_scope_{name}.ps1")
        vms.write_guest_script(local, source)
        remote = GUEST_DIR + "\\" + os.path.basename(local)
        vm.copy_in(local, remote)
        vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", remote, interactive=interactive, check=False)

    local_report = os.path.join(work_dir, "msix_scope_report.txt")
    text = ""
    try:
        vm.copy_out(REPORT, local_report)
        with open(local_report, encoding="utf-8") as f:
            text = f.read()
    except Exception as error:
        print("取回報告失敗：" + str(error), file=sys.stderr)

    steps = parse_report(text)
    return steps, [evaluate_same_scope_update(steps),
                   evaluate_elevated_registration(steps)]


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="量測 ADR-0013 待辦的兩項行為（全機器範圍）。")
    parser.add_argument("package_v1")
    parser.add_argument("package_v2")
    parser.add_argument("--machine", default="win11")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--cer", default=None,
                        help="測試憑證（.cer），未受信任的套件無法部署")
    args = parser.parse_args(argv)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.package_v1))
    os.makedirs(work_dir, exist_ok=True)

    vm = vms.connect(args.machine, profile=args.profile,
                     purpose="量測 MSIX 全機器範圍（ADR-0013 待辦）")
    try:
        with vms.preserved_tab(vm.machine.vmx):
            vms.fresh_boot(vm)
            if args.cer:
                _trust_certificate(vm, args.cer, work_dir)
            steps, verdicts = run(vm, args.package_v1, args.package_v2, work_dir)
            vm.stop()
    finally:
        vms.release(args.machine)

    print("=== 逐步結果 ===")
    for step in steps:
        print(f"[{step.get('result', '?'):>4}] {step.get('step')}"
              f"（前置：{step.get('before', '未記錄')}）"
              + (f" {step.get('detail')}" if step.get("detail") else ""))
    print()
    print("=== 判定 ===")
    worst = HOLDS
    for verdict in verdicts:
        print(f"[{verdict.verdict}] {verdict.name}：{verdict.detail}")
        if verdict.verdict != HOLDS:
            worst = verdict.verdict
    return 0 if worst == HOLDS else 1


def _trust_certificate(vm, cer_local, work_dir):
    """讓客體信任這張自簽憑證。未受信任的套件無法部署，這是前置不是量測。"""
    remote = GUEST_DIR + "\\" + os.path.basename(cer_local)
    vm.copy_in(cer_local, remote)
    script = os.path.join(work_dir, "trust_cert.ps1")
    vms.write_guest_script(script, f"""$ErrorActionPreference = 'Stop'
Import-Certificate -FilePath '{remote}' -CertStoreLocation Cert:\\LocalMachine\\TrustedPeople | Out-Null
Import-Certificate -FilePath '{remote}' -CertStoreLocation Cert:\\LocalMachine\\Root | Out-Null
""")
    remote_script = GUEST_DIR + "\\" + os.path.basename(script)
    vm.copy_in(script, remote_script)
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", remote_script)


if __name__ == "__main__":
    sys.exit(main())
