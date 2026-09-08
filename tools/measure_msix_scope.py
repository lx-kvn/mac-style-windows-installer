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

import packaging_core

from tools import vms


HOLDS = "holds"                  # 待驗的敘述成立
BROKEN = "broken"                # 待驗的敘述不成立
NOT_REPRODUCED = "not-reproduced"  # 現象沒有重現
INCONCLUSIVE = "inconclusive"    # 量不到，與「量到失敗」不同

# 跨範圍的兩種可能結局。這兩者決定「先移除」那道步驟該不該做，而那道步驟
# 走的是會清掉使用者資料的系統動作（ADR-0015），因此必須量準，不能推論。
COEXISTS = "coexists"                    # 新舊各自存在——決定六的「先移除」有必要
SYSTEM_HANDLES_IT = "system-handles-it"  # 系統自己收斂——那道移除是白清資料

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
    # 客體端以 Windows PowerShell 建立這個檔案時會寫入位元組順序標記，第一行
    # 因此是 `﻿step=...`，鍵會變成 `﻿step`。真實抓到的後果是每一輪
    # 的第一筆紀錄「沒有名字」，判準據此回報「客體沒有回報那一步」——量到了
    # 卻看起來像沒量到。
    text = text.lstrip("﻿")
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
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


def evaluate_cross_scope(steps):
    """跨範圍：使用者自己裝過之後，管理員再佈建同一個套件會怎樣。

    決定六以「不先移除就會新舊並存」為前提，而那道移除會清掉使用者的資料
    （ADR-0015）。因此這裡要分辨的是：佈建之後，使用者那一份是停在舊版本
    （並存，移除有必要），還是被系統帶到新版本（系統自己收斂，移除是白清）。
    """
    name = "跨範圍：使用者範圍已安裝時再佈建"
    found = _by_name(steps)
    required = ("user_registered_before", "provision_over_user_scope",
                "state_after_cross_scope")
    missing = [key for key in required if key not in found]
    if missing:
        return Verdict(name, INCONCLUSIVE, "客體沒有回報：" + "、".join(missing))

    if found["provision_over_user_scope"].get("result") != "ok":
        return Verdict(name, INCONCLUSIVE,
                       "佈建本身失敗，並存與否沒有被測到："
                       + found["provision_over_user_scope"].get("detail", ""))

    before = found["user_registered_before"].get("detail", "")
    after = found["state_after_cross_scope"].get("detail", "")
    before_user = _user_version(before)
    after_user = _user_version(after)

    if not after_user:
        return Verdict(name, SYSTEM_HANDLES_IT,
                       "佈建之後使用者那一份已不存在（" + after + "），沒有並存。")
    if before_user and after_user != before_user:
        return Verdict(name, SYSTEM_HANDLES_IT,
                       f"佈建把使用者那一份從 {before_user} 帶到 {after_user}，"
                       "沒有並存——此時先移除等於白清一次資料。")
    return Verdict(name, COEXISTS,
                   f"佈建之後使用者那一份仍是 {after_user}（{after}），"
                   "新舊各自存在。")


def _user_version(state):
    """從 `user:1.0.0.0:Ok+provisioned:1.1.0.0` 這種狀態字串取出使用者那一份的版本。"""
    for part in (state or "").split("+"):
        if part.startswith("user:"):
            pieces = part.split(":")
            return pieces[1] if len(pieces) > 1 else ""
    return ""


def cross_scope_round_scripts(package_v1, package_v2):
    """第三輪：使用者自己裝 1.0.0，管理員再佈建 1.1.0。

    順序不可對調：要量的是「使用者已經有一份」這個前置狀態下佈建的結果。
    """
    unelevated_first = _preamble() + f"""
# 使用者自己裝一份（未提權，這是終端使用者實際會做的事）。
$before = State
try {{
    Add-AppxPackage -Path '{package_v1}' -ErrorAction Stop
    Note 'user_installs_first' $before 'ok' ''
}} catch {{
    Note 'user_installs_first' $before 'fail' $_.Exception.Message
}}
Note 'user_registered_before' 'n/a' 'ok' (State)
"""

    elevated = _preamble() + f"""
# 管理員佈建同一個套件的較新版本（跨範圍）。
$before = State
try {{
    Add-AppxProvisionedPackage -Online -PackagePath '{package_v2}' -SkipLicense `
        -ErrorAction Stop | Out-Null
    Note 'provision_over_user_scope' $before 'ok' ''
}} catch {{
    Note 'provision_over_user_scope' $before 'fail' $_.Exception.Message
}}

# 第三段的驗收要以另一個帳號登入，前提是那個帳號存在於這張快照裡。
try {{
    $names = (Get-LocalUser | Where-Object {{ $_.Enabled }} |
        ForEach-Object {{ $_.Name }}) -join ','
    Note 'local_users' 'n/a' 'ok' $names
}} catch {{
    Note 'local_users' 'n/a' 'fail' $_.Exception.Message
}}
"""

    unelevated_after = _preamble() + """
# 佈建之後，使用者那一份還在不在、是哪一個版本。
Note 'state_after_cross_scope' 'n/a' 'ok' (State)
"""
    return [unelevated_first, elevated, unelevated_after]


def registration_round_scripts(package_v1):
    """第一輪：只做註冊，從乾淨狀態開始。

    **這一輪不能碰佈建。** 真實踩過：第一版把佈建排在提權註冊之前，等到要測
    註冊時機器上已經有較新的版本，那次失敗回的是 `0x80073D06`（版本較舊）
    而不是 `0x80070005`（存取被拒）——量到的是另一件事，而且看起來像量到了。
    """
    elevated = _preamble() + f"""
# 提權（背景工作階段）。背景第五項說這裡會失敗。
$before = State
try {{
    Add-AppxPackage -Path '{package_v1}' -ErrorAction Stop
    Note 'register_elevated' $before 'ok' ''
}} catch {{
    Note 'register_elevated' $before 'fail' $_.Exception.Message
}}

# 系統給的訊息是一句概括的話，原因在部署紀錄裡——待辦要的正是那個原因。
$before = 'n/a'
try {{
    $log = Get-AppxLog -All -ErrorAction Stop |
        Where-Object {{ $_.Message -match '{IDENTITY}' -or $_.Message -match '0x80070005' }} |
        Select-Object -Last 8 | ForEach-Object {{ $_.Message }}
    Note 'appx_log_elevated' $before 'ok' ($log -join ' | ')
}} catch {{
    Note 'appx_log_elevated' $before 'fail' $_.Exception.Message
}}
"""

    unelevated = _preamble() + f"""
# 未提權（使用者桌面）。同一份套件、同一個帳號，只有權限不同——這是提權那次
# 的失敗能否歸因於權限的對照組。
$before = State
try {{
    Add-AppxPackage -Path '{package_v1}' -ErrorAction Stop
    Note 'register_unelevated' $before 'ok' ''
}} catch {{
    Note 'register_unelevated' $before 'fail' $_.Exception.Message
}}
"""
    return [elevated, unelevated]


def provision_round_scripts(package_v1, package_v2):
    """第二輪：佈建與同範圍的版本更替。這一輪會把機器弄髒（佈建紀錄無法從
    系統介面移除），因此排在註冊那一輪之後，中間還原快照。"""
    elevated = _preamble() + f"""
# 乾淨狀態下佈建一次，作為後續各步的基準。
$before = State
try {{
    Add-AppxProvisionedPackage -Online -PackagePath '{package_v1}' -SkipLicense `
        -ErrorAction Stop | Out-Null
    Note 'provision_clean' $before 'ok' ''
}} catch {{
    Note 'provision_clean' $before 'fail' $_.Exception.Message
}}

# 佈建之後，執行佈建的那個使用者自己拿到了什麼——ADR-0013 背景第二項說
# 「佈建不等於替當前使用者安裝」，這一步記錄當下的事實。
Note 'after_provision_state' (State) 'ok' ''

# 同範圍（皆為佈建）的版本更替：決定六說這裡直接部署即可。
$before = State
try {{
    Add-AppxProvisionedPackage -Online -PackagePath '{package_v2}' -SkipLicense `
        -ErrorAction Stop | Out-Null
    Note 'provision_update_same_scope' $before 'ok' ''
}} catch {{
    Note 'provision_update_same_scope' $before 'fail' $_.Exception.Message
}}
"""

    unelevated = _preamble() + f"""
# 同範圍（皆為當前使用者）的版本更替。用較新的那一顆：使用者這一端此時
# 可能已經有 1.0.0（見 after_provision_state），拿舊的去裝會因版本而被拒，
# 那不是這一步要量的東西。
$before = State
try {{
    Add-AppxPackage -Path '{package_v2}' -ErrorAction Stop
    Note 'user_update_same_scope' $before 'ok' ''
}} catch {{
    Note 'user_update_same_scope' $before 'fail' $_.Exception.Message
}}

# 已有使用者註冊時再佈建一次（上一輪出現不一致的那個情境）。未提權下必然
# 失敗，量的是它失敗的形態與提權那次是否不同。
$before = State
try {{
    Add-AppxProvisionedPackage -Online -PackagePath '{package_v1}' -SkipLicense `
        -ErrorAction Stop | Out-Null
    Note 'provision_after_user_register_unelevated' $before 'ok' ''
}} catch {{
    Note 'provision_after_user_register_unelevated' $before 'fail' $_.Exception.Message
}}
"""
    return [elevated, unelevated]


def all_scripts(package_v1, package_v2):
    return registration_round_scripts(package_v1) \
        + provision_round_scripts(package_v1, package_v2) \
        + cross_scope_round_scripts(package_v1, package_v2)


def _run_round(vm, name, scripts, work_dir, report_suffix):
    """跑一輪（提權一支、未提權一支），把那一輪的報告取回來。"""
    for index, (source, interactive) in enumerate(scripts):
        local = os.path.join(work_dir, f"msix_scope_{name}_{index}.ps1")
        vms.write_guest_script(local, source)
        remote = GUEST_DIR + "\\" + os.path.basename(local)
        vm.copy_in(local, remote)
        vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", remote, interactive=interactive, check=False)

    local_report = os.path.join(work_dir, f"msix_scope_{report_suffix}.txt")
    try:
        vm.copy_out(REPORT, local_report)
        with open(local_report, encoding="utf-8") as f:
            return parse_report(f.read())
    except Exception as error:
        print(f"取回 {name} 那一輪的報告失敗：{error}", file=sys.stderr)
        return []


def run(vm, package_v1_local, package_v2_local, work_dir, prepare=None):
    """兩輪，中間還原快照。

    註冊那一輪必須從乾淨狀態開始（見 `registration_round_scripts()`），而佈建
    那一輪會把機器弄髒且佈建紀錄無法從系統介面移除，因此兩輪不能共用一次開機。
    `prepare` 由呼叫端提供：還原快照、開機、重新建立信任等前置。
    """
    def stage_packages():
        remote_v1 = GUEST_DIR + "\\" + os.path.basename(package_v1_local)
        remote_v2 = GUEST_DIR + "\\" + os.path.basename(package_v2_local)
        vm.copy_in(package_v1_local, remote_v1)
        vm.copy_in(package_v2_local, remote_v2)
        return remote_v1, remote_v2

    remote_v1, remote_v2 = stage_packages()
    steps = _run_round(
        vm, "registration",
        [(registration_round_scripts(remote_v1)[0], False),
         (registration_round_scripts(remote_v1)[1], True)],
        work_dir, "registration")

    if prepare:
        prepare()
        remote_v1, remote_v2 = stage_packages()

    provision_scripts = provision_round_scripts(remote_v1, remote_v2)
    steps += _run_round(
        vm, "provision",
        [(provision_scripts[0], False), (provision_scripts[1], True)],
        work_dir, "provision")

    if prepare:
        prepare()
        remote_v1, remote_v2 = stage_packages()

    # 第三輪同樣從乾淨狀態開始：前兩輪都留下了佈建紀錄，而那個紀錄無法從
    # 系統介面移除，帶著它量「使用者已裝過時再佈建」會量到另一件事。
    cross_scripts = cross_scope_round_scripts(remote_v1, remote_v2)
    steps += _run_round(
        vm, "cross_scope",
        [(cross_scripts[0], True), (cross_scripts[1], False), (cross_scripts[2], True)],
        work_dir, "cross_scope")

    return steps, [evaluate_same_scope_update(steps),
                   evaluate_elevated_registration(steps),
                   evaluate_cross_scope(steps)]


def main(argv=None):
    import argparse

    # 客體寫回來的文字不受這支工具控制，其中可能含有主控台編不出來的
    # 字元（實際踩過：客體讀錯編碼寫回一段亂碼）。少了這一行，一輪已經
    # 跑完的量測會在印出報告的那一步崩潰，結果完全拿不到。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

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

    def prepare():
        """兩輪之間：還原快照、開機、重新建立信任。

        佈建紀錄無法從系統介面移除，因此兩輪之間一定要還原，不能只是把套件
        移除了事。
        """
        vms.fresh_boot(vm)
        if args.cer:
            _trust_certificate(vm, args.cer, work_dir)

    try:
        with vms.preserved_tab(vm.machine.vmx):
            prepare()
            steps, verdicts = run(vm, args.package_v1, args.package_v2, work_dir,
                                  prepare=prepare)
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
