"""共用的假物件，讓測試不需要真的動這台機器的登錄表/系統資源就能跑。"""


class FakeWinReg:
    """用一個巢狀 dict 模擬登錄表。CreateKey/OpenKey/SetValueEx/QueryValueEx/DeleteKey
    呼叫都紀錄在 self.store 裡，可以斷言「最後登錄表裡實際上寫了什麼」。

    store 的 key 是 (hive, subkey) tuple：HKLM 跟 HKCU 是兩個完全獨立的登錄表分支，
    同樣的相對路徑（例如 "Software\\Classes\\.xyz"）在兩個 hive 底下是各自獨立的
    機碼，不能共用同一個 dict key，不然會互相覆蓋、測不出「HKCU 覆寫優先於 HKLM」
    這類跟 hive 有關的真實行為。
    """

    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1
    REG_DWORD = 4
    REG_EXPAND_SZ = 2
    KEY_ALL_ACCESS = 0xF003F
    # 唯讀開啟：呼叫端在「還不確定這個位置有沒有東西要移除」時先用它探一次，
    # 確定有才用 KEY_ALL_ACCESS 重開來寫（見 system_entries.remove_from_path()）。
    KEY_READ = 0x20019

    def __init__(self):
        self.store = {}
        self.fail_on_substring = None  # CreateKey/OpenKey 對到含這個子字串的路徑會丟例外
        self.fail_on_value_name = None  # SetValueEx 寫到這個值名稱時丟例外，模擬寫到一半失敗

    def _maybe_fail(self, subkey):
        if self.fail_on_substring and self.fail_on_substring in subkey:
            raise PermissionError(f"模擬權限不足，無法存取 {subkey}")

    def CreateKey(self, hive, subkey):
        self._maybe_fail(subkey)
        self.store.setdefault((hive, subkey), {})
        return _FakeKeyCtx(self, hive, subkey)

    def OpenKey(self, hive, subkey, *args, **kwargs):
        self._maybe_fail(subkey)
        if (hive, subkey) not in self.store:
            raise FileNotFoundError(subkey)
        return _FakeKeyCtx(self, hive, subkey)

    def DeleteKey(self, hive, subkey):
        if (hive, subkey) not in self.store:
            raise FileNotFoundError(subkey)
        if self.store[(hive, subkey)]:
            # 真正的 winreg：機碼底下還有子機碼時不能刪，模擬同樣的限制，
            # 這樣才能驗證呼叫端有沒有照正確順序先刪子機碼。
            children = [
                k for k in self.store
                if k != (hive, subkey) and k[0] == hive and k[1].startswith(subkey + "\\")
            ]
            if children:
                raise OSError(f"{subkey} 底下還有子機碼，無法刪除")
        del self.store[(hive, subkey)]

    def SetValueEx(self, key_ctx, name, reserved, value_type, value):
        if self.fail_on_value_name and name == self.fail_on_value_name:
            raise OSError(f"模擬寫入 {name} 時失敗")
        self.store[(key_ctx.hive, key_ctx.subkey)][name] = value

    def QueryValueEx(self, key_ctx, name):
        values = self.store.get((key_ctx.hive, key_ctx.subkey), {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], self.REG_SZ

    def CloseKey(self, key_ctx):
        pass

    def EnumKey(self, key_ctx, index):
        """列出 key_ctx 底下直接子機碼的名稱（依 index 逐一取出），模擬真正
        winreg.EnumKey()：index 超出範圍時拋 OSError（.NET Desktop Runtime
        的版本偵測要靠 EnumKey 列出 InstalledVersions\\...\\sharedfx\\...
        底下那些以版本號命名的子機碼）。"""
        prefix = key_ctx.subkey + "\\"
        children = sorted({
            subkey[len(prefix):].split("\\")[0]
            for (hive, subkey) in self.store
            if hive == key_ctx.hive and subkey.startswith(prefix) and subkey != key_ctx.subkey
        })
        if index >= len(children):
            raise OSError("no more data")
        return children[index]

    def hklm(self, subkey):
        """測試用的便捷存取子：讀/寫 HKEY_LOCAL_MACHINE 底下某個機碼目前的值。"""
        return self.store.get((self.HKEY_LOCAL_MACHINE, subkey))

    def hkcu(self, subkey):
        """測試用的便捷存取子：讀/寫 HKEY_CURRENT_USER 底下某個機碼目前的值。"""
        return self.store.get((self.HKEY_CURRENT_USER, subkey))

    def set_hklm(self, subkey, values):
        self.store[(self.HKEY_LOCAL_MACHINE, subkey)] = values

    def set_hkcu(self, subkey, values):
        self.store[(self.HKEY_CURRENT_USER, subkey)] = values


class _FakeKeyCtx:
    def __init__(self, reg, hive, subkey):
        self.reg = reg
        self.hive = hive
        self.subkey = subkey

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def write_test_png(path, width=256, height=256):
    """寫一張真的 PNG 到指定路徑，回傳該路徑。

    測試資料原本多半是 `b"fake"` 之類的位元組——副檔名對、內容不對。那在
    只檢查副檔名的年代沒問題，但 MSIX 模式會實際讀圖片的尺寸（正方形、
    邊長下限，見 png_size.py），假內容會被正確地擋下來。與其在每個測試各自
    造一張，共用這一個。

    不引進 Pillow：PNG 的最小合法結構就是簽章加三個區塊，用標準函式庫寫得出來，
    而本專案刻意不帶影像處理相依（第五輪決議第一項）。
    """
    import os
    import struct
    import zlib

    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * width for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1))
            + chunk(b"IEND", b""))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as f:
        f.write(blob)
    return path


def run_threads_synchronously():
    """`threading.Thread` 的替身：`start()` 當場把 target 跑完，不另開執行緒。

    真實抓到的缺陷：`gui_config.ConfigAPI.start_pack()` 起一個背景執行緒後
    立刻回傳，測試裡的 `with mock.patch(...)` 區塊隨即結束、替身被撤掉，
    那個執行緒接著呼叫到**真正的** `builder.build_all()`——於是測試在 repo
    根目錄寫出 `installer_config.json` 並真的去叫 pyinstaller。留下來的那個
    檔案會被後續讀取 `installer_config.json` 的測試撈到，造成與執行順序相依
    的失敗（`InstallerAPI()` 在建構時就會讀它）。

    這種失敗很難追：出問題的測試與寫出檔案的測試在不同檔案，而且單獨跑
    兩者都會通過。用法：

        with mock.patch("gui_config.threading.Thread",
                        side_effect=run_threads_synchronously()):
            api.start_pack(data)

    這樣打包在 `with` 區塊內就跑完了，替身仍然有效。
    """
    from unittest import mock

    def factory(target=None, args=(), kwargs=None, **_ignored):
        thread = mock.Mock()
        thread.start.side_effect = lambda: target(*args, **(kwargs or {}))
        return thread

    return factory


def make_installer_api(**overrides):
    """建立一個與工作目錄無關的 `installer_core.InstallerAPI`，再覆寫指定欄位。

    `InstallerAPI.__init__()` 會呼叫 `load_config()`，而後者讀的是工作目錄裡的
    `installer_config.json`（未凍結時 `get_resource_path()` fallback 到 cwd）。
    那是產品的正確行為，但會讓測試受工作目錄的殘留檔案擺布——真實發生過：
    某個測試在 repo 根目錄留下一份設定檔，另一個檔案裡的測試因此算出別的
    安裝路徑而失敗，兩者單獨跑都會過。

    這裡在建構期間把 `get_resource_path` 換成指向一個不存在的路徑，讓
    `load_config()` 什麼都讀不到，欄位維持 `__init__` 的預設值。

    先前每個測試檔各自有一份同名 helper，其說明寫著「繞開 load_config() 對
    磁碟檔案的依賴」，但實作只是 `InstallerAPI()` 加 setattr，從未真的繞開。
    """
    import os
    from unittest import mock
    import installer_core

    def nowhere(name):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_no_such_resource_dir", name)

    with mock.patch.object(installer_core, "get_resource_path", side_effect=nowhere):
        api = installer_core.InstallerAPI()
    for key, value in overrides.items():
        setattr(api, key, value)
    return api
