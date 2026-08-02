"""builder.py 的 build_all() 測試。

全程用假的 subprocess.run 頂替真正的 pyinstaller 呼叫（這台機器上跑一次真的
編譯要數十秒，而且測試不應該依賴外部工具是否安裝），驗證的是「這個函式組出
來的設定檔內容、呼叫順序、錯誤處理」這幾件事，不是編譯本身。
"""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
