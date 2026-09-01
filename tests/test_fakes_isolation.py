"""測試 helper 自己的隔離性：不受工作目錄裡殘留的檔案影響。

真實抓到的缺陷，兩個疊在一起：

1. `tests/test_file_associations.py` 有一個測試讓 `start_pack()` 起的背景
   執行緒逃出 `with mock.patch(...)` 區塊，那個執行緒接著呼叫到**真正的**
   `builder.build_all()`，在 repo 根目錄寫出 `installer_config.json`。
2. `make_installer_api()` 的說明寫著「不需要真的讀 installer_config.json、
   繞開 load_config() 對磁碟檔案的依賴」，但實作只是 `InstallerAPI()` 加上
   setattr——而 `InstallerAPI.__init__()` 會呼叫 `load_config()`，那個方法
   讀的正是工作目錄裡的 `installer_config.json`。說明描述的是意圖，實作
   從未實現。

於是第 1 點留下的檔案被第 2 點撈走，造成與執行順序相依的失敗：兩個測試
在不同檔案，各自單獨跑都會過，合起來跑才會壞，而錯誤訊息（某個路徑算出
`TestApp` 而不是 `MyApp`）完全不指向真正的成因。

這裡釘住的是第 2 點——helper 必須真的與工作目錄的殘留無關。第 1 點的修正
在 `tests/_fakes.run_threads_synchronously()`。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from _fakes import make_installer_api


class StrayConfigInTheWorkingDirectoryIsIgnored(unittest.TestCase):
    """`InstallerAPI()` 建構時就會讀工作目錄的 installer_config.json——那是
    產品的正確行為（未凍結時工作目錄就是資源目錄），但測試不該受它擺布。"""

    def setUp(self):
        self.stray = os.path.join(REPO_ROOT, "installer_config.json")
        self.backup = None
        if os.path.exists(self.stray):
            self.backup = self.stray + ".test_backup"
            shutil.move(self.stray, self.backup)
        self.addCleanup(self._restore)
        with open(self.stray, "w", encoding="utf-8") as f:
            json.dump({
                "app_name": "StrayApp",
                "folder_name": "StrayFolder",
                "version": "9.9.9",
                "publisher": "StrayPublisher",
                "main_exe": "stray.exe",
            }, f)

    def _restore(self):
        if os.path.exists(self.stray):
            os.remove(self.stray)
        if self.backup and os.path.exists(self.backup):
            shutil.move(self.backup, self.stray)

    def test_an_unspecified_field_does_not_come_from_the_stray_file(self):
        """沒有指定的欄位要是乾淨的預設值，不是殘留檔案裡的值。原本的失敗
        正是這樣發生的：測試指定了 app_name 卻沒指定 folder_name，於是
        folder_name 從殘留檔案來，算出來的安裝路徑用了別的名字。"""
        api = make_installer_api(app_name="MyApp")
        self.assertNotEqual(api.folder_name, "StrayFolder")

    def test_the_specified_fields_still_win(self):
        api = make_installer_api(app_name="MyApp", folder_name="MyFolder")
        self.assertEqual(api.app_name, "MyApp")
        self.assertEqual(api.folder_name, "MyFolder")

    def test_the_stray_file_does_not_reach_any_field(self):
        api = make_installer_api()
        for field in ("app_name", "folder_name", "version", "publisher", "main_exe"):
            self.assertNotIn("Stray", str(getattr(api, field, "")),
                             f"{field} 來自工作目錄的殘留檔案")


class ThreadsRunInsideThePatch(unittest.TestCase):
    """替身在 with 區塊結束時就被撤掉；打包執行緒必須在那之前跑完，否則它
    會呼叫到真正的 build_all()。"""

    def test_start_runs_the_target_immediately(self):
        from _fakes import run_threads_synchronously
        ran = []
        factory = run_threads_synchronously()
        thread = factory(target=lambda a, b: ran.append((a, b)), args=(1, 2))
        self.assertEqual(ran, [], "還沒 start() 就跑了")
        thread.start()
        self.assertEqual(ran, [(1, 2)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
