"""共用的假物件，讓測試不需要真的動這台機器的登錄表/系統資源就能跑。"""


class FakeWinReg:
    """用一個巢狀 dict 模擬登錄表。CreateKey/OpenKey/SetValueEx/QueryValueEx/DeleteKey
    呼叫都紀錄在 self.store 裡，可以斷言「最後登錄表裡實際上寫了什麼」。
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
        self.store.setdefault(subkey, {})
        return _FakeKeyCtx(self, subkey)

    def OpenKey(self, hive, subkey, *args, **kwargs):
        self._maybe_fail(subkey)
        if subkey not in self.store:
            raise FileNotFoundError(subkey)
        return _FakeKeyCtx(self, subkey)

    def DeleteKey(self, hive, subkey):
        if subkey not in self.store:
            raise FileNotFoundError(subkey)
        if self.store[subkey]:
            # 真正的 winreg：機碼底下還有子機碼時不能刪，模擬同樣的限制，
            # 這樣才能驗證呼叫端有沒有照正確順序先刪子機碼。
            children = [k for k in self.store if k != subkey and k.startswith(subkey + "\\")]
            if children:
                raise OSError(f"{subkey} 底下還有子機碼，無法刪除")
        del self.store[subkey]

    def SetValueEx(self, key_ctx, name, reserved, value_type, value):
        self.store[key_ctx.subkey][name] = value

    def QueryValueEx(self, key_ctx, name):
        values = self.store.get(key_ctx.subkey, {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], self.REG_SZ

    def CloseKey(self, key_ctx):
        pass


class _FakeKeyCtx:
    def __init__(self, reg, subkey):
        self.reg = reg
        self.subkey = subkey

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
