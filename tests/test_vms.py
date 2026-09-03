"""tools/vms.py 的測試：把「要對哪台虛擬機做什麼」翻譯成 vmrun 指令列。

實際執行 vmrun 需要裝好的虛擬機、快照，以及數十秒的還原與開機時間，測試
不做這件事——注入的替身只記錄指令，比照 builder.py 的 run 參數與
file_assoc.py 的 registry 參數。

這份測試存在的理由是幾個一旦寫錯、症狀都不會指向成因的地方：

- **密碼以 `-gp`／`-vp` 出現在指令列上。** 只要有人在錯誤訊息或診斷輸出裡
  帶上整串指令，密碼就會出現在終端機與記錄檔裡。這件事不會有任何徵兆。
- **送進客體的 .ps1 少了 UTF-8 BOM。** 客體端是 Windows PowerShell 5.1，
  讀無 BOM 的檔案時以 ANSI 解讀，中文被拆成無效 token，回報的是語法錯誤
  而不是編碼錯誤（實際踩過一次）。
- **加密的虛擬機少了 `-vp`。** Windows 11 需要虛擬 TPM，而 VMware 要求
  帶虛擬 TPM 的機器加密存放，因此連「列出快照」都會被擋。
"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fakes
from tools import vms


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRun:
    """記錄每一次 vmrun 呼叫，不實際執行。"""

    def __init__(self, results=None):
        self.calls = []
        self._results = list(results or [])

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if self._results:
            return self._results.pop(0)
        return FakeCompleted()

    @property
    def subcommands(self):
        """每次呼叫的 vmrun 子指令（如 revertToSnapshot、start）。"""
        found = []
        for cmd in self.calls:
            for token in cmd:
                if token in vms.SUBCOMMANDS:
                    found.append(token)
                    break
        return found


PLAIN = vms.Machine(
    key="plain",
    vmx=r"D:\VMware\X\X.vmx",
    snapshot="Clean",
    user="Tester",
    password_env="PLAIN_PW",
    encryption_env=None,
    profiles={"default": vms.Profile("default", "Clean", "Tester", "")},
)

ENCRYPTED = PLAIN._replace(
    key="encrypted",
    vmx=r"D:\VMware\Y\Y.vmx",
    password_env="ENC_PW",
    encryption_env="ENC_KEY",
)


def make_vm(run, log=None, password="p@ss", sleep=None,
            machine=PLAIN, encryption_password=None):
    return vms.Vm(
        machine,
        password,
        encryption_password=encryption_password,
        vmrun=r"C:\vmware\vmrun.exe",
        run=run,
        log=log,
        sleep=sleep or (lambda seconds: None),
    )


class MachineLookupTests(unittest.TestCase):
    def test_known_key_returns_the_machine(self):
        found = vms.machine("win1809")
        self.assertEqual(found.key, "win1809")
        self.assertTrue(found.vmx.lower().endswith(".vmx"))

    def test_unknown_key_lists_the_valid_ones(self):
        """打錯名字時要當場說出有哪些可選，不要讓人去翻原始碼。"""
        with self.assertRaises(vms.VmError) as caught:
            vms.machine("win98")
        message = str(caught.exception)
        for key in vms.MACHINES:
            self.assertIn(key, message)

    def test_the_windows_11_machine_declares_an_encryption_variable(self):
        """帶虛擬 TPM 的機器是加密的，少了這個宣告連列快照都會失敗。"""
        self.assertTrue(vms.machine("win11").encryption_env)

    def test_the_1809_machine_needs_no_encryption_variable(self):
        self.assertIsNone(vms.machine("win1809").encryption_env)


class ProfileTests(unittest.TestCase):
    """一台虛擬機有多張快照，各自代表不同的起始情境。

    情境的差別不只在快照名稱，還在「用哪個帳號登入」——標準使用者的快照裡
    登入的是 `User` 而不是 `Tester`。兩者綁在一起，分開記會出現「拿管理員
    帳號去登入標準使用者快照」這種對不起來的組合，而失敗訊息會是認證失敗，
    不會指向情境選錯。
    """

    def test_win11_declares_the_four_snapshots(self):
        found = vms.machine("win11").profiles
        for key in ("default", "two_disks", "standard_user",
                    "standard_user_two_disks"):
            self.assertIn(key, found)

    def test_a_profile_carries_both_the_snapshot_and_the_account(self):
        profile = vms.machine("win11").profiles["standard_user"]
        self.assertEqual(profile.snapshot, "Clean_User")
        self.assertEqual(profile.user, "User")

    def test_the_default_profile_is_the_original_clean_snapshot(self):
        machine = vms.machine("win11")
        self.assertEqual(machine.profiles["default"].snapshot, "Clean")
        self.assertEqual(machine.snapshot, "Clean")
        self.assertEqual(machine.user, "Tester")

    def test_win1809_has_only_the_default_profile(self):
        self.assertEqual(list(vms.machine("win1809").profiles), ["default"])

    def test_connect_selects_the_named_profile(self):
        run = FakeRun()
        vm = vms.connect("win11", profile="standard_user_two_disks",
                         environ={"WIN11_VM_PASSWORD": "p",
                                  "WIN11_VM_ENCRYPTION_PASSWORD": "k"},
                         run=run, vmrun=r"C:\vmware\vmrun.exe")
        vm.revert()
        self.assertIn("Clean_User_C:/E:", run.calls[0])
        vm.copy_in("a", "b")
        self.assertIn("User", run.calls[1])
        self.assertNotIn("Tester", run.calls[1])

    def test_unknown_profile_lists_the_valid_ones(self):
        with self.assertRaises(vms.VmError) as caught:
            vms.connect("win11", profile="nope",
                        environ={"WIN11_VM_PASSWORD": "p",
                                 "WIN11_VM_ENCRYPTION_PASSWORD": "k"})
        message = str(caught.exception)
        self.assertIn("standard_user", message)
        self.assertIn("nope", message)


class PasswordTests(unittest.TestCase):
    def test_reads_the_named_variable(self):
        self.assertEqual(vms.password_from_env("PLAIN_PW", {"PLAIN_PW": "s3cret"}),
                         "s3cret")

    def test_missing_variable_names_itself_in_the_error(self):
        with self.assertRaises(vms.VmError) as caught:
            vms.password_from_env("PLAIN_PW", {})
        self.assertIn("PLAIN_PW", str(caught.exception))

    def test_empty_variable_counts_as_missing(self):
        """空字串若放行，失敗會出現在 vmrun 的登入階段，訊息指向錯的地方。"""
        with self.assertRaises(vms.VmError):
            vms.password_from_env("PLAIN_PW", {"PLAIN_PW": ""})


class ConnectTests(unittest.TestCase):
    def test_supplies_both_passwords_for_an_encrypted_machine(self):
        run = FakeRun()
        vm = vms.connect(ENCRYPTED, environ={"ENC_PW": "guest", "ENC_KEY": "disk"},
                         run=run, vmrun=r"C:\vmware\vmrun.exe")
        vm.revert()
        cmd = run.calls[0]
        self.assertIn("-vp", cmd)
        self.assertIn("disk", cmd)

    def test_does_not_require_an_encryption_variable_when_none_is_declared(self):
        """未加密的機器不該因為少設一個不相干的環境變數而無法使用。"""
        run = FakeRun()
        vm = vms.connect(PLAIN, environ={"PLAIN_PW": "guest"},
                         run=run, vmrun=r"C:\vmware\vmrun.exe")
        vm.revert()
        self.assertNotIn("-vp", run.calls[0])

    def test_accepts_a_machine_key_as_well_as_a_machine(self):
        run = FakeRun()
        vms.connect("win1809", environ={"WIN1809_VM_PASSWORD": "guest"},
                    run=run, vmrun=r"C:\vmware\vmrun.exe").revert()
        self.assertIn(vms.machine("win1809").vmx, run.calls[0])


class WriteGuestScriptTests(unittest.TestCase):
    """送進客體的腳本必須帶 UTF-8 BOM。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "job.ps1")

    def test_file_starts_with_utf8_bom(self):
        vms.write_guest_script(self.path, "Write-Output 'hi'\n")
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(3), b"\xef\xbb\xbf")

    def test_chinese_content_survives_round_trip(self):
        vms.write_guest_script(self.path, "# 側載預設值\n")
        with io.open(self.path, encoding="utf-8-sig") as handle:
            self.assertIn("側載預設值", handle.read())


class CommandAssemblyTests(unittest.TestCase):
    def test_revert_uses_the_machines_own_snapshot(self):
        run = FakeRun()
        make_vm(run).revert()
        self.assertEqual(run.subcommands, ["revertToSnapshot"])
        self.assertIn("Clean", run.calls[0])

    def test_host_side_commands_carry_no_guest_credentials(self):
        """還原與開機不進客體，帶上帳密只會讓密碼多曝光一次。"""
        run = FakeRun()
        vm = make_vm(run)
        vm.revert()
        vm.start()
        vm.stop()
        for cmd in run.calls:
            self.assertNotIn("-gu", cmd)
            self.assertNotIn("-gp", cmd)

    def test_encryption_password_goes_on_every_command(self):
        """加密的機器連列快照都要帶，不只客體操作。"""
        run = FakeRun()
        vm = make_vm(run, machine=ENCRYPTED, encryption_password="disk")
        vm.revert()
        vm.start()
        for cmd in run.calls:
            self.assertIn("-vp", cmd)
            self.assertNotIn("-gu", cmd)

    def test_start_is_headless_by_default(self):
        run = FakeRun()
        make_vm(run).start()
        self.assertIn("nogui", run.calls[0])

    def test_start_can_open_a_visible_console(self):
        """要親眼看虛擬機在做什麼時用得到，也是截圖驗證畫面的前提。"""
        run = FakeRun()
        make_vm(run).start(gui=True)
        self.assertIn("gui", run.calls[0])
        self.assertNotIn("nogui", run.calls[0])

    def test_guest_commands_carry_credentials(self):
        run = FakeRun()
        vm = make_vm(run)
        vm.copy_in("host.ps1", r"C:\Windows\Temp\job.ps1")
        vm.copy_out(r"C:\Windows\Temp\out.txt", "out.txt")
        vm.run_program(r"C:\powershell.exe", "-File", r"C:\Windows\Temp\job.ps1")
        for cmd in run.calls:
            self.assertIn("-gu", cmd)
            self.assertIn("Tester", cmd)
            self.assertIn("-gp", cmd)

    def test_run_program_puts_arguments_after_the_program(self):
        run = FakeRun()
        make_vm(run).run_program(r"C:\powershell.exe", "-File", "job.ps1")
        cmd = run.calls[0]
        self.assertLess(cmd.index(r"C:\powershell.exe"), cmd.index("-File"))
        self.assertLess(cmd.index("-File"), cmd.index("job.ps1"))

    def test_run_program_runs_outside_the_desktop_by_default(self):
        """預設落在工作階段 0（服務階段），畫面上看不到。"""
        run = FakeRun()
        make_vm(run).run_program(r"C:\x.exe")
        self.assertNotIn("-interactive", run.calls[0])

    def test_run_program_can_target_the_interactive_desktop(self):
        """要在使用者看得到的桌面上跑（例如安裝精靈）就得加這個旗標。

        實測：不加時客體回報工作階段 0，加了是工作階段 1。旗標必須排在
        程式路徑之前，那是 vmrun 接受的位置。
        """
        run = FakeRun()
        make_vm(run).run_program(r"C:\x.exe", interactive=True)
        cmd = run.calls[0]
        self.assertIn("-interactive", cmd)
        self.assertLess(cmd.index("-interactive"), cmd.index(r"C:\x.exe"))

    def test_run_program_can_return_failure_without_raising(self):
        """有些情境預期客體程式失敗（例如驗證側載預設是關的），那不是錯誤。"""
        run = FakeRun([FakeCompleted(returncode=1, stderr="denied")])
        result = make_vm(run).run_program(r"C:\x.exe", check=False)
        self.assertEqual(result.returncode, 1)

    def test_capture_screen_is_a_guest_operation(self):
        """檔案寫在主機端，VMware 仍歸類為客體操作，不帶帳密會被拒絕。"""
        run = FakeRun()
        make_vm(run).capture_screen("shot.png")
        self.assertIn("-gu", run.calls[0])
        self.assertIn("shot.png", run.calls[0])


class SecretRedactionTests(unittest.TestCase):
    def test_failure_message_omits_both_passwords(self):
        run = FakeRun([FakeCompleted(returncode=1, stderr="Error: boom")])
        vm = make_vm(run, password="guestpw", machine=ENCRYPTED,
                     encryption_password="diskpw")
        with self.assertRaises(vms.VmError) as caught:
            vm.copy_in("a", "b")
        message = str(caught.exception)
        self.assertNotIn("guestpw", message)
        self.assertNotIn("diskpw", message)
        self.assertIn("boom", message)

    def test_log_redacts_both_passwords(self):
        lines = []
        vm = make_vm(FakeRun(), log=lines.append, password="guestpw",
                     machine=ENCRYPTED, encryption_password="diskpw")
        vm.copy_in("a", "b")
        joined = "\n".join(lines)
        self.assertNotIn("guestpw", joined)
        self.assertNotIn("diskpw", joined)
        self.assertIn("***", joined)


PREFERENCES_SAMPLE = """\
pref.ws.session.window.count = "1"
pref.ws.session.window0.tab.count = "3"
pref.ws.session.window0.tab0.file = ""
pref.ws.session.window0.tab0.type = "home"
pref.ws.session.window0.tab1.file = "C:\\VMs\\Win11\\Win11.vmx"
pref.ws.session.window0.tab1.type = "vm"
pref.ws.session.window0.tab2.file = "D:\\VMware\\X\\X.vmx"
pref.ws.session.window0.tab2.type = "vm"
"""


class OpenTabsTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "preferences.ini")
        with io.open(self.path, "w", encoding="utf-8") as handle:
            handle.write(PREFERENCES_SAMPLE)

    def test_lists_the_virtual_machines_with_an_open_tab(self):
        self.assertEqual(
            vms.open_tabs(self.path),
            [r"C:\VMs\Win11\Win11.vmx", r"D:\VMware\X\X.vmx"],
        )

    def test_ignores_the_home_tab(self):
        """首頁分頁的 file 是空字串，不是一台虛擬機。"""
        self.assertNotIn("", vms.open_tabs(self.path))

    def test_missing_file_means_no_open_tabs(self):
        """Workstation 沒開過時設定檔可能不存在，那不是錯誤。"""
        self.assertEqual(vms.open_tabs(self.path + ".nope"), [])


class PreservedTabTests(unittest.TestCase):
    """無畫面執行會讓 Workstation 收掉該虛擬機的分頁，用完把它補回來。

    只補「原本就開著」的分頁。使用者沒開的時候什麼都不做，因為補分頁會把
    Workstation 的視窗叫到最前面（實測 brave -> vmware），對沒在看虛擬機
    的人來說那是無故的打斷。
    """

    def test_reopens_a_tab_that_disappeared(self):
        tabs = [[r"D:\VMware\X\X.vmx"], []]
        reopened = []
        with vms.preserved_tab(r"D:\VMware\X\X.vmx",
                               list_tabs=lambda: tabs.pop(0),
                               reopen=reopened.append):
            pass
        self.assertEqual(reopened, [r"D:\VMware\X\X.vmx"])

    def test_leaves_things_alone_when_the_tab_was_never_open(self):
        tabs = [[], []]
        reopened = []
        with vms.preserved_tab(r"D:\VMware\X\X.vmx",
                               list_tabs=lambda: tabs.pop(0),
                               reopen=reopened.append):
            pass
        self.assertEqual(reopened, [])

    def test_does_not_reopen_a_tab_that_survived(self):
        tabs = [[r"D:\VMware\X\X.vmx"], [r"D:\VMware\X\X.vmx"]]
        reopened = []
        with vms.preserved_tab(r"D:\VMware\X\X.vmx",
                               list_tabs=lambda: tabs.pop(0),
                               reopen=reopened.append):
            pass
        self.assertEqual(reopened, [])

    def test_path_comparison_ignores_case(self):
        """Workstation 寫回設定檔的大小寫不保證與呼叫端給的一致。"""
        tabs = [[r"d:\vmware\x\X.VMX"], []]
        reopened = []
        with vms.preserved_tab(r"D:\VMware\X\x.vmx",
                               list_tabs=lambda: tabs.pop(0),
                               reopen=reopened.append):
            pass
        self.assertEqual(len(reopened), 1)

    def test_restores_even_when_the_body_raises(self):
        """驗證失敗時更需要把畫面還原成使用者交出去時的樣子。"""
        tabs = [[r"D:\VMware\X\X.vmx"], []]
        reopened = []
        with self.assertRaises(ValueError):
            with vms.preserved_tab(r"D:\VMware\X\X.vmx",
                                   list_tabs=lambda: tabs.pop(0),
                                   reopen=reopened.append):
                raise ValueError("boom")
        self.assertEqual(len(reopened), 1)


class ReadinessTests(unittest.TestCase):
    """開機完成不等於客體已就緒，中間要等。

    就緒與否**不以 checkToolsState 的字串判斷**：實測它回報 `installed`
    時，客體其實已經在正常桌面、`runProgramInGuest` 結束碼為 0。同一台
    虛擬機在不同時候回過 `running` 與 `installed` 兩種值，拿它當條件會
    在客體明明可用時空等到逾時。改為直接試一個最便宜的客體指令。
    """

    def test_polls_until_the_guest_accepts_a_command(self):
        run = FakeRun([FakeCompleted(returncode=1), FakeCompleted(returncode=0)])
        slept = []
        make_vm(run, sleep=slept.append).wait_until_ready()
        self.assertEqual(
            run.subcommands, ["runProgramInGuest", "runProgramInGuest"])
        self.assertEqual(len(slept), 1)

    def test_the_probe_failing_is_not_an_error(self):
        """探測失敗只代表還沒好，不是工具壞了——不該丟例外。"""
        run = FakeRun([FakeCompleted(returncode=1), FakeCompleted(returncode=0)])
        make_vm(run).wait_until_ready()  # 不應丟出例外

    def test_gives_up_with_a_clear_error(self):
        run = FakeRun([FakeCompleted(returncode=1)] * 50)
        with self.assertRaises(vms.VmError) as caught:
            make_vm(run, sleep=lambda seconds: None).wait_until_ready(attempts=3)
        self.assertIn("就緒", str(caught.exception))


class FreshBootTests(unittest.TestCase):
    def test_passes_the_display_mode_through(self):
        """模式在 fresh_boot 就要定下來——開機之後再改得先關機重開。"""
        run = FakeRun()
        vms.fresh_boot(make_vm(run), gui=True)
        start_call = run.calls[1]
        self.assertIn("gui", start_call)
        self.assertNotIn("nogui", start_call)

    def test_reverts_starts_then_waits(self):
        """殘留狀態上跑出來的結果不算數，還原必須排在開機之前。

        另一個理由：略過還原直接開機是從硬碟冷開機，客體停在鎖定畫面，
        沒有互動登入，-interactive 會被拒絕。
        """
        run = FakeRun()
        vms.fresh_boot(make_vm(run))
        self.assertEqual(
            run.subcommands,
            ["revertToSnapshot", "start", "runProgramInGuest"],
        )


class SubprocessOutputDecodingTest(unittest.TestCase):
    """子行程輸出的解碼方式（見 tests/_fakes.py 的解碼探針說明）。

    這些測試真的起一個子行程，讓它輸出一段在系統地區編碼下無法解碼的位元組，
    再檢查受測函式最後拿到什麼——驗證的是「輸出有沒有被完整取回」，不是實作
    傳了哪些參數。
    """

    def test_the_failure_message_carries_what_vmrun_printed(self):
        script = _fakes.decode_probe_script(
            ascii_text="Error: The virtual machine is not powered on",
            exit_code=1, stream="stderr")
        with self.assertRaises(vms.VmError) as ctx:
            make_vm(_fakes.decode_probe_run(script)).start()
        self.assertIn("not powered on", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
