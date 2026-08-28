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

from install_scope import InstallScope


def prog_id(ext):
    """副檔名 -> ProgID 的命名慣例，register()/unregister() 都靠這個對齊。"""
    return f"AppFile{ext.replace('.', '')}"


def register(extensions, main_exe_path, app_name, icon_refs, log=None, registry=_real_winreg, no_admin_install=False):
    """把 extensions 清單裡每個副檔名關聯到 main_exe_path。

    icon_refs：字典 {副檔名: DefaultIcon 要寫的值}，讓每個副檔名可以各自
    設定不同的圖示（例如 .a 跟 .b 用不同 ICO）——每個副檔名本來就有自己
    獨立的 ProgID（見 prog_id()），DefaultIcon 是掛在 ProgID 底下的子機碼，
    天生就互不影響，這裡只是把「所有副檔名共用同一個圖示」這個呼叫端假設
    拿掉。缺少某個副檔名的對應項目時，退回 "{main_exe_path},0"（沿用主
    程式圖示），呼叫端（installer_core.py）通常會先把「共用圖示」/「沒設
    定就用主程式圖示」這幾層 fallback 算好、每個副檔名都給一個值，這裡的
    退回只是最後一道保險。

    no_admin_install：真實抓到的 bug——這四個核心寫入點原本一律硬寫
    HKEY_LOCAL_MACHINE，是這個專案裡唯一沒有接上 InstallScope seam 的
    登錄表寫入點。no_admin_install=True 時整個安裝流程刻意不要求提權，
    但寫 HKLM\\Software\\Classes 一般使用者帳號寫不進去，而下面「不吞
    例外」的設計會讓這個 PermissionError 直接讓整個安裝失敗回滾——等於
    「免管理員權限安裝」跟「檔案關聯」這兩個選項只要同時打開就必定失敗。
    改成跟 system_entries.py 同一種模式：no_admin_install=True 時改寫
    HKEY_CURRENT_USER（Windows 的 HKEY_CLASSES_ROOT 合併規則本來就會納入
    HKCU\\Software\\Classes，效果對等，且完全不需要任何權限）。

    不吞例外：ProgID/DefaultIcon/command 這幾個核心機碼寫失敗會直接往外拋，
    交給呼叫端判斷是否要整個安裝失敗回滾，不要讓使用者以為關聯成功了。
    後段清除使用者先前手動設定殘留的部分是盡量做，個別失敗不影響整體。
    """
    if not extensions or not main_exe_path:
        return
    hive = InstallScope(no_admin_install, registry=registry).registry_hive
    for ext in extensions:
        pid = prog_id(ext)
        icon_ref = icon_refs.get(ext, f"{main_exe_path},0")
        with registry.CreateKey(hive, f"Software\\Classes\\{ext}") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, pid)
        with registry.CreateKey(hive, f"Software\\Classes\\{pid}") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, f"{app_name} File")
        with registry.CreateKey(hive, f"Software\\Classes\\{pid}\\shell\\open\\command") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, f'"{main_exe_path}" "%1"')
        with registry.CreateKey(hive, f"Software\\Classes\\{pid}\\DefaultIcon") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, icon_ref)

        _clear_stale_user_associations(ext, registry, log, just_wrote_hive=hive)

    _notify_association_changed()


def unregister(extensions, registry=_real_winreg, no_admin_install=False):
    """解除安裝：對稱地清掉 register() 寫過的所有機碼，不留殘骸。

    no_admin_install 必須跟當初 register() 用的值一致，才能找到當初真正
    寫入的那個 hive——見 register() 文件字串。

    真實抓到的 bug：這裡原本也呼叫 _clear_stale_user_associations()——
    那個函式存在的理由是「讓 register() 剛寫入的新關聯，在 HKCU 覆寫
    層級之下也真的生效」，解除安裝當下沒有要寫入任何新關聯，這組清除
    完全沒有正當理由執行。它清的 UserChoice/HKCU Software\\Classes\\<ext>
    這幾個位置，隨時可能已經是使用者事後手動改選、或另一個完全無關的
    應用程式寫入的內容——解除安裝我們自己的應用程式，不應該連帶清掉
    使用者事後的選擇，或另一個應用程式的關聯。套用刪除測試：拿掉這個
    呼叫，複雜度不會在別處重新出現（跟 register() 那邊「必須清掉才能讓
    新關聯生效」的理由完全不同），純粹是移除一個做過頭的動作。

    回傳值（F02）：這個函式原本對每一次 DeleteKey 都 try/except: pass
    且不回傳任何值，uninstall.py 的檔案關聯移除步驟因此無條件記錄成功，
    實際失敗完全沒有出口。改成回傳布林值，語義跟 system_entries.py 的
    移除原語一致——機碼本來就不存在（DeleteKey 拋 FileNotFoundError）
    視為成功，只有 FileNotFoundError 以外的例外（權限不足、底下還有子
    機碼刪不掉）代表機碼仍留在登錄表裡，才回傳 False。
    """
    hive = InstallScope(no_admin_install, registry=registry).registry_hive
    removal_failed = False
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
                registry.DeleteKey(hive, reg_path)
            except FileNotFoundError:
                continue
            except Exception:
                removal_failed = True

    _notify_association_changed()
    return not removal_failed


def _clear_stale_user_associations(ext, registry, log, just_wrote_hive=None):
    """清掉這個副檔名在使用者個人層級（HKCU）殘留的幾個地方，讓新關聯真的生效。

    Windows 8 之後，只要使用者曾經手動選過（或系統自動選過）這個副檔名的
    預設開啟程式，就會在目前使用者的 HKCU 底下留下好幾層殘留，Explorer 之後
    只認這些殘留、完全不看我們寫的關聯——不清掉的話，前面登錄表寫得再對，
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

    真實抓到的 bug（no_admin_install 支援接上 InstallScope 之後才會踩到）：
    第 2 點原本無條件當成「一定是別的殘留」清掉，這個假設在新關聯固定寫
    HKLM 的年代成立；no_admin_install=True 時 register()/unregister() 改寫
    HKCU\\Software\\Classes\\<ext>，這裡如果還是無條件清 HKCU 的同一個
    路徑，會把剛剛才寫入的關聯自己清掉。just_wrote_hive 是呼叫端這次實際
    寫入/移除的 hive——等於 HKEY_CURRENT_USER 時跳過第 2 點，只清跟它
    不同路徑、確定不會自己打自己的第 1、3 點。
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

    if just_wrote_hive != registry.HKEY_CURRENT_USER:
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
