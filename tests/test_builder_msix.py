"""builder.build_msix() 的測試：把 .msix 的組裝、打包、簽章串成一件事。

第二輪決議第三項在兩截式骨架之上留了一條「一體式」便捷路徑——憑證是本機
檔案時，由工具自己把三個步驟串完。這個函式就是那條路徑，CLI 與 GUI 共用
同一份；兩邊各寫一份的話，會分頭長歪成兩種行為。

全程用假的 subprocess.run 與假的 find_tool，測試不需要真的有 SDK 工具。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import builder


class FakeTool:
    def __init__(self, name):
        self.path = os.path.join("C:", "fake_sdk", name)
        self.tool = name

    def describe(self):
        return f"{self.tool}：{self.path}"


class BuildMsixTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app_dir = os.path.join(self.tmp, "app")
        os.makedirs(self.app_dir)
        self.png = os.path.join(self.app_dir, "icon.png")
        with open(self.png, "wb") as f:
            f.write(b"placeholder")
        self.workspace = os.path.join(self.tmp, "ws")
        os.makedirs(self.workspace)
        self.output = os.path.join(self.workspace, "MyCompany.DemoApp.msix")
        self.commands = []

    def pack_data(self, **overrides):
        data = {
            "app_name": "DemoApp",
            "publisher": "Demo Inc",
            "main_exe": "main.exe",
            "add_to_path": False,
            "path_target_exe": "",
            "file_associations": [],
            "doc_icons": {},
            "msix": {
                "identity_name": "MyCompany.DemoApp",
                "certificate_subject": "CN=Demo",
                "package_version": "1.0.0.0",
                "min_windows_version": "10.0.17763.0",
                "icons": {},
            },
        }
        data.update(overrides)
        return data

    def fake_run(self, cmd, **kwargs):
        self.commands.append(cmd)
        return mock.Mock(returncode=0, stdout="", stderr="")

    def call(self, signing=None, run=None, **overrides):
        kwargs = dict(
            app_dir=self.app_dir,
            pack_data=self.pack_data(),
            png_path=self.png,
            output_path=self.output,
            workspace_dir=self.workspace,
            signing=signing,
            find_tool=lambda name: FakeTool(name),
            run=run or self.fake_run,
        )
        kwargs.update(overrides)
        return builder.build_msix(**kwargs)

    def signing_config(self):
        cert = os.path.join(self.tmp, "cert.pfx")
        with open(cert, "wb") as f:
            f.write(b"fake pfx")
        os.environ["TEST_MSIX_CERT_PW"] = "hunter2"
        self.addCleanup(os.environ.pop, "TEST_MSIX_CERT_PW", None)
        return {
            "cert_path": cert,
            "cert_password_env": "TEST_MSIX_CERT_PW",
            "timestamp_url": "http://timestamp.example/ts",
        }

    def signtool_commands(self):
        return [c for c in self.commands if "signtool.exe" in c[0]]


class StagingAndPackingTest(BuildMsixTestBase):
    def test_it_stages_then_packs(self):
        with mock.patch("msix_package.stage") as stage, \
                mock.patch("msix_package.pack", return_value=self.output) as pack:
            self.call()
        self.assertEqual(stage.call_count, 1)
        self.assertEqual(pack.call_count, 1)

    def test_the_normalized_values_from_pack_data_reach_staging(self):
        with mock.patch("msix_package.stage") as stage, \
                mock.patch("msix_package.pack", return_value=self.output):
            self.call()
        kwargs = stage.call_args.kwargs
        self.assertEqual(kwargs["identity_name"], "MyCompany.DemoApp")
        self.assertEqual(kwargs["certificate_subject"], "CN=Demo")
        self.assertEqual(kwargs["version"], "1.0.0.0")
        self.assertEqual(kwargs["min_windows_version"], "10.0.17763.0")
        self.assertEqual(kwargs["app_name"], "DemoApp")
        self.assertEqual(kwargs["publisher"], "Demo Inc")

    def test_the_staging_directory_is_inside_the_workspace(self):
        """組裝目錄是中間產物，放在工作目錄底下才會跟著工作目錄一起被管理。"""
        with mock.patch("msix_package.stage") as stage, \
                mock.patch("msix_package.pack", return_value=self.output):
            self.call()
        staging = stage.call_args.kwargs["staging_dir"]
        self.assertTrue(
            os.path.abspath(staging).startswith(os.path.abspath(self.workspace)),
            f"組裝目錄跑到工作目錄外面：{staging}")

    def test_it_returns_the_output_path(self):
        with mock.patch("msix_package.stage"), \
                mock.patch("msix_package.pack", return_value=self.output):
            self.assertEqual(self.call(), self.output)


class SigningTest(BuildMsixTestBase):
    """憑證是本機檔案時才簽；不是的話產物必須是未簽章的。"""

    def test_without_a_local_certificate_nothing_is_signed(self):
        """pack-msix 走的就是這一條：它的產物按定義是未簽章的，簽下去會讓
        雲端代簽的情境失去容身之處（第二輪決議第三項）。"""
        with mock.patch("msix_package.stage"), \
                mock.patch("msix_package.pack", return_value=self.output):
            self.call(signing=None)
        self.assertEqual(self.signtool_commands(), [])

    def test_with_a_local_certificate_the_package_is_signed(self):
        with mock.patch("msix_package.stage"), \
                mock.patch("msix_package.pack", return_value=self.output):
            self.call(signing=self.signing_config())
        signed = self.signtool_commands()
        self.assertEqual(len(signed), 1, f"實際下的指令：{self.commands}")
        self.assertIn(self.output, signed[0])

    def test_the_package_is_signed_after_it_is_packed(self):
        """順序反了的話 signtool 會對著一個還不存在的檔案動作。"""
        order = []

        def packed(*args, **kwargs):
            order.append("pack")
            return self.output

        def run(cmd, **kwargs):
            self.commands.append(cmd)
            if "signtool.exe" in cmd[0]:
                order.append("sign")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("msix_package.stage"), \
                mock.patch("msix_package.pack", side_effect=packed):
            self.call(signing=self.signing_config(), run=run)
        self.assertEqual(order, ["pack", "sign"])

    def test_a_signing_failure_stops_the_build(self):
        """簽不成就回傳一個未簽章的套件，呼叫端會把它內嵌進安裝檔，而那份
        安裝檔要到終端使用者手上才會失敗。"""
        def run(cmd, **kwargs):
            self.commands.append(cmd)
            if "signtool.exe" in cmd[0]:
                return mock.Mock(returncode=1, stdout="", stderr="憑證密碼錯誤")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("msix_package.stage"), \
                mock.patch("msix_package.pack", return_value=self.output):
            with self.assertRaises(Exception) as ctx:
                self.call(signing=self.signing_config(), run=run)
        self.assertIn("憑證密碼錯誤", str(ctx.exception))

    def test_the_certificate_password_never_appears_in_the_error(self):
        def run(cmd, **kwargs):
            self.commands.append(cmd)
            if "signtool.exe" in cmd[0]:
                return mock.Mock(returncode=1, stdout="", stderr="失敗")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("msix_package.stage"), \
                mock.patch("msix_package.pack", return_value=self.output):
            with self.assertRaises(Exception) as ctx:
                self.call(signing=self.signing_config(), run=run)
        self.assertNotIn("hunter2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
