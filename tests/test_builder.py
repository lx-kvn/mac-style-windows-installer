"""builder.py 的 build_all() 測試。

全程用假的 subprocess.run 頂替真正的 pyinstaller 呼叫（這台機器上跑一次真的
編譯要數十秒，而且測試不應該依賴外部工具是否安裝），驗證的是「這個函式組出
來的設定檔內容、呼叫順序、錯誤處理」這幾件事，不是編譯本身。
"""
import hashlib
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import builder


def make_fake_run(uninstall_dist_dir):
    """回傳一個假的 subprocess.run：遇到編譯 uninstall.exe 的指令就順便在
    dist/ 底下生出一個假的 uninstall.exe，讓 build_all() 後續「檢查產出檔案
    是否存在」那段可以正常通過，不用真的呼叫 pyinstaller。
    """
    def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
        if "uninstall.py" in cmd:
            os.makedirs(uninstall_dist_dir, exist_ok=True)
            with open(os.path.join(uninstall_dist_dir, "uninstall.exe"), "wb") as f:
                f.write(b"FAKE_UNINSTALL_EXE")
        return mock.Mock(returncode=0, stdout="", stderr="")
    return fake_run


class BuildAllTestBase(unittest.TestCase):
    def setUp(self):
        self.workspace_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.workspace_dir, "ui"))
        with open(os.path.join(self.workspace_dir, "ui", "index.html"), "w") as f:
            f.write("<html></html>")
        with open(os.path.join(self.workspace_dir, "ui", "uninstall.html"), "w") as f:
            f.write("<html></html>")
        with open(os.path.join(self.workspace_dir, "installer_core.py"), "w") as f:
            f.write("# stub")
        with open(os.path.join(self.workspace_dir, "uninstall.py"), "w") as f:
            f.write("# stub")

        self.app_dir = tempfile.mkdtemp()
        self.png_path = os.path.join(self.app_dir, "icon.png")
        self.ico_path = os.path.join(self.app_dir, "icon.ico")
        with open(self.png_path, "wb") as f:
            f.write(b"PNG")
        with open(self.ico_path, "wb") as f:
            f.write(b"ICO")

        self.dist_dir = os.path.join(self.workspace_dir, "dist")

    def tearDown(self):
        shutil.rmtree(self.workspace_dir, ignore_errors=True)
        shutil.rmtree(self.app_dir, ignore_errors=True)

    def _call_build_all(self, run_side_effect=None, **overrides):
        kwargs = dict(
            app_dir=self.app_dir,
            exe_name="Setup_TestApp",
            app_name="測試應用程式",
            folder_name="TestApp",
            version="1.0.0",
            publisher="Tester",
            png_path=self.png_path,
            ico_path=self.ico_path,
            main_exe="main.exe",
            workspace_dir=self.workspace_dir,
        )
        kwargs.update(overrides)
        with mock.patch("builder.subprocess.run", side_effect=run_side_effect or make_fake_run(self.dist_dir)):
            builder.build_all(**kwargs)


class TestConfigAssembly(BuildAllTestBase):
    def test_config_content_before_cleanup(self):
        """規格文件記錄過的真實 bug：早期版本 app_name 完全沒傳到 builder.py，
        installer_config.json 裡的 "app_name" 其實是拿 exe_name 冒充。這裡鎖住
        三者不能再混淆：exe_name 不該出現在設定檔裡，app_name/folder_name 要各自對應。
        """
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                # 編最終安裝檔之前，installer_config.json 應該已經寫好且內容正確
                config_path = os.path.join(self.workspace_dir, "installer_config.json")
                with open(config_path, "r", encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("builder.subprocess.run", side_effect=fake_run):
            builder.build_all(
                app_dir=self.app_dir, exe_name="Setup_TestApp", app_name="測試應用程式",
                folder_name="TestApp", version="2.3.1", publisher="Acme",
                png_path=self.png_path, ico_path=self.ico_path, main_exe="main.exe",
                eula_texts={"zh-TW": "EULA 全文", "en": "EULA full text"}, eula_default_lang="zh-TW",
                dependencies=["vcredist_x64"],
                file_associations=[".xyz"], add_to_path=True, path_target_exe="tools\\cli.exe",
                restart_explorer_on_update=True,
                workspace_dir=self.workspace_dir,
            )

        self.assertEqual(captured["app_name"], "測試應用程式")
        self.assertEqual(captured["folder_name"], "TestApp")
        self.assertNotIn("Setup_TestApp", captured.values())
        self.assertEqual(captured["version"], "2.3.1")
        self.assertEqual(captured["publisher"], "Acme")
        self.assertEqual(captured["eula_texts"], {"zh-TW": "EULA 全文", "en": "EULA full text"})
        self.assertEqual(captured["eula_default_lang"], "zh-TW")
        self.assertEqual(captured["dependencies"], ["vcredist_x64"])
        self.assertEqual(captured["file_associations"], [".xyz"])
        self.assertTrue(captured["add_to_path"])
        self.assertEqual(captured["path_target_exe"], "tools\\cli.exe")
        self.assertTrue(captured["restart_explorer_on_update"])
        self.assertEqual(captured["doc_icon"], "", "沒傳 doc_icon_path 時，設定檔裡的 doc_icon 欄位應該是空字串")

    def test_windows_service_field_written_to_config_verbatim(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        service_config = {"service_name": "MySvc", "exe_relative_path": "app.exe", "start_type": "auto"}
        self._call_build_all(run_side_effect=fake_run, windows_service=service_config)
        self.assertEqual(captured["windows_service"], service_config)

    def test_windows_service_defaults_to_empty_dict(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)
        self.assertEqual(captured["windows_service"], {})

    def test_scheduled_task_field_written_to_config_verbatim(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        task_config = {"task_name": "MyTask", "exe_relative_path": "app.exe", "trigger": "onlogon"}
        self._call_build_all(run_side_effect=fake_run, scheduled_task=task_config)
        self.assertEqual(captured["scheduled_task"], task_config)

    def test_scheduled_task_defaults_to_empty_dict(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)
        self.assertEqual(captured["scheduled_task"], {})

    def test_dependencies_min_version_written_to_config_verbatim(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        min_versions = {"vcredist_x64": "14.30", "dotnet_desktop": "8.0.0"}
        self._call_build_all(run_side_effect=fake_run, dependencies_min_version=min_versions)
        self.assertEqual(captured["dependencies_min_version"], min_versions)

    def test_dependencies_min_version_defaults_to_empty_dict(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)
        self.assertEqual(captured["dependencies_min_version"], {})

    def test_create_restore_point_before_install_written_to_config(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run, create_restore_point_before_install=True)
        self.assertTrue(captured["create_restore_point_before_install"])

    def test_create_restore_point_before_install_defaults_to_false(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)
        self.assertFalse(captured["create_restore_point_before_install"])

    def test_folder_name_falls_back_to_app_name_when_blank(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run, folder_name="")
        self.assertEqual(captured["folder_name"], "測試應用程式")

    def test_custom_install_dir_written_to_config_verbatim(self):
        """custom_install_dir 是原始字串（可能含 %APPDATA% 這類環境變數
        寫法），打包當下不展開——展開要留到安裝端在使用者的電腦上執行時
        才有意義，見 installer_core.py 的 _compute_default_path()。"""
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run, custom_install_dir="%APPDATA%\\MyApp")
        self.assertEqual(captured["custom_install_dir"], "%APPDATA%\\MyApp")

    def test_custom_install_dir_defaults_to_empty_string(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)
        self.assertEqual(captured["custom_install_dir"], "")

    def test_doc_icon_path_produces_named_config_entry(self):
        doc_icon_src = os.path.join(self.app_dir, "custom_doc.ico")
        with open(doc_icon_src, "wb") as f:
            f.write(b"DOC_ICO")
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
                # --add-data 那個路徑一定要固定叫 doc_icon.ico（不管使用者原本檔名叫什麼），
                # 這樣安裝端才能一律用固定名字去查。
                self.assertTrue(any("doc_icon.ico;." in part for part in cmd))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run, doc_icon_path=doc_icon_src)

        self.assertEqual(captured["doc_icon"], "doc_icon.ico")

    def test_doc_icon_temp_file_is_cleaned_up_after_build(self):
        doc_icon_src = os.path.join(self.app_dir, "custom_doc.ico")
        with open(doc_icon_src, "wb") as f:
            f.write(b"DOC_ICO")
        self._call_build_all(doc_icon_path=doc_icon_src)
        self.assertFalse(os.path.exists(os.path.join(self.workspace_dir, "doc_icon.ico")))

    def test_doc_icons_per_extension_produce_named_config_entries_and_add_data(self):
        """.a 跟 .b 用不同 ICO：每個副檔名各自複製一份固定命名的圖示，
        installer_config.json 的 doc_icons 要記對照表，PyInstaller 指令
        要把每一張都內嵌進去。"""
        icon_a_src = os.path.join(self.app_dir, "a.ico")
        icon_b_src = os.path.join(self.app_dir, "b.ico")
        with open(icon_a_src, "wb") as f:
            f.write(b"ICON_A")
        with open(icon_b_src, "wb") as f:
            f.write(b"ICON_B")
        captured = {}
        captured_cmd = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
                captured_cmd["cmd"] = cmd
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(
            run_side_effect=fake_run,
            file_associations=[".a", ".b"],
            doc_icons={".a": icon_a_src, ".b": icon_b_src},
        )

        self.assertEqual(captured["doc_icons"], {".a": "doc_icon_a.ico", ".b": "doc_icon_b.ico"})
        self.assertTrue(any("doc_icon_a.ico;." in part for part in captured_cmd["cmd"]))
        self.assertTrue(any("doc_icon_b.ico;." in part for part in captured_cmd["cmd"]))

    def test_doc_icons_temp_files_are_cleaned_up_after_build(self):
        icon_a_src = os.path.join(self.app_dir, "a.ico")
        with open(icon_a_src, "wb") as f:
            f.write(b"ICON_A")
        self._call_build_all(file_associations=[".a"], doc_icons={".a": icon_a_src})
        self.assertFalse(os.path.exists(os.path.join(self.workspace_dir, "doc_icon_a.ico")))

    def test_no_doc_icons_produces_empty_config_entry(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)
        self.assertEqual(captured["doc_icons"], {})


class TestInstallPasswordProtection(BuildAllTestBase):
    """安裝密碼保護（見 CONTEXT.md「安裝密碼保護」一節）：install_password_env
    有設定時，app_dir 整包加密成一份檔案再內嵌，不直接把明文資料夾塞進
    --add-data；installer_config.json 寫入 password_protected 旗標，供
    installer_core.py 在安裝時知道要不要跳密碼關卡。"""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.app_dir, "main.exe"), "wb") as f:
            f.write(b"fake main exe bytes")

    def test_password_protected_flag_written_to_config_when_set(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.dict(os.environ, {"MY_TEST_BUILD_INSTALL_PW": "hunter2"}):
            self._call_build_all(run_side_effect=fake_run, install_password_env="MY_TEST_BUILD_INSTALL_PW")
        self.assertTrue(captured["password_protected"])

    def test_password_protected_defaults_to_false(self):
        captured = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                with open(os.path.join(self.workspace_dir, "installer_config.json"), encoding="utf-8") as f:
                    captured.update(json.load(f))
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)
        self.assertFalse(captured["password_protected"])

    def test_app_contents_embedded_as_encrypted_file_not_plaintext_folder(self):
        """真實會發生的問題：如果密碼保護開著，卻還是照舊把明文 app_dir
        整包塞進 --add-data，密碼保護形同虛設——這裡鎖住『有密碼保護時，
        --add-data 絕對不能直接指向明文的 app_dir』這件事。"""
        captured_cmd = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                captured_cmd["cmd"] = list(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.dict(os.environ, {"MY_TEST_BUILD_INSTALL_PW": "hunter2"}):
            self._call_build_all(run_side_effect=fake_run, install_password_env="MY_TEST_BUILD_INSTALL_PW")

        app_contents_args = [a for a in captured_cmd["cmd"] if a.startswith("--add-data") and "app_contents" in a]
        self.assertFalse(
            any(a == f"--add-data={self.app_dir};app_contents" for a in app_contents_args),
            "密碼保護開啟時，不應該直接把明文 app_dir 塞進 --add-data",
        )

    def test_encrypted_payload_temp_file_cleaned_up_after_build(self):
        temp_files_during_build = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                temp_files_during_build["files"] = set(os.listdir(self.workspace_dir))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.dict(os.environ, {"MY_TEST_BUILD_INSTALL_PW": "hunter2"}):
            self._call_build_all(run_side_effect=fake_run, install_password_env="MY_TEST_BUILD_INSTALL_PW")

        encrypted_files_during = {f for f in temp_files_during_build["files"] if f.endswith(".enc")}
        self.assertTrue(encrypted_files_during, "編譯當下應該有暫存的加密檔案存在，才能被 --add-data 內嵌")

        remaining_after = {f for f in os.listdir(self.workspace_dir) if f.endswith(".enc")}
        self.assertEqual(remaining_after, set(), "編譯完成後，暫存的加密檔案應該被清乾淨")

    def test_no_password_protection_still_embeds_plaintext_app_dir(self):
        """沒設定 install_password_env 時，行為完全不變——不應該無緣無故
        多一道加密/解密流程。"""
        captured_cmd = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
            else:
                captured_cmd["cmd"] = list(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)
        self.assertIn(f"--add-data={self.app_dir};app_contents", captured_cmd["cmd"])


class TestTempArtifactCleanupOnFailure(BuildAllTestBase):
    """真實抓到的問題（F19）：暫存產物（doc_icon.ico、內嵌的前後置腳本、
    下載下來要內嵌的相依元件安裝檔）原本只有在函式順利跑到最後一段
    「清理暫存中間檔案」才會被刪除——任何一步中途拋例外（版本字串寫錯、
    doc icon 檔案不存在、相依元件下載失敗）都會讓這些暫存檔留在
    workspace_dir 裡，下一輪打包前才會被清掉（如果還記得要清的話）。
    這裡驗證：bundle_dependencies 下載失敗時，前面已經複製好的 doc_icon
    暫存檔還是會被清乾淨，不因為後面的步驟失敗就留下殘骸。"""

    def test_doc_icon_temp_file_cleaned_up_when_bundle_download_fails_later(self):
        doc_icon_src = os.path.join(self.app_dir, "doc.ico")
        with open(doc_icon_src, "wb") as f:
            f.write(b"ICO")
        expected_temp_doc_icon = os.path.join(self.workspace_dir, "doc_icon.ico")

        with mock.patch("builder._download_file", side_effect=OSError("模擬下載失敗")), \
             self.assertRaises(Exception):
            self._call_build_all(
                doc_icon_path=doc_icon_src,
                dependencies=["vcredist_x64"],
                bundle_dependencies=["vcredist_x64"],
            )

        self.assertFalse(
            os.path.exists(expected_temp_doc_icon),
            "doc_icon.ico 暫存檔應該在例外拋出後也被清掉，不能留到下一輪打包才清",
        )


class FakeHttpResponse:
    """模擬 urllib.request.urlopen() 回傳的物件：支援 context manager、
    分塊 read()、以及 getheader("Content-Length")。斷線情境用「宣告的
    Content-Length 比實際內容長」表達——真正的連線中斷就是這個形狀：
    read() 只是回傳空字串正常結束迴圈，不會拋例外。
    """

    def __init__(self, body, declared_length=None):
        self._body = body
        self._pos = 0
        self._declared_length = len(body) if declared_length is None else declared_length

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, size):
        chunk = self._body[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def getheader(self, name):
        if name == "Content-Length":
            return str(self._declared_length)
        return None


class TestDownloadFileVerification(unittest.TestCase):
    """F06：`sha256` 這個欄位在內嵌模式下完全不生效。

    打包端 `_download_file()` 原本只有讀取與寫檔，既沒有 Content-Length
    完整性比對，也沒有 sha256 驗證，內嵌迴圈呼叫它時 `custom_dependencies`
    裡填的 `sha256` 根本沒有被傳進來。安裝端兩項驗證都有，但 sha256 檢查
    位於「需要連線下載」的分支內，走內嵌路徑時整段跳過。

    合併效果：使用者同時填寫 `sha256` 並勾選內嵌時，該檔案從打包到安裝
    沒有任何一個環節驗證過——打包當下網路中斷會把一顆內容截斷的執行檔
    內嵌進 Setup.exe，之後每一位終端使用者都會執行它。
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.dest = os.path.join(self.tmp_dir, "dep.exe")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_writes_the_file_when_everything_matches(self):
        body = b"installer-payload"
        with mock.patch("builder.urllib.request.urlopen", return_value=FakeHttpResponse(body)):
            builder._download_file("https://example.invalid/dep.exe", self.dest)
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), body)

    def test_raises_when_fewer_bytes_arrive_than_content_length(self):
        """連線中途斷掉的形狀：read() 回傳空字串正常結束，不拋例外，
        Content-Length 是唯一能看出內容短少的依據。"""
        resp = FakeHttpResponse(b"trunc", declared_length=9999)
        with mock.patch("builder.urllib.request.urlopen", return_value=resp), \
             self.assertRaises(Exception) as ctx:
            builder._download_file("https://example.invalid/dep.exe", self.dest)
        self.assertIn("9999", str(ctx.exception))

    def test_raises_when_sha256_does_not_match(self):
        body = b"installer-payload"
        wrong = "0" * 64
        with mock.patch("builder.urllib.request.urlopen", return_value=FakeHttpResponse(body)), \
             self.assertRaises(Exception) as ctx:
            builder._download_file(
                "https://example.invalid/dep.exe", self.dest, expected_sha256=wrong,
            )
        # 訊息要說得出「驗證不符」，不能只是任何一個例外——沒有這個斷言的話，
        # 連「函式根本不接受 expected_sha256 參數」的 TypeError 都會讓測試綠燈。
        self.assertIn("sha256", str(ctx.exception).lower())
        self.assertIn("不符", str(ctx.exception))

    def test_accepts_matching_sha256_case_insensitively(self):
        body = b"installer-payload"
        digest = hashlib.sha256(body).hexdigest()
        with mock.patch("builder.urllib.request.urlopen", return_value=FakeHttpResponse(body)):
            builder._download_file(
                "https://example.invalid/dep.exe", self.dest, expected_sha256=digest.upper(),
            )
        self.assertTrue(os.path.exists(self.dest))

    def test_does_not_leave_a_failed_download_behind(self):
        """驗證失敗時不能留下那顆檔案——留著等於下一步的 --add-data 仍然
        可能把它內嵌進去（呼叫端目前會中止，但這裡不依賴呼叫端的行為）。"""
        resp = FakeHttpResponse(b"trunc", declared_length=9999)
        with mock.patch("builder.urllib.request.urlopen", return_value=resp), \
             self.assertRaises(Exception):
            builder._download_file("https://example.invalid/dep.exe", self.dest)
        self.assertFalse(os.path.exists(self.dest))


class TestBundleDependenciesPassesSha256(BuildAllTestBase):
    """F06 的另一半：內嵌迴圈要把該相依元件設定裡的 `sha256` 傳給
    `_download_file()`。欄位存在、驗證函式也存在，但兩者之間沒有接線，
    等於使用者填的 sha256 在內嵌模式下是一個裝飾。"""

    def test_custom_dependency_sha256_is_passed_to_the_downloader(self):
        digest = "a" * 64
        captured = {}

        def fake_download(url, dest_path, timeout=60, expected_sha256=None):
            captured["url"] = url
            captured["expected_sha256"] = expected_sha256
            with open(dest_path, "wb") as f:
                f.write(b"FAKE_DEP")

        with mock.patch("builder._download_file", side_effect=fake_download):
            self._call_build_all(
                custom_dependencies=[{
                    "key": "mydep", "display_name": "My Dep",
                    "download_url": "https://example.invalid/mydep.exe",
                    "silent_args": ["/S"], "sha256": digest,
                }],
                dependencies=["mydep"],
                bundle_dependencies=["mydep"],
            )

        self.assertEqual(captured.get("expected_sha256"), digest)

    def test_dependency_without_sha256_passes_none(self):
        captured = {}

        def fake_download(url, dest_path, timeout=60, expected_sha256=None):
            captured["expected_sha256"] = expected_sha256
            with open(dest_path, "wb") as f:
                f.write(b"FAKE_DEP")

        with mock.patch("builder._download_file", side_effect=fake_download):
            self._call_build_all(
                dependencies=["vcredist_x64"],
                bundle_dependencies=["vcredist_x64"],
            )

        self.assertIsNone(captured.get("expected_sha256"))


class TestErrorPaths(BuildAllTestBase):
    def test_missing_ui_dir_raises(self):
        shutil.rmtree(os.path.join(self.workspace_dir, "ui"))
        with self.assertRaises(Exception):
            self._call_build_all()

    def test_missing_installer_core_raises(self):
        os.remove(os.path.join(self.workspace_dir, "installer_core.py"))
        with self.assertRaises(Exception):
            self._call_build_all()

    def test_uninstall_compile_failure_raises_with_output(self):
        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                return mock.Mock(returncode=1, stdout="some pyinstaller output", stderr="boom")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with self.assertRaises(Exception) as ctx:
            self._call_build_all(run_side_effect=fake_run)
        self.assertIn("反安裝程式編譯失敗", str(ctx.exception))

    def test_installer_compile_failure_raises_with_output(self):
        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE")
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="main build failed")

        with self.assertRaises(Exception) as ctx:
            self._call_build_all(run_side_effect=fake_run)
        self.assertIn("主安裝檔編譯打包失敗", str(ctx.exception))

    def test_stale_dist_and_build_dirs_are_cleared_first(self):
        stale_dist = os.path.join(self.workspace_dir, "dist")
        os.makedirs(stale_dist)
        with open(os.path.join(stale_dist, "leftover_from_previous_build.exe"), "wb") as f:
            f.write(b"STALE")

        self._call_build_all()

        self.assertFalse(
            os.path.exists(os.path.join(stale_dist, "leftover_from_previous_build.exe")),
            "每次重新編譯前應該清掉上一輪殘留的 dist 產物，避免混淆",
        )


class TestUninstallCompileFlags(BuildAllTestBase):
    """uninstall.exe 現在也是 pywebview 視窗化程式（見 ui/uninstall.html，
    取代原本純 console + 原生 MessageBoxW 的介面），編譯指令要對應調整：
    --noconsole 移除黑底命令提示字元視窗、掛載 ui 資料夾讓它找得到
    uninstall.html、跟主安裝檔一樣排除用不到的 pywebview 替代 GUI 後端。
    """

    def test_uninstall_cmd_has_noconsole_and_ui_data(self):
        captured_cmds = []

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            captured_cmds.append(cmd)
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE_UNINSTALL_EXE")
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run)

        uninstall_cmd = next(cmd for cmd in captured_cmds if "uninstall.py" in cmd)
        self.assertIn("--noconsole", uninstall_cmd)
        self.assertIn("--add-data=ui;ui", uninstall_cmd)
        self.assertIn("--exclude-module=PyQt5", uninstall_cmd)

    def test_both_pyinstaller_calls_include_version_file_with_distinct_description(self):
        captured_cmds = []
        captured_contents = {}

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            captured_cmds.append(cmd)
            version_file_flag = next(arg for arg in cmd if arg.startswith("--version-file="))
            version_file_path = version_file_flag.split("=", 1)[1]
            with open(version_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "uninstall.py" in cmd:
                captured_contents["uninstall"] = content
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE_UNINSTALL_EXE")
            else:
                captured_contents["main"] = content
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run, app_name="測試應用程式", version="2.3.1", publisher="Acme")

        self.assertIn("StringStruct('ProductName', '測試應用程式')", captured_contents["main"])
        self.assertIn("StringStruct('CompanyName', 'Acme')", captured_contents["main"])
        self.assertIn("filevers=(2, 3, 1, 0)", captured_contents["main"])
        self.assertIn("StringStruct('FileDescription', '測試應用程式')", captured_contents["main"])
        self.assertIn("StringStruct('FileDescription', 'Uninstall 測試應用程式')", captured_contents["uninstall"])

    def test_invalid_version_string_raises_before_compiling(self):
        with self.assertRaises(Exception):
            self._call_build_all(version="not-a-version")

    def test_uninstall_cmd_omits_uac_admin_when_no_admin_install(self):
        captured_cmds = []

        def fake_run(cmd, cwd=None, creationflags=0, capture_output=True, text=True):
            captured_cmds.append(cmd)
            if "uninstall.py" in cmd:
                os.makedirs(self.dist_dir, exist_ok=True)
                with open(os.path.join(self.dist_dir, "uninstall.exe"), "wb") as f:
                    f.write(b"FAKE_UNINSTALL_EXE")
            return mock.Mock(returncode=0, stdout="", stderr="")

        self._call_build_all(run_side_effect=fake_run, no_admin_install=True)

        uninstall_cmd = next(cmd for cmd in captured_cmds if "uninstall.py" in cmd)
        self.assertNotIn("--uac-admin", uninstall_cmd)
        self.assertIn("--noconsole", uninstall_cmd)


class TestMissingUninstallHtmlRaises(BuildAllTestBase):
    def test_missing_uninstall_html_raises(self):
        os.remove(os.path.join(self.workspace_dir, "ui", "uninstall.html"))
        with self.assertRaises(Exception) as ctx:
            self._call_build_all()
        self.assertIn("uninstall.html", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
