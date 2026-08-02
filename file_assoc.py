"""
file_assoc.py
--------------
副檔名關聯的登錄表操作：安裝時註冊（register）、解除安裝時反註冊
（unregister）。這兩件事原本分別是 installer_core.py 的
_register_file_associations() 跟 uninstall.py 的 remove_file_associations()，
各自維護一份幾乎一樣的機碼清單，只靠命名慣例跟註解維持對稱——修過幾輪
UserChoice/HKCU 覆寫/OpenWithProgids 殘留清除的 bug 之後，這個「兩邊手動
對齊」的協定已經漏改過，所以收斂成這一份共用實作，讓安裝寫了什麼、解除安裝
就對稱地清掉什麼，變成同一份程式碼，而不是兩份要手動對齊的清單。

`registry` 參數預設是真正的 winreg 模組；測試可以換成
tests/_fakes.py 的 FakeWinReg（介面跟 winreg 一致：CreateKey/OpenKey/
SetValueEx/QueryValueEx/DeleteKey/CloseKey），不需要 monkeypatch
sys.modules 或模組屬性就能注入假的登錄表。
"""
import ctypes
import winreg as _real_winreg


def prog_id(ext):
    """副檔名 -> ProgID 的命名慣例，register()/unregister() 都靠這個對齊。"""
    return f"AppFile{ext.replace('.', '')}"


def register(extensions, main_exe_path, app_name, icon_refs, log=None, registry=_real_winreg):
    """把 extensions 清單裡每個副檔名關聯到 main_exe_path。

    icon_refs：字典 {副檔名: DefaultIcon 要寫的值}，讓每個副檔名可以各自
    設定不同的圖示（例如 .a 跟 .b 用不同 ICO）——每個副檔名本來就有自己
    獨立的 ProgID（見 prog_id()），DefaultIcon 是掛在 ProgID 底下的子機碼，
    天生就互不影響，這裡只是把「所有副檔名共用同一個圖示」這個呼叫端假設
    拿掉。缺少某個副檔名的對應項目時，退回 "{main_exe_path},0"（沿用主
    程式圖示），呼叫端（installer_core.py）通常會先把「共用圖示」/「沒設
    定就用主程式圖示」這幾層 fallback 算好、每個副檔名都給一個值，這裡的
    退回只是最後一道保險。

    不吞例外：ProgID/DefaultIcon/command 這幾個核心機碼寫失敗會直接往外拋，
    交給呼叫端判斷是否要整個安裝失敗回滾，不要讓使用者以為關聯成功了。
    後段清除使用者先前手動設定殘留的部分是盡量做，個別失敗不影響整體。
    """
    if not extensions or not main_exe_path:
        return
    for ext in extensions:
        pid = prog_id(ext)
        icon_ref = icon_refs.get(ext, f"{main_exe_path},0")
        with registry.CreateKey(registry.HKEY_LOCAL_MACHINE, f"Software\\Classes\\{ext}") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, pid)
        with registry.CreateKey(registry.HKEY_LOCAL_MACHINE, f"Software\\Classes\\{pid}") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, f"{app_name} File")
        with registry.CreateKey(registry.HKEY_LOCAL_MACHINE, f"Software\\Classes\\{pid}\\shell\\open\\command") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, f'"{main_exe_path}" "%1"')
        with registry.CreateKey(registry.HKEY_LOCAL_MACHINE, f"Software\\Classes\\{pid}\\DefaultIcon") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, icon_ref)

        _clear_stale_user_associations(ext, registry, log)

    _notify_association_changed()


def unregister(extensions, registry=_real_winreg):
    """解除安裝：對稱地清掉 register() 寫過的所有機碼，不留殘骸。"""
    for ext in extensions:
        pid = prog_id(ext)
        # DefaultIcon 是 shell 的平行子機碼，DeleteKey 要求目標本身沒有子機碼
        # 才能刪除，所以要在刪 {pid} 本體之前先把它跟 shell\open\command 清掉，
        # 不然最後一步會因為底下還有東西而刪不掉，留下殘留機碼。
        for reg_path in (
            f"Software\\Classes\\{ext}",
            f"Software\\Classes\\{pid}\\shell\\open\\command",
            f"Software\\Classes\\{pid}\\shell\\open",
            f"Software\\Classes\\{pid}\\shell",
            f"Software\\Classes\\{pid}\\DefaultIcon",
            f"Software\\Classes\\{pid}",
        ):
            try:
                registry.DeleteKey(registry.HKEY_LOCAL_MACHINE, reg_path)
            except Exception:
                pass

        _clear_stale_user_associations(ext, registry, log=None)

    _notify_association_changed()


def _clear_stale_user_associations(ext, registry, log):
    """清掉這個副檔名在使用者個人層級（HKCU）殘留的幾個地方，讓新關聯真的生效。

    Windows 8 之後，只要使用者曾經手動選過（或系統自動選過）這個副檔名的
    預設開啟程式，就會在目前使用者的 HKCU 底下留下好幾層殘留，Explorer 之後
    只認這些殘留、完全不看我們寫的 HKLM 關聯——不清掉的話，前面登錄表寫得再對，
    雙擊檔案還是會開使用者之前選的舊程式，使用者會誤以為「關聯沒有生效」。
    這整組都是盡量做：這個副檔名如果原本就沒有這些殘留（最常見的情況，例如
    全新副檔名），或目前使用者帳號權限不足，都不該讓整個檔案關聯被判定失敗。

    三層殘留，分別是：
      1. FileExts\\<ext>\\UserChoice —— 帶雜湊保護的「使用者選擇」機碼，
         Explorer 解析雙擊要開哪個程式時的最高優先權。
      2. Software\\Classes\\<ext> —— per-user 的關聯覆寫（含 OpenWithProgids
         子機碼），在傳統 HKEY_CLASSES_ROOT 合併規則裡優先權高於 HKLM。
      3. FileExts\\<ext>\\OpenWithProgids / OpenWithList —— 跟第 2 點的
         Software\\Classes\\<ext>\\OpenWithProgids 是不同的機碼路徑，是餵給
         「選取應用程式以開啟」對話框「建議的應用程式」清單用的，重複測試、
         換過命名方式的舊 ProgID 會一直累積在這裡。
    """
    try:
        registry.DeleteKey(
            registry.HKEY_CURRENT_USER,
            rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice",
        )
        if log:
            log(f"已清除 {ext} 先前手動設定的預設開啟程式，改用新安裝的關聯")
    except Exception:
        pass

    try:
        registry.DeleteKey(registry.HKEY_CURRENT_USER, rf"Software\Classes\{ext}\OpenWithProgids")
    except Exception:
        pass
    try:
        registry.DeleteKey(registry.HKEY_CURRENT_USER, rf"Software\Classes\{ext}")
        if log:
            log(f"已清除 {ext} 在使用者個人層級（HKCU）殘留的關聯覆寫")
    except Exception:
        pass

    try:
        registry.DeleteKey(
            registry.HKEY_CURRENT_USER,
            rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithProgids",
        )
    except Exception:
        pass
    try:
        registry.DeleteKey(
            registry.HKEY_CURRENT_USER,
            rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithList",
        )
    except Exception:
        pass


def _notify_association_changed():
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass
