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
class FencingTokenTests(LockTestCase):
    """被強制拆鎖時，原持有者必須當場知道，不能安靜地把它再佔回來。

    使用者要立刻放掉一台機器時的逃生門是「直接刪掉鎖檔」（錯誤訊息裡就這樣
    寫）。刪掉之後另一個 session 會佔進去，而原持有者下一次續租時，若只比對
    「持有者名字」就會發現檔案不存在、於是重新佔一次——兩邊同時以為自己拿到
    了，正是這個模組要防的事，而且不會有任何錯誤訊息。

    因此每次占用發一張編號，續租要帶著它；對不上就代表這張租約已經不是你的。
    """

    def test_acquire_mints_a_token(self):
        lease = self.acquire()
        self.assertTrue(lease.token)

    def test_two_acquisitions_get_different_tokens(self):
        first = self.acquire(vm="win11")
        second = self.acquire(vm="win1809")
        self.assertNotEqual(first.token, second.token)

    def test_renew_with_the_matching_token_succeeds(self):
        lease = self.acquire(now=1000.0, minutes=10)
        renewed = vm_lock.renew("win11", owner="agent-a", token=lease.token,
                                minutes=10, now=1400.0, lock_dir=self.lock_dir)
        self.assertEqual(renewed.token, lease.token)

    def test_renew_after_somebody_else_took_over_is_refused(self):
        lease = self.acquire(owner="agent-a", now=1000.0, minutes=10)
        # 使用者拆鎖、另一邊佔了進去——這正是逃生門被用過之後的狀態。
        vm_lock.release("win11", force=True, lock_dir=self.lock_dir)
        self.acquire(owner="agent-b", now=1100.0, minutes=10)

        with self.assertRaises(vm_lock.LeaseLost) as caught:
            vm_lock.renew("win11", owner="agent-a", token=lease.token,
                          minutes=10, now=1200.0, lock_dir=self.lock_dir)
        self.assertIn("agent-b", str(caught.exception))

    def test_renew_after_the_lock_file_vanished_is_refused(self):
        lease = self.acquire(owner="agent-a", now=1000.0, minutes=10)
        vm_lock.release("win11", force=True, lock_dir=self.lock_dir)

        # 檔案不在也算失去——不能默默再佔一次，那會蓋掉可能已經進來的人。
        with self.assertRaises(vm_lock.LeaseLost):
            vm_lock.renew("win11", owner="agent-a", token=lease.token,
                          minutes=10, now=1200.0, lock_dir=self.lock_dir)

    def test_lease_lost_is_a_vm_error(self):
        """呼叫端用同一個 except 接得住，不會漏掉這一種。"""
        self.assertTrue(issubclass(vm_lock.LeaseLost, vm_lock.VmError))


class RenewalFrequencyTests(LockTestCase):
    """續租綁在每一次碰虛擬機的動作上，因此會被呼叫得很頻繁；過了一半才真的
    寫檔，省掉大部分的寫入，而效果相同。"""

    def test_renew_before_halfway_does_not_move_the_expiry(self):
        lease = self.acquire(now=1000.0, minutes=10)      # 到 1600
        renewed = vm_lock.renew("win11", owner="agent-a", token=lease.token,
                                minutes=10, now=1100.0, lock_dir=self.lock_dir)
        self.assertEqual(renewed.expires_at, lease.expires_at)

    def test_renew_past_halfway_extends_the_expiry(self):
        lease = self.acquire(now=1000.0, minutes=10)      # 到 1600
        renewed = vm_lock.renew("win11", owner="agent-a", token=lease.token,
                                minutes=10, now=1400.0, lock_dir=self.lock_dir)
        self.assertEqual(renewed.expires_at, 1400.0 + 600)

    def test_renew_keeps_the_original_acquisition_time(self):
        """續租不是一次新的占用——看得出這台機器實際上已經被佔多久。"""
        lease = self.acquire(now=1000.0, minutes=10)
        renewed = vm_lock.renew("win11", owner="agent-a", token=lease.token,
                                minutes=10, now=1400.0, lock_dir=self.lock_dir)
        self.assertEqual(renewed.acquired_at, lease.acquired_at)


class EventLogTests(LockTestCase):
    """事後要能回答「當時是不是有人動過這台機器」。

    實際發生過的情形：一輪量測拿到了看起來合理、其實是錯的數字，而當下沒有
    任何錯誤訊息。有紀錄才查得出來那段時間機器換過手。
    """

    def read_log(self):
        path = vm_lock.log_path(lock_dir=self.lock_dir)
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_acquire_is_recorded(self):
        self.acquire(owner="agent-a", purpose="驗證註冊行為")
        text = self.read_log()
        self.assertIn("acquire", text)
        self.assertIn("agent-a", text)
        self.assertIn("win11", text)
        self.assertIn("驗證註冊行為", text)

    def test_refusal_is_recorded_with_both_names(self):
        self.acquire(owner="agent-a", now=1000.0, minutes=10)
        with self.assertRaises(vm_lock.VmBusy):
            self.acquire(owner="agent-b", now=1100.0, minutes=10)
        text = self.read_log()
        self.assertIn("refuse", text)
        self.assertIn("agent-b", text)

    def test_release_is_recorded(self):
        self.acquire(owner="agent-a")
        vm_lock.release("win11", owner="agent-a", lock_dir=self.lock_dir)
        self.assertIn("release", self.read_log())

    def test_force_release_is_recorded_as_such(self):
        """強制拆鎖是使用者的介入，跟正常歸還要分得出來。"""
        self.acquire(owner="agent-a")
        vm_lock.release("win11", force=True, lock_dir=self.lock_dir)
        self.assertIn("force-release", self.read_log())

    def test_log_lines_are_one_event_each(self):
        self.acquire(owner="agent-a")
        vm_lock.release("win11", owner="agent-a", lock_dir=self.lock_dir)
        lines = [l for l in self.read_log().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)


class ConcurrentAcquireTests(LockTestCase):
    """同時開始的兩個 session 不能都以為自己拿到了。

    「讀檔 → 判斷 → 寫檔」若沒有被圍成一個不可分割的動作，兩邊可能都先讀到
    「沒人用」然後都寫上去，後寫的蓋掉先寫的，而兩邊都以為自己是持有者。時間
    窗只有幾毫秒，但踩到時完全沒有錯誤訊息。
    """

    def test_only_one_of_many_simultaneous_acquirers_wins(self):
        import threading

        winners, refused = [], []
        start = threading.Barrier(8)

        def contend(index):
            start.wait()
            try:
                lease = vm_lock.acquire("win11", owner="agent-%d" % index,
                                        minutes=10, lock_dir=self.lock_dir)
                winners.append(lease.owner)
            except vm_lock.VmBusy:
                refused.append(index)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(winners), 1, "同時搶的人裡面只能有一個拿到")
        self.assertEqual(len(refused), 7)

    def test_the_winner_is_the_one_recorded_in_the_lock_file(self):
        import threading

        winners = []
        start = threading.Barrier(6)

        def contend(index):
            start.wait()
            try:
                winners.append(vm_lock.acquire(
                    "win11", owner="agent-%d" % index, minutes=10,
                    lock_dir=self.lock_dir).owner)
            except vm_lock.VmBusy:
                pass

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(self.holder(now=None).owner, winners[0])
