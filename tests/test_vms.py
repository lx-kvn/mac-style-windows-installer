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
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _fakes
from tools import vm_lock, vms


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
                         reserve=False,
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
                                 "WIN11_VM_ENCRYPTION_PASSWORD": "k"},
                        reserve=False)
        message = str(caught.exception)
        self.assertIn("standard_user", message)
        self.assertIn("nope", message)


VMX_SAMPLE = (
    'displayName = "Demo"\n'
    'sata0:0.fileName = "Demo.vmdk"\n'
    'sata0:1.deviceType = "cdrom-image"\n'
    'sata0:1.fileName = "C:\\Downloads\\windows.iso"\n'
    'sata0:1.present = "TRUE"\n'
    'sata0:1.startConnected = "FALSE"\n'
    'memsize = "4096"\n'
)


class CdromImageTests(unittest.TestCase):
    """把光碟機指到指定的 ISO。

    存在的理由是速度：`CopyFileFromHostToGuest` 走 VMware Tools 的控制通道，
    那條管線是設計來傳設定值這類小東西的，實測 GB 級別的檔案只有 1.8 MB/s
    ——2.23 GB 要跑二十分鐘。改由虛擬光碟讀取，客體是以虛擬磁碟的速度存取
    主機上的檔案，且安裝檔可以直接從光碟執行，複製那一步整個消失。

    改的是既有的光碟機，不新增硬體；而且在「還原快照之後、開機之前」套用，
    不留下永久變更。
    """

    def test_points_the_existing_drive_at_the_image(self):
        updated = vms.set_cdrom_image(VMX_SAMPLE, r"D:\payload.iso")
        self.assertIn('sata0:1.fileName = "D:\\payload.iso"', updated)
        self.assertNotIn("windows.iso", updated)

    def test_connects_the_drive_at_power_on(self):
        """原本是 FALSE——不改的話開機後客體看不到那台光碟機。"""
        updated = vms.set_cdrom_image(VMX_SAMPLE, r"D:\payload.iso")
        self.assertIn('sata0:1.startConnected = "TRUE"', updated)
        self.assertNotIn('sata0:1.startConnected = "FALSE"', updated)

    def test_adds_the_setting_when_the_file_does_not_have_it(self):
        without = VMX_SAMPLE.replace('sata0:1.startConnected = "FALSE"\n', "")
        updated = vms.set_cdrom_image(without, r"D:\payload.iso")
        self.assertIn('sata0:1.startConnected = "TRUE"', updated)

    def test_leaves_everything_else_alone(self):
        updated = vms.set_cdrom_image(VMX_SAMPLE, r"D:\payload.iso")
        self.assertIn('sata0:0.fileName = "Demo.vmdk"', updated)
        self.assertIn('memsize = "4096"', updated)
        self.assertIn('displayName = "Demo"', updated)

    def test_a_machine_without_a_cdrom_is_a_clear_error(self):
        without = "\n".join(line for line in VMX_SAMPLE.splitlines()
                            if "sata0:1" not in line)
        with self.assertRaises(vms.VmError) as caught:
            vms.set_cdrom_image(without, r"D:\payload.iso")
        self.assertIn("光碟機", str(caught.exception))

    def test_reads_and_writes_using_the_declared_encoding(self):
        """`.vmx` 第一行宣告自己的編碼，實測這台機器的是 Big5。

        以 UTF-8 讀寫會在檔案裡出現非 ASCII 字元時把設定檔寫壞（虛擬機名稱
        用中文就會踩到），而寫壞的症狀是虛擬機開不起來——不會有人聯想到是
        掛 ISO 這個動作造成的。
        """
        path = os.path.join(tempfile.mkdtemp(), "demo.vmx")
        original = ('.encoding = "Big5"\n'
                    'displayName = "測試用虛擬機"\n'
                    + VMX_SAMPLE)
        with open(path, "wb") as handle:
            handle.write(original.encode("big5"))

        vms.write_vmx(path, vms.set_cdrom_image(vms.read_vmx(path),
                                                r"D:\payload.iso"))

        with open(path, "rb") as handle:
            written = handle.read().decode("big5")
        self.assertIn("測試用虛擬機", written)
        self.assertIn('sata0:1.fileName = "D:\\payload.iso"', written)

    def test_defaults_to_utf8_when_no_encoding_is_declared(self):
        path = os.path.join(tempfile.mkdtemp(), "demo.vmx")
        with open(path, "wb") as handle:
            handle.write(VMX_SAMPLE.encode("utf-8"))
        self.assertIn("sata0:1", vms.read_vmx(path))

    def test_applying_it_twice_is_the_same_as_once(self):
        """每次還原快照之後都會重新套用，重複套用不能越改越亂。"""
        once = vms.set_cdrom_image(VMX_SAMPLE, r"D:\payload.iso")
        twice = vms.set_cdrom_image(once, r"D:\payload.iso")
        self.assertEqual(once, twice)


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
                         reserve=False,
                         run=run, vmrun=r"C:\vmware\vmrun.exe")
        vm.revert()
        cmd = run.calls[0]
        self.assertIn("-vp", cmd)
        self.assertIn("disk", cmd)

    def test_does_not_require_an_encryption_variable_when_none_is_declared(self):
        """未加密的機器不該因為少設一個不相干的環境變數而無法使用。"""
        run = FakeRun()
        vm = vms.connect(PLAIN, environ={"PLAIN_PW": "guest"},
                         reserve=False,
                         run=run, vmrun=r"C:\vmware\vmrun.exe")
        vm.revert()
        self.assertNotIn("-vp", run.calls[0])

    def test_accepts_a_machine_key_as_well_as_a_machine(self):
        run = FakeRun()
        vms.connect("win1809", environ={"WIN1809_VM_PASSWORD": "guest"},
                    reserve=False,
                    run=run, vmrun=r"C:\vmware\vmrun.exe").revert()
        self.assertIn(vms.machine("win1809").vmx, run.calls[0])


class ConnectReservationTests(unittest.TestCase):
    """connect 是所有人共同的入口，占用協調就掛在這裡——掛在個別的破壞性
    指令上，會漏掉「先佔住再慢慢做」這個真正需要保護的用法（另一邊的還原
    要在我們開始之前就被擋下來，不是在我們送出還原指令的那一瞬間才比對）。
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.lock_dir = self._dir.name
        self.addCleanup(self._dir.cleanup)

    def test_connect_reserves_the_machine_by_default(self):
        vms.connect(PLAIN, environ={"PLAIN_PW": "guest",
                                    "VM_LOCK_OWNER": "agent-a"},
                    lock_dir=self.lock_dir,
                    run=FakeRun(), vmrun=r"C:mwaremrun.exe")
        self.assertEqual(
            vm_lock.holder(PLAIN.key, lock_dir=self.lock_dir).owner, "agent-a")

    def test_connect_is_refused_while_another_owner_holds_it(self):
        vm_lock.acquire(PLAIN.key, owner="agent-a", purpose="裝 MSIX",
                        lock_dir=self.lock_dir)
        with self.assertRaises(vms.VmBusy) as caught:
            vms.connect(PLAIN, environ={"PLAIN_PW": "guest",
                                        "VM_LOCK_OWNER": "agent-b"},
                        lock_dir=self.lock_dir,
                        run=FakeRun(), vmrun=r"C:mwaremrun.exe")
        self.assertIn("agent-a", str(caught.exception))

    def test_reserve_false_leaves_the_lock_alone(self):
        vms.connect(PLAIN, environ={"PLAIN_PW": "guest",
                                    "VM_LOCK_OWNER": "agent-a"},
                    reserve=False, lock_dir=self.lock_dir,
                    run=FakeRun(), vmrun=r"C:mwaremrun.exe")
        self.assertIsNone(vm_lock.holder(PLAIN.key, lock_dir=self.lock_dir))

    def test_purpose_reaches_the_lock_so_the_other_side_sees_why(self):
        vms.connect(PLAIN, environ={"PLAIN_PW": "guest",
                                    "VM_LOCK_OWNER": "agent-a"},
                    purpose="驗證 Pipe is broken", lock_dir=self.lock_dir,
                    run=FakeRun(), vmrun=r"C:mwaremrun.exe")
        self.assertEqual(
            vm_lock.holder(PLAIN.key, lock_dir=self.lock_dir).purpose,
            "驗證 Pipe is broken")


class LeaseRenewalTests(unittest.TestCase):
    """每一次真的碰虛擬機的動作都順手續租。

    租約時間因此不是「一次工作最多能做多久」，而是「最後一次碰它之後多久
    視為離開」。少了這一段時，租約長度等於單次工作的上限，而那個值猜不準
    ——訂短了會在工作進行中被別人接手（且不會有任何錯誤訊息），訂長了則在
    session 當掉之後把機器擋著。

    續租點掛在 _invoke：所有操作最後都經過它，掛在個別方法上會漏掉新增的。
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.lock_dir = self._dir.name
        self.addCleanup(self._dir.cleanup)

    def make_vm(self, run=None):
        return vms.connect(PLAIN, environ={"PLAIN_PW": "guest",
                                           "VM_LOCK_OWNER": "agent-a"},
                           lock_dir=self.lock_dir, purpose="測試",
                           run=run or FakeRun(), vmrun=r"C:mrun.exe")

    def test_an_operation_extends_the_lease(self):
        vm = self.make_vm()
        before = vm_lock.holder(PLAIN.key, lock_dir=self.lock_dir).expires_at
        # 推到過了一半之後——前半段續租不寫檔（省掉的是寫入，不是保護）。
        vm._lease_now = lambda: time.time() + vm_lock.DEFAULT_MINUTES * 60 * 0.8
        vm.start()
        after = vm_lock.holder(PLAIN.key, lock_dir=self.lock_dir,
                               now=vm._lease_now()).expires_at
        self.assertGreater(after, before)

    def test_losing_the_lease_stops_the_operation_before_it_runs(self):
        """被接手之後不能再碰那台機器——另一邊可能正在上面工作。"""
        run = FakeRun()
        vm = self.make_vm(run=run)
        vm_lock.release(PLAIN.key, force=True, lock_dir=self.lock_dir)
        vm_lock.acquire(PLAIN.key, owner="agent-b", lock_dir=self.lock_dir)

        calls_before = len(run.calls)
        with self.assertRaises(vm_lock.LeaseLost):
            vm.revert()
        self.assertEqual(len(run.calls), calls_before,
                         "失去租約時不能真的送出指令")

    def test_reserve_false_does_not_renew(self):
        """只是要組指令列、不會真的碰到機器的用途（測試即是）。"""
        vm = vms.connect(PLAIN, environ={"PLAIN_PW": "guest",
                                         "VM_LOCK_OWNER": "agent-a"},
                         reserve=False, lock_dir=self.lock_dir,
                         run=FakeRun(), vmrun=r"C:mrun.exe")
        vm.start()
        self.assertIsNone(vm_lock.holder(PLAIN.key, lock_dir=self.lock_dir))


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

    def test_run_program_can_return_without_waiting(self):
        """會停下來等使用者的程式一定要用這個，否則主機端會跟著一起等。

        實際踩到過：以 interactive=True 啟動一支會跳出對話框的安裝程式而沒有
        加 -noWait，vmrun 等安裝程式結束、安裝程式等使用者回答，兩邊互相等到
        逾時。症狀是「指令沒有回來」，看不出成因。
        """
        run = FakeRun()
        make_vm(run).run_program(r"C:\x.exe", no_wait=True)
        cmd = run.calls[0]
        self.assertIn("-noWait", cmd)
        self.assertLess(cmd.index("-noWait"), cmd.index(r"C:\x.exe"))

    def test_run_program_waits_by_default(self):
        run = FakeRun()
        make_vm(run).run_program(r"C:\x.exe")
        self.assertNotIn("-noWait", run.calls[0])

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

    def test_mounting_an_iso_forces_a_cold_boot(self):
        """`startConnected` 只在冷開機時套用。

        實測：還原快照後掛上 ISO 再恢復，客體回報「媒體已載入 = False」，
        連在客體內重新開機也無效——恢復時裝置狀態是從記憶體映像還原的，
        不重新列舉硬體。先恢復一次再強制關機，把記憶體狀態丟掉，之後那次
        `start` 才是真正的冷開機（實測耗時 17.8 秒，客體隨即看得到光碟）。

        掛載排在關機之後：虛擬機關機時 VMware 會重寫 `.vmx`，在那之前改
        有被覆寫的風險。
        """
        run = FakeRun()
        attached = []

        def fake_attach(vm, iso):
            attached.append((iso, list(run.subcommands)))

        vms.fresh_boot(make_vm(run), iso="payload.iso", attach=fake_attach)
        self.assertEqual(len(attached), 1)
        iso, before = attached[0]
        self.assertEqual(iso, "payload.iso")
        self.assertEqual(before[-1], "stop")
        self.assertEqual(run.subcommands[:5],
                         ["revertToSnapshot", "start", "runProgramInGuest",
                          "stop", "start"])

    def test_no_iso_means_no_extra_boot(self):
        """沒有要掛東西時不該多付一次開機的時間。"""
        run = FakeRun()
        vms.fresh_boot(make_vm(run))
        self.assertNotIn("stop", run.subcommands)

    def test_stops_the_guest_from_falling_asleep(self):
        """自動化全程沒有使用者輸入，Windows 因此認定客體閒置並進入睡眠。

        實際發生過：送入一個 2.23 GB 的檔案時，客體在傳輸途中發出 ACPI S1
        睡眠要求，VMware 隨即暫停虛擬機，主機端拿到的錯誤是「虛擬機需要處於
        開機狀態」——訊息指向電源狀態，完全看不出成因是客體自己睡著了。

        短操作（數秒）永遠碰不到這條線，因此這個問題直到有 GB 級別的傳輸才
        浮現。設定在快照還原後才套用，隨快照一起丟棄，不動到快照本身。
        """
        run = FakeRun()
        vms.fresh_boot(make_vm(run))
        joined = " ".join(" ".join(call) for call in run.calls)
        self.assertIn("powercfg", joined)
        self.assertIn("standby-timeout-ac", joined)

    def test_reverts_starts_then_waits(self):
        """殘留狀態上跑出來的結果不算數，還原必須排在開機之前。

        另一個理由：略過還原直接開機是從硬碟冷開機，客體停在鎖定畫面，
        沒有互動登入，-interactive 會被拒絕。
        """
        run = FakeRun()
        vms.fresh_boot(make_vm(run))
        # 前三個是還原、開機、就緒探測；其後是關閉睡眠的幾道設定（見上一項
        # 測試），數量不斷言，以免加減一項設定就要改這裡。
        self.assertEqual(
            run.subcommands[:3],
            ["revertToSnapshot", "start", "runProgramInGuest"],
        )
        self.assertTrue(
            all(name == "runProgramInGuest" for name in run.subcommands[3:]))


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
