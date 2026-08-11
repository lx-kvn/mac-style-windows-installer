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

    def __init__(self):
        self.store = {}
        self.fail_on_substring = None  # CreateKey/OpenKey 對到含這個子字串的路徑會丟例外

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
