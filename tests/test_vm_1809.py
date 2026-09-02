"""tools/vm_1809.py 的測試：把「要對虛擬機做什麼」翻譯成 vmrun 指令列。

實際執行 vmrun 需要一台裝好的虛擬機、一張快照，以及數十秒的還原與開機
時間，測試不做這件事——注入的替身只記錄指令，比照 builder.py 的 run 參數
與 file_assoc.py 的 registry 參數。

這份測試存在的理由是兩個一旦寫錯、症狀都不會指向成因的地方：

- **密碼以 `-gp` 出現在指令列上。** 只要有人在錯誤訊息或診斷輸出裡帶上
  整串指令，密碼就會出現在終端機、log 檔與往後可能接上的 CI 記錄裡。
  這件事不會有任何徵兆，要靠測試釘住。
- **送進客體的 .ps1 少了 UTF-8 BOM。** 客體端是 Windows PowerShell 5.1，
  讀無 BOM 的檔案時以 ANSI 解讀，中文字元被拆成無效 token，回報的是語法
  錯誤而不是編碼錯誤（實際踩過一次，見 .claude/skills/run-1809-vm）。
"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import vm_1809


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
                if token in vm_1809.SUBCOMMANDS:
                    found.append(token)
                    break
        return found


def make_vm(run, log=None, password="p@ss", sleep=None):
    return vm_1809.Vm(
        vmx=r"D:\VMware\X\X.vmx",
        user="Tester",
        password=password,
        vmrun=r"C:\vmware\vmrun.exe",
        run=run,
        log=log,
        sleep=sleep or (lambda seconds: None),
    )


class WriteGuestScriptTests(unittest.TestCase):
    """送進客體的腳本必須帶 UTF-8 BOM。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "job.ps1")

    def test_file_starts_with_utf8_bom(self):
        vm_1809.write_guest_script(self.path, "Write-Output 'hi'\n")
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(3), b"\xef\xbb\xbf")

    def test_chinese_content_survives_round_trip(self):
        vm_1809.write_guest_script(self.path, "# 側載預設值\n")
        with io.open(self.path, encoding="utf-8-sig") as handle:
            self.assertIn("側載預設值", handle.read())


class CommandAssemblyTests(unittest.TestCase):
    def test_revert_names_the_snapshot(self):
        run = FakeRun()
        make_vm(run).revert("Clean")
        self.assertEqual(run.subcommands, ["revertToSnapshot"])
        self.assertIn("Clean", run.calls[0])

    def test_host_side_commands_carry_no_guest_credentials(self):
        """還原與開機不進客體，帶上帳密只會讓密碼多曝光一次。"""
        run = FakeRun()
        vm = make_vm(run)
        vm.revert("Clean")
        vm.start()
        vm.stop()
        for cmd in run.calls:
            self.assertNotIn("-gu", cmd)
            self.assertNotIn("-gp", cmd)

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

    def test_run_program_can_return_failure_without_raising(self):
        """有些情境預期客體程式失敗（例如驗證側載預設是關的），那不是錯誤。"""
        run = FakeRun([FakeCompleted(returncode=1, stderr="denied")])
        result = make_vm(run).run_program(r"C:\x.exe", check=False)
        self.assertEqual(result.returncode, 1)


class PasswordHandlingTests(unittest.TestCase):
    def test_password_read_from_named_environment_variable(self):
        found = vm_1809.password_from_env({vm_1809.PASSWORD_ENV: "s3cret"})
        self.assertEqual(found, "s3cret")

    def test_missing_environment_variable_raises(self):
        with self.assertRaises(vm_1809.VmError) as caught:
            vm_1809.password_from_env({})
        self.assertIn(vm_1809.PASSWORD_ENV, str(caught.exception))

    def test_empty_environment_variable_raises(self):
        """空字串當成「沒設定」，否則會拿空密碼去登入、錯在別的地方。"""
        with self.assertRaises(vm_1809.VmError):
            vm_1809.password_from_env({vm_1809.PASSWORD_ENV: ""})

    def test_failure_message_omits_the_password(self):
        run = FakeRun([FakeCompleted(returncode=1, stderr="Error: boom")])
        vm = make_vm(run, password="s3cret")
        with self.assertRaises(vm_1809.VmError) as caught:
            vm.copy_in("a", "b")
        message = str(caught.exception)
        self.assertNotIn("s3cret", message)
        self.assertIn("boom", message)

    def test_log_redacts_the_password(self):
        lines = []
        vm = make_vm(FakeRun(), log=lines.append, password="s3cret")
        vm.copy_in("a", "b")
        self.assertTrue(lines)
        joined = "\n".join(lines)
        self.assertNotIn("s3cret", joined)
        self.assertIn("***", joined)


class ToolsReadinessTests(unittest.TestCase):
    """開機完成不等於客體已就緒，中間要等 VMware Tools 起來。

    `vmrun start` 回來時客體才剛開始開機，此時送檔案或執行程式會失敗，
    而失敗訊息（找不到檔案／登入失敗）不會指向「開太快」這個成因。
    """

    def test_polls_until_tools_report_running(self):
        run = FakeRun([
            FakeCompleted(stdout="starting\n"),
            FakeCompleted(stdout="running\n"),
        ])
        slept = []
        make_vm(run, sleep=slept.append).wait_for_tools()
        self.assertEqual(run.subcommands, ["checkToolsState", "checkToolsState"])
        self.assertEqual(len(slept), 1)

    def test_gives_up_with_a_clear_error(self):
        run = FakeRun([FakeCompleted(stdout="starting\n")] * 50)
        with self.assertRaises(vm_1809.VmError) as caught:
            make_vm(run, sleep=lambda seconds: None).wait_for_tools(attempts=3)
        self.assertIn("VMware Tools", str(caught.exception))


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
            vm_1809.open_tabs(self.path),
            [r"C:\VMs\Win11\Win11.vmx", r"D:\VMware\X\X.vmx"],
        )

    def test_ignores_the_home_tab(self):
        """首頁分頁的 file 是空字串，不是一台虛擬機。"""
        self.assertNotIn("", vm_1809.open_tabs(self.path))

    def test_missing_file_means_no_open_tabs(self):
        """Workstation 沒開過時設定檔可能不存在，那不是錯誤。"""
        self.assertEqual(vm_1809.open_tabs(self.path + ".nope"), [])


class PreservedTabTests(unittest.TestCase):
    """無畫面執行會讓 Workstation 收掉該虛擬機的分頁，用完把它補回來。

    只補「原本就開著」的分頁。使用者沒開的時候什麼都不做，因為補分頁會把
    Workstation 的視窗叫到最前面（實測 brave -> vmware），對沒在看虛擬機
    的人來說那是無故的打斷。
    """

    def test_reopens_a_tab_that_disappeared(self):
        tabs = [[r"D:\VMware\X\X.vmx"], []]
        reopened = []
        with vm_1809.preserved_tab(r"D:\VMware\X\X.vmx",
                                   list_tabs=lambda: tabs.pop(0),
                                   reopen=reopened.append):
            pass
        self.assertEqual(reopened, [r"D:\VMware\X\X.vmx"])

    def test_leaves_things_alone_when_the_tab_was_never_open(self):
        tabs = [[], []]
        reopened = []
        with vm_1809.preserved_tab(r"D:\VMware\X\X.vmx",
                                   list_tabs=lambda: tabs.pop(0),
                                   reopen=reopened.append):
            pass
        self.assertEqual(reopened, [])

    def test_does_not_reopen_a_tab_that_survived(self):
        tabs = [[r"D:\VMware\X\X.vmx"], [r"D:\VMware\X\X.vmx"]]
        reopened = []
        with vm_1809.preserved_tab(r"D:\VMware\X\X.vmx",
                                   list_tabs=lambda: tabs.pop(0),
                                   reopen=reopened.append):
            pass
        self.assertEqual(reopened, [])

    def test_path_comparison_ignores_case(self):
        """Workstation 寫回設定檔的大小寫不保證與呼叫端給的一致。"""
        tabs = [[r"d:\vmware\x\X.VMX"], []]
        reopened = []
        with vm_1809.preserved_tab(r"D:\VMware\X\x.vmx",
                                   list_tabs=lambda: tabs.pop(0),
                                   reopen=reopened.append):
            pass
        self.assertEqual(len(reopened), 1)

    def test_restores_even_when_the_body_raises(self):
        """驗證失敗時更需要把畫面還原成使用者交出去時的樣子。"""
        tabs = [[r"D:\VMware\X\X.vmx"], []]
        reopened = []
        with self.assertRaises(ValueError):
            with vm_1809.preserved_tab(r"D:\VMware\X\X.vmx",
                                       list_tabs=lambda: tabs.pop(0),
                                       reopen=reopened.append):
                raise ValueError("boom")
        self.assertEqual(len(reopened), 1)


class FreshBootTests(unittest.TestCase):
    def test_passes_the_display_mode_through(self):
        """模式在 fresh_boot 就要定下來——開機之後再改得先關機重開。"""
        run = FakeRun([
            FakeCompleted(),
            FakeCompleted(),
            FakeCompleted(stdout="running\n"),
        ])
        vm_1809.fresh_boot(make_vm(run), snapshot="Clean", gui=True)
        start_call = run.calls[1]
        self.assertIn("gui", start_call)
        self.assertNotIn("nogui", start_call)

    def test_reverts_starts_then_waits(self):
        """殘留狀態上跑出來的結果不算數，還原必須排在開機之前。"""
        run = FakeRun([
            FakeCompleted(),
            FakeCompleted(),
            FakeCompleted(stdout="running\n"),
        ])
        vm_1809.fresh_boot(make_vm(run), snapshot="Clean")
        self.assertEqual(
            run.subcommands,
            ["revertToSnapshot", "start", "checkToolsState"],
        )


if __name__ == "__main__":
    unittest.main()
