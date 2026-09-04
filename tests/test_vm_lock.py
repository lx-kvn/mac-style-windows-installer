"""虛擬機占用協調（tools/vm_lock.py）的行為定義。

這台機器上可能同時有多個 agent session 在跑（實際發生過：FileLocker 與
mac-style-windows-installer 兩邊各有一個 session 同時工作）。兩邊都會經由
tools/vms.py 驅動同一批虛擬機，其中 revertToSnapshot 是破壞性的——另一邊
正做到一半的安裝、正在等的畫面，會在毫無徵兆的情況下被還原掉。

租約而不是行程鎖：每次執行腳本都是一個新的 python 行程，跑完就結束，用
「持有鎖的行程還活著嗎」判斷殘留的話，同一個 session 在兩次操作之間就會
失去鎖。因此改成「誰佔的、佔到幾點」，同一個 owner 再要就是續租，時間到
自動視為無人持有——這樣既跨得過行程邊界，卡住時也會自己解開，不需要人工
清理殘留檔案。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import vm_lock


class LockTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.lock_dir = self._dir.name
        self.addCleanup(self._dir.cleanup)

    def acquire(self, vm="win11", owner="agent-a", now=1000.0, minutes=30,
                purpose=""):
        return vm_lock.acquire(vm, owner=owner, purpose=purpose,
                               minutes=minutes, now=now, lock_dir=self.lock_dir)

    def holder(self, vm="win11", now=1000.0):
        return vm_lock.holder(vm, now=now, lock_dir=self.lock_dir)


class AcquireTests(LockTestCase):
    def test_free_machine_can_be_acquired(self):
        lease = self.acquire(owner="agent-a", purpose="驗證 Pipe is broken")
        self.assertEqual(lease.owner, "agent-a")
        self.assertEqual(lease.vm, "win11")
        self.assertEqual(lease.purpose, "驗證 Pipe is broken")

    def test_holder_reports_current_owner(self):
        self.acquire(owner="agent-a")
        self.assertEqual(self.holder().owner, "agent-a")

    def test_holder_is_none_when_nobody_holds_it(self):
        self.assertIsNone(self.holder())

    def test_same_owner_renews_instead_of_failing(self):
        first = self.acquire(owner="agent-a", now=1000.0, minutes=30)
        second = self.acquire(owner="agent-a", now=2000.0, minutes=30)
        self.assertGreater(second.expires_at, first.expires_at)

    def test_other_owner_is_refused_while_lease_is_live(self):
        self.acquire(owner="agent-a", now=1000.0, minutes=30)
        with self.assertRaises(vm_lock.VmBusy) as caught:
            self.acquire(owner="agent-b", now=1001.0)
        self.assertIn("agent-a", str(caught.exception))

    def test_refusal_message_names_the_machine_and_the_purpose(self):
        """訊息要能直接轉述給使用者，不必再去翻鎖檔內容。"""
        self.acquire(owner="agent-a", now=1000.0, purpose="裝 MSIX 測側載")
        with self.assertRaises(vm_lock.VmBusy) as caught:
            self.acquire(owner="agent-b", now=1001.0)
        message = str(caught.exception)
        self.assertIn("win11", message)
        self.assertIn("裝 MSIX 測側載", message)

    def test_expired_lease_can_be_taken_over(self):
        self.acquire(owner="agent-a", now=1000.0, minutes=30)
        lease = self.acquire(owner="agent-b", now=1000.0 + 31 * 60)
        self.assertEqual(lease.owner, "agent-b")

    def test_two_machines_are_locked_independently(self):
        self.acquire(vm="win11", owner="agent-a")
        lease = self.acquire(vm="win1809", owner="agent-b")
        self.assertEqual(lease.owner, "agent-b")
        self.assertEqual(self.holder("win11").owner, "agent-a")


class ExpiryTests(LockTestCase):
    def test_holder_is_none_once_the_lease_has_expired(self):
        self.acquire(owner="agent-a", now=1000.0, minutes=30)
        self.assertIsNone(self.holder(now=1000.0 + 31 * 60))

    def test_lease_is_still_reported_just_before_expiry(self):
        self.acquire(owner="agent-a", now=1000.0, minutes=30)
        self.assertEqual(self.holder(now=1000.0 + 29 * 60).owner, "agent-a")


class ReleaseTests(LockTestCase):
    def test_release_frees_the_machine(self):
        self.acquire(owner="agent-a")
        vm_lock.release("win11", owner="agent-a", lock_dir=self.lock_dir)
        self.assertIsNone(self.holder())

    def test_release_of_somebody_elses_lease_is_refused(self):
        """別人正在用的機器不能被順手解鎖——那等於繞過整個協調機制。"""
        self.acquire(owner="agent-a", now=1000.0)
        with self.assertRaises(vm_lock.VmBusy):
            vm_lock.release("win11", owner="agent-b", now=1001.0,
                            lock_dir=self.lock_dir)
        self.assertEqual(self.holder().owner, "agent-a")

    def test_force_release_is_possible_for_the_user_to_unstick_things(self):
        """使用者親自決定要拆別人的鎖時的逃生門，預設不會走到這裡。"""
        self.acquire(owner="agent-a", now=1000.0)
        vm_lock.release("win11", owner="agent-b", force=True, now=1001.0,
                        lock_dir=self.lock_dir)
        self.assertIsNone(self.holder())

    def test_releasing_a_free_machine_is_not_an_error(self):
        vm_lock.release("win11", owner="agent-a", lock_dir=self.lock_dir)
        self.assertIsNone(self.holder())


class OwnerIdentityTests(LockTestCase):
    def test_owner_defaults_to_the_environment_variable(self):
        lease = vm_lock.acquire("win11", environ={"VM_LOCK_OWNER": "agent-env"},
                                now=1000.0, lock_dir=self.lock_dir)
        self.assertEqual(lease.owner, "agent-env")

    def test_missing_owner_says_what_to_set(self):
        """匿名持有等於誰都可以續租別人的租約，協調機制就失去意義。"""
        with self.assertRaises(vm_lock.VmError) as caught:
            vm_lock.acquire("win11", environ={}, now=1000.0,
                            lock_dir=self.lock_dir)
        self.assertIn("VM_LOCK_OWNER", str(caught.exception))


class ReservedContextTests(LockTestCase):
    def test_lease_is_released_on_the_way_out(self):
        with vm_lock.reserved("win11", owner="agent-a", now=1000.0,
                              lock_dir=self.lock_dir):
            self.assertEqual(self.holder().owner, "agent-a")
        self.assertIsNone(self.holder())

    def test_lease_is_released_even_when_the_body_raises(self):
        with self.assertRaises(ZeroDivisionError):
            with vm_lock.reserved("win11", owner="agent-a", now=1000.0,
                                  lock_dir=self.lock_dir):
                raise ZeroDivisionError
        self.assertIsNone(self.holder())


class LockFileTests(LockTestCase):
    def test_lock_file_is_readable_by_a_human(self):
        """卡住時使用者要能自己打開來看是誰佔的，不必先問 agent。"""
        self.acquire(owner="agent-a", now=1000.0, purpose="驗證雙擊解密")
        path = os.path.join(self.lock_dir, "win11.lock")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("agent-a", text)
        self.assertIn("驗證雙擊解密", text)

    def test_corrupt_lock_file_is_treated_as_free(self):
        """壞掉的鎖檔不該把機器永久鎖死——這種檔案沒有可信的持有者資訊。"""
        os.makedirs(self.lock_dir, exist_ok=True)
        with open(os.path.join(self.lock_dir, "win11.lock"), "w",
                  encoding="utf-8") as handle:
            handle.write("這不是 JSON")
        self.assertIsNone(self.holder())
        self.assertEqual(self.acquire(owner="agent-b").owner, "agent-b")


if __name__ == "__main__":
    unittest.main()
