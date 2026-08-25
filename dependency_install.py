"""
dependency_install.py
----------------------
相依元件偵測/下載/靜默安裝子系統。

真實抓到的架構問題：`dependency_defs.py` 的說明文字講「checker/URL/
靜默參數本體定義在這」，但那個檔案一直以來只有一個沒有行為的 metadata
dict——登錄表探測、.NET shared-fx 目錄備援掃描、下載/驗證/執行整條協定
全部散落在 installer_core.py 的模組層級函式跟 InstallerAPI 的方法裡，
去 dependency_defs.py 找行為的人只會撲空（這輪 session 手動排查一個
.NET Desktop Runtime 誤判的 bug 時剛好踩到這個落差）。這裡收斂成一個
獨立模組，介面吃明確參數（custom_dependencies/bundle_dependencies/
dependencies_min_version/checkers），不吃 InstallerAPI 的實例狀態——
測試或未來想重用相依元件偵測邏輯的地方（例如 gui_config.py 的打包
預覽），都不需要先建構一整個 InstallerAPI()。

跟 file_assoc.py/system_entries.py 不同：這裡的 winreg 存取沒有走同一種
`registry=` 注入 seam，維持原本 installer_core.py 的做法（每次呼叫都
`import winreg`，測試用 `mock.patch.dict(sys.modules, {"winreg": fake})`）
——單純是搬移現有程式碼，不在這輪一併改變 seam 設計。
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import shutil
import urllib.request

import bits_download
import dependency_defs
from version_compare import parse_version, compare_versions

# ---------------------------------------------------------------------------
# 相依元件偵測：只做「登錄表存在與否」的簡易判斷，非百分之百精準，
# 但足以提醒使用者「可能缺少某個執行環境」。
# ---------------------------------------------------------------------------

_REGISTRY_HIVES = None  # 延後初始化：只有用得到時才 import winreg（跨平台開發機也能載入這個模組）


def _registry_hive(name):
    """把設定檔裡的字串（"HKLM"/"HKCU"）轉成 winreg 的 hive 常數。
    只支援這兩個，相依元件偵測跟免 UAC 模式用得到的就這兩種。
    """
    global _REGISTRY_HIVES
    import winreg
    if _REGISTRY_HIVES is None:
        _REGISTRY_HIVES = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        }
    return _REGISTRY_HIVES.get(str(name).strip().upper(), winreg.HKEY_LOCAL_MACHINE)


def _generic_registry_check(hive, path, value_name=None, expected=None):
    """泛用的「這個相依元件裝了沒」登錄表偵測，取代原本每個相依元件各自寫
    一個檢查函式的做法（見規格文件對應章節：自訂相依元件功能）。

    value_name 留空：只確認這個機碼本身存在（.NET Desktop Runtime 沒有明確
    的版本值可查，用機碼是否存在當作「有沒有裝」的依據）。
    value_name 有給：機碼底下這個值要等於 expected 才算已安裝。
    """
    import winreg
    try:
        with winreg.OpenKey(_registry_hive(hive), path) as key:
            if value_name is None:
                return True
            val, _ = winreg.QueryValueEx(key, value_name)
            return val == expected
    except Exception:
        return False


def _read_registry_version(hive, path, value_name=None, enum_subkeys=False):
    """讀出這個機碼底下的「已安裝版本」字串；讀不到／機碼不存在回傳 None。

    enum_subkeys=False：讀 value_name 這個值當版本字串（vcredist 的
    Version 值就是這種佈局）。
    enum_subkeys=True：EnumKey 列出子機碼名稱，取版本最高的當已安裝版本
    （.NET Desktop Runtime 的 InstalledVersions\\...\\sharedfx\\... 底下，
    子機碼名稱本身就是版本號，例如 "8.0.10"，沒有明確的版本值可查）。
    """
    import winreg
    try:
        with winreg.OpenKey(_registry_hive(hive), path) as key:
            if enum_subkeys:
                versions = []
                i = 0
                while True:
                    try:
                        versions.append(winreg.EnumKey(key, i))
                    except OSError:
                        break
                    i += 1
                return max(versions, key=parse_version) if versions else None
            val, _ = winreg.QueryValueEx(key, value_name)
            return str(val)
    except Exception:
        return None


def _generic_registry_version_check(hive, path, value_name=None, enum_subkeys=False, min_version=None):
    """存在性 + 選填版本門檻的登錄表偵測。min_version 是 None 時等同純粹的
    存在性判斷（讀得到版本字串／至少有一個子機碼就算已安裝，不管版本）；
    有給 min_version 則額外要求讀到的版本 >= min_version（用 version_compare
    的 compare_versions()）。
    """
    installed_version = _read_registry_version(hive, path, value_name=value_name, enum_subkeys=enum_subkeys)
    if installed_version is None:
        return False
    if min_version is None:
        return True
    return compare_versions(installed_version, min_version) >= 0


def _check_vcredist_x64(min_version=None):
    path = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
    if not _generic_registry_check("HKLM", path, "Installed", 1):
        return False
    if min_version is None:
        return True
    return _generic_registry_version_check("HKLM", path, value_name="Version", min_version=min_version)


def _dotnet_shared_fx_versions(fx_name):
    """掃 .NET 執行環境常見安裝目錄下 shared\\<fx_name>\\ 底下的版本子資料夾
    名稱——這就是 `dotnet --list-runtimes` 內部實際在做的事。

    真實抓到的問題：_check_dotnet_desktop() 只查登錄表
    HKLM\\SOFTWARE\\WOW6432Node\\dotnet\\Setup\\InstalledVersions\\...，但
    這把機碼只有透過官方 MSI 版安裝程式裝的才會寫入——實測發現透過
    winget／Visual Studio Installer／dotnet-install.ps1 裝的完全不會寫這把
    機碼，即使 `dotnet --list-runtimes` 能正常列出已安裝版本，登錄表判斷
    還是會誤判成沒裝，使用者明明已經裝好、版本也符合，還是被要求「自動
    安裝」，裝完一樣偵測不到。改成登錄表查不到時，直接掃實際安裝目錄
    當備援，不依賴「用哪一種安裝程式裝的」這個實作細節。
    """
    candidates = []
    for env_var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(env_var)
        if root and root not in candidates:
            candidates.append(root)
    versions = []
    for root in candidates:
        shared_dir = os.path.join(root, "dotnet", "shared", fx_name)
        try:
            for entry in os.listdir(shared_dir):
                if os.path.isdir(os.path.join(shared_dir, entry)):
                    versions.append(entry)
        except OSError:
            continue
    return versions


def _check_dotnet_desktop(min_version=None):
    if _generic_registry_version_check(
        "HKLM", r"SOFTWARE\WOW6432Node\dotnet\Setup\InstalledVersions\x64\sharedfx\Microsoft.WindowsDesktop.App",
        enum_subkeys=True, min_version=min_version,
    ):
        return True
    versions = _dotnet_shared_fx_versions("Microsoft.WindowsDesktop.App")
    if not versions:
        return False
    if min_version is None:
        return True
    return compare_versions(max(versions, key=parse_version), min_version) >= 0


def _make_custom_dependency_checker(reg_check):
    """把 custom_dependencies 裡一筆 registry_check 設定，包成跟內建相依元件
    同樣簽章的 check_fn（不吃參數、回傳布林），供 build_checkers() 統一放進
    同一個 dict 使用。用 default 參數把當下這筆設定值綁進閉包，避免 for
    迴圈裡常見的「閉包晚繫結」陷阱（迴圈跑完後所有 checker 都指到最後
    一筆設定）。

    有給 min_version 時改用 _generic_registry_version_check()（版本門檻，
    忽略 expected）；沒給時維持原本 _generic_registry_check()（exact-match
    語意），完全向後相容既有的 custom_dependencies 設定。
    """
    def _check(reg_check=reg_check):
        min_version = reg_check.get("min_version")
        if min_version is not None:
            return _generic_registry_version_check(
                reg_check.get("hive", "HKLM"),
                reg_check.get("path", ""),
                value_name=reg_check.get("value_name"),
                enum_subkeys=bool(reg_check.get("enum_subkeys", False)),
                min_version=min_version,
            )
        return _generic_registry_check(
            reg_check.get("hive", "HKLM"),
            reg_check.get("path", ""),
            reg_check.get("value_name"),
            reg_check.get("expected"),
        )
    return _check


# {key: (check_fn, 顯示名稱, 官方靜默安裝檔下載連結, 靜默安裝命令列參數)}。
# 下載連結目前都指向可以直接執行的安裝檔（不是網頁），install() 靠這個直接
# 下載＋執行。顯示名稱/連結/靜默參數本體定義在 dependency_defs.py 的
# BUILT_IN_DEPENDENCIES（純 metadata），跟 builder.py 的 bundle_dependencies
# （打包時內嵌相依元件安裝檔）共用同一份，避免兩邊分別維護一份 URL 悄悄
# 不同步（見 dependency_defs.py 開頭的說明，包含 dotnet_desktop 版本號
# 需要定期手動更新的已知限制）。
DEPENDENCY_CHECKERS = {
    "vcredist_x64": (
        _check_vcredist_x64,
        dependency_defs.BUILT_IN_DEPENDENCIES["vcredist_x64"]["display_name"],
        dependency_defs.BUILT_IN_DEPENDENCIES["vcredist_x64"]["download_url"],
        dependency_defs.BUILT_IN_DEPENDENCIES["vcredist_x64"]["silent_args"],
    ),
    "dotnet_desktop": (
        _check_dotnet_desktop,
        dependency_defs.BUILT_IN_DEPENDENCIES["dotnet_desktop"]["display_name"],
        dependency_defs.BUILT_IN_DEPENDENCIES["dotnet_desktop"]["download_url"],
        dependency_defs.BUILT_IN_DEPENDENCIES["dotnet_desktop"]["silent_args"],
    ),
}


def build_checkers(custom_dependencies=(), dependencies_min_version=None):
    """把內建的 DEPENDENCY_CHECKERS 跟打包時透過 custom_dependencies 自訂的
    相依元件合併成一份對照表。

    每次呼叫都重新組（不是快取起來）：一來合併成本很低（最多幾筆），二來
    讓 DEPENDENCY_CHECKERS 在執行當下被覆蓋/patch（例如測試情境）時，這裡
    永遠讀到當下最新的內容。custom_dependencies 的 key 如果跟內建的撞名，
    直接以自訂設定覆蓋（打包時 packaging_core 已經擋掉撞名，這裡再處理
    一次是最後一道防線，不視為錯誤，沿用「儘量讓安裝繼續」的原則）。
    """
    dependencies_min_version = dependencies_min_version or {}
    checkers = {}
    for key, (check_fn, display_name, url, silent_args) in DEPENDENCY_CHECKERS.items():
        min_version = dependencies_min_version.get(key)
        if min_version is not None:
            # 只有實際設定了 min_version 才改用需要吃 min_version 關鍵字
            # 參數的呼叫方式——沒設定時完全比照舊行為呼叫 check_fn()，
            # 不會因為測試把 DEPENDENCY_CHECKERS 的內容 patch 成零參數
            # 的 lambda 就爆炸（真實抓到的相容性考量）。
            check_fn = (lambda fn=check_fn, mv=min_version: fn(min_version=mv))
        checkers[key] = (check_fn, display_name, url, silent_args)
    for entry in custom_dependencies:
        key = str(entry.get("key", "")).strip()
        if not key:
            continue
        checkers[key] = (
            _make_custom_dependency_checker(entry.get("registry_check", {}) or {}),
            str(entry.get("display_name", key)),
            str(entry.get("download_url", "")),
            list(entry.get("silent_args", []) or []),
        )
    return checkers


def get_warnings(dependencies, checkers):
    """回傳目前系統缺少的相依元件清單（key + 顯示名稱 + 下載連結），
    不阻擋安裝。前端用 key 呼叫 install(key, ...) 觸發自動安裝，
    url 保留給自動安裝失敗時的手動下載備援連結。
    """
    warnings = []
    for key in dependencies:
        checker = checkers.get(key)
        if not checker:
            continue
        check_fn, display_name, url, _silent_args = checker
        if not check_fn():
            warnings.append({"key": key, "name": display_name, "url": url})
    return warnings


def _file_sha256(path, chunk_size=1024 * 1024):
    """算檔案的 SHA-256 摘要（十六進位小寫字串），用來驗證下載回來的相依
    元件安裝檔沒有被竄改——密碼學等級的完整性/來源驗證，跟複製檔案時用的
    CRC32（只防「複製過程中壞掉」）用途不同。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _default_resource_path(relative_path):
    """跟 installer_core.py/uninstall.py 各自的 get_resource_path() 邏輯
    相同：PyInstaller 單一檔案打包環境下解析成解壓出來的暫存資源目錄，
    開發模式下解析成目前工作目錄。呼叫端（InstallerAPI）通常會傳自己的
    get_resource_path 進來，這裡只是獨立測試/直接呼叫這個模組時的預設值。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


def _noop_progress(percent, message):
    pass


def install(key, checkers, custom_dependencies=(), bundle_dependencies=(),
            resolve_resource_path=_default_resource_path, on_progress=_noop_progress):
    """使用者在相依元件彈窗按下「自動安裝」時，依序對每個缺少的元件呼叫
    這個函式：下載官方安裝檔到暫存目錄、靜默執行，結束後不管子程序的
    結束碼，一律重新呼叫這個元件自己的登錄表偵測函式（check_fn）確認
    「現在到底裝好了沒」才是最終依據。

    真實情境：Visual C++ Redistributable 的官方文件明講，如果偵測到
    機器上已經裝了更新版本，`/quiet` 模式下的子程序會回傳非 0 的錯誤碼，
    但這其實不是真正的失敗——只看結束碼判斷會誤判成失敗。呼叫端（安裝
    程式本身）執行到這裡一定已經是系統管理員權杖，子程序繼承同一個權杖
    直接靜默安裝，不會再跳一次 UAC。
    """
    checker = checkers.get(key)
    if not checker:
        return {"status": "error", "message": "未知的相依元件。"}
    check_fn, display_name, url, silent_args = checker

    # sha256（選填）：custom_dependencies 這一筆如果有設定，下載完成後、
    # 執行前用來驗證檔案完整性/沒被竄改——見 packaging_core.py 的驗證
    # 與正規化（統一小寫），這裡再 .lower() 一次是保底，避免繞過打包
    # 驗證直接手動編輯設定檔的情境。內建相依元件目前沒有這個欄位。
    expected_sha256 = next(
        (str(e.get("sha256")).lower() for e in custom_dependencies
         if str(e.get("key", "")).strip() == key and e.get("sha256")),
        None,
    )

    # bundle_dependencies：打包時已經把這個相依元件的安裝檔內嵌進安裝檔裡
    # （見 packaging_core.py/builder.py 的 bundle_dependencies 處理），
    # 直接執行內嵌檔案即可，不用再連網下載——適合「不確定目標機器有沒有
    # 網路」的情境。掛載路徑固定是 dependencies/<key>.exe，跟打包端的
    # 命名規則一致。
    bundled_path = resolve_resource_path(os.path.join("dependencies", f"{key}.exe"))
    run_path = None
    tmp_dir = None
    if key in bundle_dependencies and os.path.exists(bundled_path):
        run_path = bundled_path
        on_progress(55, f"正在安裝 {display_name}...")
    else:
        # 真實抓到的問題：原本用固定、可預測的檔名
        # （%TEMP%\dep_installer_<key>.exe）。如果這支安裝程式是在提權
        # 更高的情境下執行（例如透過 MDM/GPO 用 SYSTEM 帳號靜默部署，
        # %TEMP% 解析成所有已驗證使用者都寫得進去的 C:\Windows\Temp），
        # 這個固定路徑本身就是可以被搶先寫入的競態視窗。改成每次下載
        # 都用 mkdtemp() 產生一個獨立、不可預測的暫存資料夾。
        tmp_dir = tempfile.mkdtemp(prefix="mswi_dep_")
        tmp_path = os.path.join(tmp_dir, f"{key}.exe")
        on_progress(5, f"正在下載 {display_name}...")
        # 優先走 BITS（斷點續傳、背景低優先權下載），沒裝 pywin32 或
        # BITS 呼叫本身失敗都會 best-effort 回傳 False，退回原本的
        # urllib 下載邏輯，維持行為對等（只是沒有 BITS 特有的好處）。
        downloaded_via_bits = bits_download.download_via_bits(
            url, tmp_path,
            on_progress=lambda p: on_progress(
                5 + int(p * 0.5), f"正在下載 {display_name}...",  # 下載階段佔 5%-55%
            ),
        )
        if not downloaded_via_bits:
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    total = resp.getheader("Content-Length")
                    total = int(total) if total else None
                    downloaded = 0
                    with open(tmp_path, "wb") as f:
                        while True:
                            chunk = resp.read(1024 * 256)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                percent = 5 + int(downloaded / total * 50)  # 下載階段佔 5%-55%
                                on_progress(percent, f"正在下載 {display_name}...")
                    # 真實抓到的問題：Content-Length 原本只被拿來算進度
                    # 百分比，從未拿來確認真的下載完整——連線中途斷掉時
                    # read() 只是回傳空字串正常結束迴圈，不會拋例外，
                    # 會去執行一個被截斷的安裝檔。
                    if total is not None and downloaded != total:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                        return {
                            "status": "error",
                            "message": f"下載 {display_name} 失敗：下載不完整"
                                       f"（預期 {total} bytes，實際收到 {downloaded} bytes）。",
                        }
            except Exception as e:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return {"status": "error", "message": f"下載 {display_name} 失敗：{e}"}
        run_path = tmp_path

        if expected_sha256:
            actual_sha256 = _file_sha256(run_path)
            if actual_sha256 != expected_sha256:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return {
                    "status": "error",
                    "message": f"{display_name} 下載完整性驗證失敗（sha256 不符），"
                               f"可能是下載過程被竄改，已略過執行。",
                }

    try:
        on_progress(60, f"正在安裝 {display_name}...")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run([run_path] + silent_args, creationflags=creationflags, timeout=600)
    except Exception as e:
        return {"status": "error", "message": f"執行 {display_name} 安裝程式失敗：{e}"}
    finally:
        # 內嵌檔案是這次安裝檔自己的資源（PyInstaller 解壓出來的暫存內容
        # 或開發模式下的原始檔案），不是我們自己下載出來的暫存資料夾，
        # 不能刪除；只清掉真的是我們下載出來的那份（連同它所在的暫存
        # 資料夾一起清，不留下空資料夾）。
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    on_progress(95, "正在確認安裝結果...")
    if check_fn():
        return {"status": "success", "name": display_name}
    return {
        "status": "error",
        "message": f"{display_name} 安裝流程已經結束，但仍偵測不到已安裝——"
                   f"可能需要手動安裝，或重新啟動電腦後再試一次。",
    }
