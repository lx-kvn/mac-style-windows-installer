"""tools/measure_msix_scope.py 的測試：全機器範圍的兩項待驗行為。

`ADR-0013` 的待辦兩項在動工之前必須有答案：

1. **決定六的「同範圍直接更新」**——已知限制第四項記載，於已有使用者註冊的
   狀態下重新佈建曾失敗、亦曾成功，兩次的差異未查明。決定六的形狀（同範圍
   直接部署、跨範圍先移除）建立在那個行為上。
2. **背景第五項的根因**——提權的行程替當前使用者註冊會以 `0x80070005` 失敗，
   只確認了現象。決定三（先提權佈建、再降回未提權註冊）建立在那個現象上。

這裡測的是判準與客體腳本的性質，不驅動虛擬機：一輪量測要還原快照、開機、
跑好幾個階段，而判準寫錯的後果是拿到一個看起來有結論、實際上不成立的答案。

**這份量測最重要的性質是「無法歸因時不給結論」。** 上一輪之所以要重做，正是
因為同一個情境兩次得到不同結果卻沒有記錄當時的前置狀態；因此每個階段都要
連同「做這件事之前系統是什麼狀態」一起記錄，缺了就判為無法判定。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# vm_lease 由另一個 repo 提供，理由與跳過方式見 tests/test_vms.py 開頭。
try:
    import vm_lease  # noqa: F401
except ImportError as exc:  # pragma: no cover - 取決於執行機器裝了什麼
    raise unittest.SkipTest(
        "vm_lease 未安裝，跳過虛擬機驅動的測試（見 tests/test_vms.py 的說明）"
    ) from exc

from tools import measure_msix_scope as measure


class ParseReport(unittest.TestCase):
    def test_each_step_becomes_a_record(self):
        text = (
            "step=provision_clean\n"
            "before=none\n"
            "result=ok\n"
            "detail=\n"
            "--\n"
            "step=provision_after_user_register\n"
            "before=user_registered\n"
            "result=fail\n"
            "detail=0x80073CF9 安裝失敗\n"
            "--\n")
        steps = measure.parse_report(text)
        self.assertEqual([s["step"] for s in steps],
                         ["provision_clean", "provision_after_user_register"])
        self.assertEqual(steps[1]["result"], "fail")
        self.assertIn("0x80073CF9", steps[1]["detail"])

    def test_a_value_may_contain_an_equals_sign(self):
        steps = measure.parse_report("step=x\ndetail=code=0x1 說明\n--\n")
        self.assertEqual(steps[0]["detail"], "code=0x1 說明")

    def test_an_unterminated_last_step_is_still_returned(self):
        """客體在最後一個階段中途掛掉時，前面量到的仍要帶回來。"""
        steps = measure.parse_report("step=a\nresult=ok\n--\nstep=b\n")
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[1]["step"], "b")
        self.assertNotIn("result", steps[1])


class TheSameScopeUpdateVerdict(unittest.TestCase):
    """決定六：同範圍直接部署即可，不必先移除。"""

    def _steps(self, **results):
        return [{"step": name, "before": "n/a", "result": value, "detail": ""}
                for name, value in results.items()]

    def test_it_holds_when_both_same_scope_updates_succeed(self):
        verdict = measure.evaluate_same_scope_update(self._steps(
            user_update_same_scope="ok", provision_update_same_scope="ok"))
        self.assertEqual(verdict.verdict, measure.HOLDS)

    def test_it_fails_when_a_same_scope_update_is_refused(self):
        verdict = measure.evaluate_same_scope_update(self._steps(
            user_update_same_scope="ok", provision_update_same_scope="fail"))
        self.assertEqual(verdict.verdict, measure.BROKEN)
        self.assertIn("provision_update_same_scope", verdict.detail)

    def test_a_missing_step_is_inconclusive(self):
        """量不到與量到失敗是兩件事。上一輪的教訓就是把兩者混在一起。"""
        verdict = measure.evaluate_same_scope_update(self._steps(
            user_update_same_scope="ok"))
        self.assertEqual(verdict.verdict, measure.INCONCLUSIVE)


class TheElevatedRegistrationVerdict(unittest.TestCase):
    """背景第五項：提權註冊失敗的現象要能重現，並取得系統給的原因。"""

    def test_the_phenomenon_reproduces_and_the_reason_is_captured(self):
        verdict = measure.evaluate_elevated_registration([
            {"step": "register_elevated", "before": "clean", "result": "fail",
             "detail": "0x80070005 存取被拒"},
            {"step": "register_unelevated", "before": "clean", "result": "ok",
             "detail": ""},
            {"step": "appx_log_elevated", "before": "n/a", "result": "ok",
             "detail": "error 0x80070005: 無法為目前使用者註冊（已提升）"},
        ])
        self.assertEqual(verdict.verdict, measure.HOLDS)
        self.assertIn("0x80070005", verdict.detail)

    def test_it_does_not_hold_when_the_elevated_attempt_succeeds(self):
        """現象不再出現時要說出來——決定三的整個形狀是為了繞過它而設計的。"""
        verdict = measure.evaluate_elevated_registration([
            {"step": "register_elevated", "before": "clean", "result": "ok",
             "detail": ""},
            {"step": "register_unelevated", "before": "clean", "result": "ok",
             "detail": ""},
            {"step": "appx_log_elevated", "before": "n/a", "result": "ok",
             "detail": ""},
        ])
        self.assertEqual(verdict.verdict, measure.NOT_REPRODUCED)

    def test_a_failure_without_a_captured_reason_is_inconclusive(self):
        """只知道失敗、不知道原因，等於這一項沒有做——待辦要的正是根因。"""
        verdict = measure.evaluate_elevated_registration([
            {"step": "register_elevated", "before": "clean", "result": "fail",
             "detail": "0x80070005"},
            {"step": "register_unelevated", "before": "clean", "result": "ok",
             "detail": ""},
            {"step": "appx_log_elevated", "before": "n/a", "result": "ok",
             "detail": ""},
        ])
        self.assertEqual(verdict.verdict, measure.INCONCLUSIVE)


class TheGuestScripts(unittest.TestCase):
    def test_every_step_records_the_state_it_started_from(self):
        """每個階段都要記錄前置狀態。上一輪拿到互相矛盾的結果卻無從解釋，
        正是因為沒有記下這件事。"""
        for script in measure.all_scripts(r"C:\Users\Public\probe_v1.msix",
                                          r"C:\Users\Public\probe_v2.msix"):
            self.assertIn("before=", script)
            self.assertIn("Add-Content", script)

    def test_the_report_is_appended_step_by_step(self):
        for script in measure.all_scripts("a.msix", "b.msix"):
            self.assertNotIn("Set-Content -Path $report -Value $all", script)

    def test_the_elevated_and_unelevated_phases_are_separate_scripts(self):
        """佈建要提權、替當前使用者註冊不能提權，兩者必須分開執行——這正是
        要量的那件事，混在同一支腳本裡就量不到。"""
        self.assertGreaterEqual(len(measure.all_scripts("a.msix", "b.msix")), 2)

    def test_the_appx_log_is_collected_after_a_failure(self):
        """`Add-AppxPackage` 給的訊息是一句概括的話；原因在部署紀錄裡。"""
        joined = "\n".join(measure.all_scripts("a.msix", "b.msix"))
        self.assertIn("Get-AppxLog", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
