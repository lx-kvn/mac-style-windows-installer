"""驗證兩項只有官方文件、沒有實機確認的敘述（1809／組建 17763）。

對應 `docs/proposals/MSIX輸出規劃.md` 待辦第 1 項：

- **A：`MinVersion=10.0.17763.0` 真的能在組建 17763 完成部署。** 這連帶
  驗證「避開 `uap10:RuntimeBehavior`、改用
  `EntryPoint="windows.fullTrustApplication"`」這個已經寫進程式碼的決定
  ——`uap10:` 需要 2004，判斷若有誤會在 17763 上當場失敗。
- **B：企業版／教育版／LTSC 在 2004 之前預設關閉側載。** LTSC 2019 正是
  該情境本身。

**兩項的順序不可對調，這是本模組最重要的設計。** 若先在未開啟側載的環境
部署並失敗，無法分辨失敗來自「側載關著」（B 成立）還是「MinVersion 根本
不對」（A 不成立）——兩者的表現都是「裝不起來」。先開啟側載證明 A 成立，
再從乾淨快照測 B，B 的失敗才有唯一解釋。

**「部署失敗」本身不足以宣告 B 成立。** 部署會因為許多原因失敗（憑證不受
信任、套件損毀、磁碟空間不足）。B 因此除了要求部署被拒，還要求當下的側載
登錄值確實處於未設定狀態；兩者不同時成立時回報「無法判定」，不回報通過。

期望值（識別名稱、版本、發行者）從 `.msix` 自身的清單讀出，不寫死——寫死
的話比對的是「常數等於常數」，換一份套件或套件版本變動時，測試會在實際
不符的情況下仍然通過。

執行方式：

    python -m tools.verify_msix_1809 <已簽章的 Setup.exe> <對應的 .msix>

虛擬機的操作方式與已知陷阱見 `.claude/skills/run-test-vm`。
"""
import collections
import os
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import vms


PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"

Result = collections.namedtuple("Result", "name verdict detail")
Identity = collections.namedtuple("Identity", "name version publisher")

# 客體端的落腳處。不用 C:\Windows\Temp——那裡的檔案由工作階段 0 的提升權限
# 放入，桌面工作階段讀不到（見 skill 的 REFERENCE.md）。此處雖然都在工作
# 階段 0 執行，仍統一放使用者目錄，避免日後改成互動執行時再踩一次。
GUEST_DIR = r"C:\Users\Tester"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

_MANIFEST_NAME = "AppxManifest.xml"


class VerifyError(Exception):
    """驗證流程本身出錯（與「驗證結果為失敗」不同）。"""


def read_package_identity(package_path):
    """從 `.msix`（本質是 zip）的清單讀出 Identity 三個屬性。

    以 XML 解析而不是正規表示式：屬性順序不固定（本工具實際產出的順序是
    Name → Publisher → Version），綁死順序的表示式換一份清單就解析不到，
    而錯誤訊息會是「找不到 Identity 宣告」——看起來像套件壞了，不像是解析
    方式寫死了順序。
    """
    try:
        with zipfile.ZipFile(package_path) as archive:
            manifest = archive.read(_MANIFEST_NAME)
    except KeyError:
        raise VerifyError(
            package_path + " 裡沒有 " + _MANIFEST_NAME + "，不是有效的 MSIX 套件。")
    except zipfile.BadZipFile:
        raise VerifyError(package_path + " 不是有效的 zip／MSIX 檔案。")
    try:
        root = ElementTree.fromstring(manifest)
    except ElementTree.ParseError as error:
        raise VerifyError(package_path + " 的清單不是合法的 XML：" + str(error))
    for element in root.iter():
        # 清單有命名空間，比對去掉命名空間之後的標籤名。
        if element.tag.rsplit("}", 1)[-1] != "Identity":
            continue
        attrs = element.attrib
        missing = [k for k in ("Name", "Version", "Publisher") if k not in attrs]
        if missing:
            raise VerifyError(
                package_path + " 的 Identity 缺少屬性：" + "、".join(missing))
        return Identity(attrs["Name"], attrs["Version"], attrs["Publisher"])
    raise VerifyError(package_path + " 的清單裡找不到 Identity 宣告。")


def parse_report(text):
    """把客體寫回的 `key=value` 報告解析成字典。

    值本身可能含有等號（發行者字串就是 `C=TW, CN=...` 這種形式），因此只切
    第一個等號。
    """
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    return found


def evaluate_deployment(report, expected_version, expected_publisher):
    """A 的判準：四項全中才算通過。

    版本或發行者對不上時仍判為失敗，因為那代表系統接收到的不是這次送進去的
    那份套件——「有東西裝起來了」不等於「這次的部署成功」。
    """
    problems = []
    if report.get("found") != "True":
        problems.append("Get-AppxPackage 找不到該套件")
    if report.get("install_location_exists") != "True":
        problems.append("InstallLocation 不存在（登記存在但檔案未落地）")
    actual_version = report.get("version", "")
    if actual_version != expected_version:
        problems.append(
            "version 不符：預期 " + expected_version + "，實際 " + repr(actual_version))
    actual_publisher = report.get("publisher", "")
    if actual_publisher != expected_publisher:
        problems.append(
            "publisher 不符：預期 " + expected_publisher
            + "，實際 " + repr(actual_publisher))
    if problems:
        return Result("A：MinVersion 可在 17763 部署", FAIL, "；".join(problems))
    return Result("A：MinVersion 可在 17763 部署", PASS,
                  "套件已部署，版本與發行者皆與送入的套件一致。")


def evaluate_sideload_default(report):
    """B 的判準：部署被拒，且該次失敗可歸因於側載設定。

    側載登錄值若已有值，代表環境並非「預設狀態」，此時的失敗來自別的原因，
    不能拿來佐證這項敘述——回報無法判定，不回報通過。
    """
    name = "B：2004 之前的企業版預設關閉側載"
    policy = report.get("allow_all_trusted_apps", "")
    if policy:
        return Result(name, INCONCLUSIVE,
                      "環境不是預設狀態：AllowAllTrustedApps 已設為 "
                      + repr(policy) + "，本次失敗無法歸因於側載設定。")
    if report.get("deploy_succeeded") == "True":
        return Result(name, FAIL,
                      "未開啟側載仍部署成功，該敘述在此環境不成立。")
    return Result(name, PASS,
                  "側載登錄值未設定（系統預設）且部署被拒，敘述成立。")


_TRUST_CERT = r"""
Import-Certificate -FilePath '{cert}' -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Import-Certificate -FilePath '{cert}' -CertStoreLocation Cert:\LocalMachine\TrustedPeople | Out-Null
"""

_ENABLE_SIDELOAD = r"""
$key = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock'
if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
New-ItemProperty -Path $key -Name AllowAllTrustedApps -Value 1 -PropertyType DWord -Force | Out-Null
"""

_REPORT = r"""
$lines = @()
$p = Get-AppxPackage -Name '{identity}' -ErrorAction SilentlyContinue
if ($p) {{
    $lines += "found=True"
    $lines += "version=" + $p.Version
    $lines += "publisher=" + $p.Publisher
    $lines += "deploy_succeeded=True"
    $lines += "install_location_exists=" + (Test-Path $p.InstallLocation)
}} else {{
    $lines += "found=False"
    $lines += "deploy_succeeded=False"
    $lines += "install_location_exists=False"
}}
$v = $null
foreach ($k in @(
    'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Appx',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock')) {{
    if (Test-Path $k) {{
        $x = (Get-ItemProperty $k).AllowAllTrustedApps
        if ($null -ne $x) {{ $v = $x }}
    }}
}}
$lines += "allow_all_trusted_apps=" + $v
$lines | Set-Content '{out}' -Encoding UTF8
"""


def _run_guest_script(vm, workdir, name, body):
    """把一段 PowerShell 送進客體執行。編碼由 vms.write_guest_script 處理。"""
    local = os.path.join(workdir, name)
    vms.write_guest_script(local, body)
    guest = GUEST_DIR + "\\" + name
    vm.copy_in(local, guest)
    vm.run_program(POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", guest)


def _collect_report(vm, workdir, identity_name):
    """在客體端產生報告並取回。報告走檔案，因為 vmrun 不轉達客體的輸出。"""
    guest_out = GUEST_DIR + "\\report.txt"
    _run_guest_script(vm, workdir, "report.ps1",
                      _REPORT.format(identity=identity_name, out=guest_out))
    local_out = os.path.join(workdir, "report.txt")
    vm.copy_out(guest_out, local_out)
    with open(local_out, encoding="utf-8-sig") as handle:
        return parse_report(handle.read())


def _install_silently(vm, setup_exe, workdir):
    """送入 Setup.exe 並靜默安裝。

    check=False：這裡不把非零結束碼當成錯誤。B 預期它失敗，而 vmrun 對任何
    非零值一律回報 1，用它區分不了原因——成敗一律由事後的報告判定。
    """
    guest_exe = GUEST_DIR + "\\Setup.exe"
    vm.copy_in(setup_exe, guest_exe)
    vm.run_program(guest_exe, "/S", check=False)


def check_deploys(vm, package, workdir):
    """A：開啟側載，證明套件在組建 17763 上部署得起來。"""
    identity = read_package_identity(package["msix"])
    _run_guest_script(vm, workdir, "trust.ps1",
                      _TRUST_CERT.format(cert=package["guest_cert"]))
    _run_guest_script(vm, workdir, "sideload.ps1", _ENABLE_SIDELOAD)
    _install_silently(vm, package["setup"], workdir)
    report = _collect_report(vm, workdir, identity.name)
    return evaluate_deployment(report, identity.version, identity.publisher)


def check_sideload_default(vm, package, workdir):
    """B：不碰側載設定，看系統預設是否拒絕部署。

    憑證仍要裝——不裝的話部署會因為「憑證不受信任」而失敗，那個失敗歸不到
    側載設定上，整項只會得到「無法判定」。
    """
    identity = read_package_identity(package["msix"])
    _run_guest_script(vm, workdir, "trust.ps1",
                      _TRUST_CERT.format(cert=package["guest_cert"]))
    _install_silently(vm, package["setup"], workdir)
    report = _collect_report(vm, workdir, identity.name)
    return evaluate_sideload_default(report)


# 順序即設計，見模組說明。改動這個順序之前先讀完那段。
CHECKS = (check_deploys, check_sideload_default)


def run_all(vm, package, workdir, checks=CHECKS, boot=None):
    """依序執行各項檢查，每一項都從乾淨快照開始。

    每項之前都還原快照，因為第一項會開啟側載——不還原的話，第二項測到的是
    第一項留下的狀態，而那正好會讓第二項測出相反的結果。

    **第一項未通過時，其餘各項一律降級為「無法判定」。** 這是整個順序設計的
    另一半：後續項目的判準都建立在「部署本身是可行的」這個前提上，前提不成立
    時，它們測到的失敗可能與第一項是同一個原因。實際發生過——安裝檔缺少
    winrt 綁定導致 A 失敗，而 B 照樣依「側載值未設定且部署被拒」回報通過，
    但那個「被拒」與側載設定無關。原始判定保留在 detail 裡，供診斷用。

    單項失敗不中斷其餘項目：仍然跑完，因為原始判定本身有診斷價值。
    """
    boot = boot or vms.fresh_boot
    results = []
    for check in checks:
        boot(vm)
        result = check(vm, package, workdir)
        if results and results[0].verdict != PASS:
            result = result._replace(
                verdict=INCONCLUSIVE,
                detail="第一項未通過，本項的判準失去前提，結果不採計。"
                       "原始判定為 " + result.verdict.upper() + "：" + result.detail)
        results.append(result)
    return results


def export_signing_certificate(source_path, out_path, run=None, environ=None):
    """把簽章憑證從已簽章的檔案裡匯出成 `.cer`，供客體匯入信任存放區。

    用簽章本身帶的憑證，而不是另外指定一份 `.pfx`：兩者若不一致，客體會
    因為「憑證不受信任」而失敗，那個失敗與本次要驗的兩件事都無關。

    **PSModulePath 要明確指向 Windows PowerShell 自己的系統模組目錄。**
    這支工具可能從 PowerShell 7 啟動，而 7 的模組路徑不含 5.1 的系統模組
    目錄；繼承過去之後 5.1 載不到自己的 Microsoft.PowerShell.Security，
    `Get-AuthenticodeSignature` 回報「模組存在但無法載入」——訊息看起來像
    指令不存在，不會讓人聯想到環境變數。

    把該變數刪掉並不能解決：實測 5.1 不會因為變數不存在就退回預設值，照樣
    找不到模組。覆寫成單一路徑同時讓這一步的行為與呼叫端的殼無關。
    """
    run = run or subprocess.run
    environ = os.environ if environ is None else environ
    system_root = environ.get("SystemRoot", r"C:\Windows")
    child_env = dict(environ)
    child_env["PSModulePath"] = os.path.join(
        system_root, "system32", "WindowsPowerShell", "v1.0", "Modules")
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath '" + source_path + "'; "
        "if (-not $s.SignerCertificate) { exit 2 }; "
        "[IO.File]::WriteAllBytes('" + out_path + "', "
        "$s.SignerCertificate.Export('Cert'))"
    )
    result = run(["powershell", "-NoProfile", "-Command", script],
                 capture_output=True, text=True,
                 encoding="utf-8", errors="replace", env=child_env)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise VerifyError(
            "無法從 " + source_path + " 匯出簽章憑證："
            + (result.stderr or result.stdout or "").strip())
    return out_path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(__doc__)
        return 2
    setup_exe, msix = argv
    workdir = tempfile.mkdtemp(prefix="verify1809_")
    cert = export_signing_certificate(msix, os.path.join(workdir, "test.cer"))

    vm = vms.connect("win1809")
    guest_cert = GUEST_DIR + "\\test.cer"
    package = {"setup": setup_exe, "msix": msix, "guest_cert": guest_cert}

    with vms.preserved_tab(vm.machine.vmx):
        # 憑證每一輪都重新送入，不烘進快照：憑證若重新產生，烘進去的那份就
        # 過期了，而快照過期的症狀是「不明原因的部署失敗」。
        original_boot = vms.fresh_boot

        def boot(target):
            original_boot(target)
            target.copy_in(cert, guest_cert)

        results = run_all(vm, package, workdir, boot=boot)
        vm.stop()

    worst = PASS
    for result in results:
        print("[" + result.verdict.upper() + "] " + result.name)
        print("    " + result.detail)
        if result.verdict != PASS:
            worst = result.verdict
    return 0 if worst == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
