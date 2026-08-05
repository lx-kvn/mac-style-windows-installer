"""InstallScope — no_admin_install 這個布林值衍生出的「該用哪個登錄表 hive、
哪個捷徑/PATH 基準目錄」判斷，`installer_core.py`（安裝端）跟 `uninstall.py`
（解除安裝端）各自獨立重新推導過一次（見兩邊原本各自的 if/else 分支），
是同一個概念在兩個檔案裡各自長出一份非正式實作。這裡收成一個地方，兩邊
共用同一份規則，之後這個規則要調整（例如 no_admin_install 底下的捷徑
位置要改）只要改一個地方。

`no_admin_install` 開啟時代表整個安裝流程（含解除安裝）完全不要求系統
管理員權限，所有「原本要寫系統層級/所有使用者共用位置」的地方都要改成
「寫使用者自己的、本來就有寫入權限的位置」：
  - 登錄表：HKEY_LOCAL_MACHINE -> HKEY_CURRENT_USER
  - PATH：機器層級的 Environment 機碼 -> 使用者層級的 Environment 機碼
  - 捷徑：Public Desktop/ProgramData 開始功能表 -> 使用者自己的桌面/開始功能表
  - 預設安裝路徑：Program Files -> %LOCALAPPDATA%\\Programs\\<folder_name>
"""

import os


def local_appdata_root(folder_name):
    """`%LOCALAPPDATA%\\Programs\\<folder_name>`——不管 no_admin_install 有沒有
    開啟都可能用到這個路徑（例如 `local_appdata_files` 指定的個別檔案，即使
    主程式仍在 Program Files，也可能被改裝到這裡），跟 no_admin_install 是
    兩個獨立但常常一起出現的概念，所以是一個獨立的純函式，不掛在
    `InstallScope` 底下。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    return os.path.join(base, "Programs", folder_name)


class InstallScope:
    """把 no_admin_install 這一個布林值，翻譯成安裝/解除安裝端各自需要的
    hive、目錄。呼叫端只要問「這個情境下要用哪個 hive/目錄」，不用自己
    重新判斷 if no_admin_install。

    `registry` 參數：跟 `file_assoc.py` 的 register()/unregister() 同一個
    「registry seam」（見 CONTEXT.md）——預設 None 時在每次存取當下才
    `import winreg`（installer_core.py 的測試用
    `mock.patch.dict(sys.modules, {"winreg": fake})` 這種方式 patch，
    需要每次都重新 import 才吃得到）；但 `uninstall.py` 是在檔案最上面
    `import winreg` 一次，測試改用 `mock.patch.object(un, "winreg", fake)`
    直接換掉那個模組屬性——這種情境下呼叫端要把自己那個（可能已經被
    patch 掉的）`winreg` 名字傳進來，不能讓這裡自己 import 一份新的、
    絕對不會被那種 patch 方式影響到的「真的」winreg。"""

    def __init__(self, no_admin_install, registry=None):
        self.no_admin_install = bool(no_admin_install)
        self._registry = registry

    def _winreg(self):
        if self._registry is not None:
            return self._registry
        import winreg
        return winreg

    @property
    def registry_hive(self):
        """解除安裝登錄表項目 / 版本偵測要開的 hive。"""
        winreg = self._winreg()
        return winreg.HKEY_CURRENT_USER if self.no_admin_install else winreg.HKEY_LOCAL_MACHINE

    @property
    def path_env_hive_and_key(self):
        """加入/移除 PATH 時要開的 (hive, sub_key)。"""
        winreg = self._winreg()
        if self.no_admin_install:
            return winreg.HKEY_CURRENT_USER, "Environment"
        return winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def shortcut_dir(self, desktop=False):
        """開始功能表 / 桌面捷徑要放的資料夾。"""
        if self.no_admin_install:
            if desktop:
                return os.path.join(os.path.expanduser("~"), "Desktop")
            return os.path.join(
                os.environ.get("APPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Roaming")),
                "Microsoft", "Windows", "Start Menu", "Programs",
            )
        if desktop:
            return "C:\\Users\\Public\\Desktop"
        return os.path.join(
            os.environ.get("ProgramData", "C:\\ProgramData"),
            "Microsoft", "Windows", "Start Menu", "Programs",
        )

    def default_install_root(self, app_name, folder_name=None):
        """預設安裝路徑：no_admin_install 開啟時是使用者層級的
        %LOCALAPPDATA%\\Programs\\<folder>，否則是 Program Files\\<folder>。"""
        folder = folder_name or app_name
        if self.no_admin_install:
            return local_appdata_root(folder)
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        return os.path.join(program_files, folder)
