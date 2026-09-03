"""
webview2_runtime.py
--------------------
偵測 Microsoft Edge WebView2 Runtime，缺少時取得它。

**為什麼需要這個模組。** 2026-09-03 於 Windows 10 Enterprise LTSC 2019
（17763.316，未安裝該元件）實測：安裝視窗會開啟，但 CSS 與 JavaScript 都
不生效——原本左右並排的圖示與安裝目的地塌成直向堆疊並溢出視窗，箭頭、
核取方塊與關閉鈕都不可見，應用程式名稱停在 `ui/index.html` 的預設佔位文字
「載入中...」。**全程不顯示任何錯誤訊息，行程也不結束。** 使用者看到的是一個
像是還在載入的畫面，會一直等下去。這比空白視窗更不利：空白至少看得出壞了。

**為什麼不沿用既有的相依元件機制。** `dependency_install.py` 那一套的偵測
結果、詢問畫面與安裝進度全都呈現在 `ui/index.html`（見該檔的「相依元件自動
安裝頁」），而那個頁面正是缺少 WebView2 時打不開的東西——雞生蛋。因此這裡
全程在 Python 內完成、不碰 HTML，只重用那套機制的下載與執行部分。

**為什麼不做離線內嵌。** 微軟的離線安裝程式實測為 246.5 MB（載入器只有
1.7 MB）。內嵌會讓每一顆安裝檔都膨脹六倍，去換極少數環境；而真正需要離線
部署的人走的是靜默安裝，那條路根本不建立視窗，這個問題對他們不存在。

**為什麼三個進入點的處置不一致**（`Setup_XXX.exe` 代為安裝、`uninstall.exe`
改走靜默路徑、`InstallerBuilder.exe` 只告知）——理由見 `docs/adr/0012`。
那個不一致是刻度不同造成的，不是遺漏，不要「順手改成一致」。

registry／sleep 是測試接縫（比照 `file_assoc.py` 的 registry 參數），預設
分別是 winreg 與 time.sleep。
"""
import collections
import os
import subprocess
import time
import urllib.request

try:
    import winreg
except ImportError:  # 非 Windows 平台只為了讓測試能匯入這個模組
    winreg = None


# 微軟文件指定的 WebView2 Runtime 用戶端識別碼。
_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

# 64 位元 Windows 上的機器層級安裝（EdgeUpdate 是 32 位元程式，因此寫在
# WOW6432Node 底下）。
WOW6432_PATH = r"SOFTWARE\WOW6432Node" + "\\" + _CLIENT
# 32 位元 Windows 上的機器層級安裝，以及使用者層級安裝（HKCU）共用這個路徑。
NATIVE_PATH = "SOFTWARE\\" + _CLIENT

VERSION_VALUE = "pv"
# EdgeUpdate 會留下 pv 為此值的空殼機碼，那不是「已安裝」。
_ABSENT_VERSION = "0.0.0.0"

# 微軟的 Evergreen 載入器永久連結（實測 1.7 MB）。它負責下載並安裝真正的
# 執行階段；**不傳 /silent**，讓它顯示微軟自己的進度介面——安裝端沒有可用的
# 畫面可以自行顯示進度（HTML 打不開、Tkinter 不在安裝檔裡）。
BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
DOWNLOAD_PAGE_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

INSTALLED = "installed"
JUST_INSTALLED = "just_installed"
DECLINED = "declined"
FAILED = "failed"

Outcome = collections.namedtuple("Outcome", "state version")


def _locations(registry):
    """三個要查的位置。順序是「先機器層級、後使用者層級」。"""
    return (
        (registry.HKEY_LOCAL_MACHINE, WOW6432_PATH),
        (registry.HKEY_LOCAL_MACHINE, NATIVE_PATH),
        (registry.HKEY_CURRENT_USER, NATIVE_PATH),
    )


def find_version(registry=None):
    """回傳已安裝的執行階段版本；沒有安裝時回傳空字串。

    **三個位置都要查。** 只查 HKLM 會把使用者層級的安裝誤判成沒裝，然後對著
    一台已經有 WebView2 的機器要求重裝。

    單一位置讀取失敗（機碼不存在、沒有 pv 值、權限不足）時繼續查下一個，
    不視為錯誤——「查不到」本來就是這個函式要回答的問題之一。
    """
    registry = registry or winreg
    for hive, path in _locations(registry):
        try:
            with registry.OpenKey(hive, path) as key:
                value, _kind = registry.QueryValueEx(key, VERSION_VALUE)
        except (OSError, FileNotFoundError):
            continue
        version = str(value).strip()
        if version and version != _ABSENT_VERSION:
            return version
    return ""


def download(url, dest_path, opener=None, timeout=30):
    """把載入器下載到 dest_path。成功回傳 True，失敗回傳 False。

    不走 BITS。`bits_download` 的好處是背景低優先權與斷點續傳，對 1.7 MB
    沒有意義；而 `dependency_install` 裡那段兩層下載邏輯埋在一支測試完整的
    函式內部，為了這件事把它抽出來會動到不必要的範圍。

    **一定要比對 Content-Length。** 稽核 F06 抓到的真實問題：連線中途斷掉時
    `read()` 只是回傳空字串正常結束迴圈，不會拋例外——不比對長度就會去執行
    一個被截斷的安裝檔。長度不符時連同已寫入的部分一併刪除：留著半截的檔案，
    下一次就可能被當成「已經下載過」而直接執行。

    下載失敗是預期中的結局之一（沒有網路、被防火牆擋、使用者的環境不通），
    因此回傳 False 而不是往外拋。
    """
    opener = opener or urllib.request.urlopen
    received = 0
    try:
        with opener(url, timeout=timeout) as response:
            declared = response.getheader("Content-Length")
            declared = int(declared) if declared else None
            with open(dest_path, "wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
    except Exception:
        _remove(dest_path)
        return False
    if declared is not None and received != declared:
        _remove(dest_path)
        return False
    return True


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def run_bootstrapper(path, run=None):
    """執行載入器，等它結束。成功回傳 True。

    **不傳 `/silent`。** 安裝端沒有可用的畫面可以自行顯示進度——HTML 打不開
    （那正是缺少這個元件的後果），Tkinter 不在安裝檔裡（`installer_core.py`
    的 `_show_starting_cursor()` 記載了「使用者要求安裝檔本身盡量簡潔」這個
    決定）。讓微軟的安裝程式顯示它自己的進度介面，是唯一不必自行實作進度
    視窗的做法。

    非零結束碼視為失敗，使用者在微軟的畫面按取消也走這條。
    """
    run = run or subprocess.run
    try:
        return run([path]).returncode == 0
    except Exception:
        return False


def ensure_available(ask, install, registry=None, sleep=None, recheck_delay=2):
    """確認執行階段可用；缺少時徵詢使用者並取得它。

    ask() 回傳使用者是否同意安裝，install() 執行取得動作並回傳是否成功——
    兩者都由呼叫端提供，因為它們的形式在三個進入點不同（安裝端跳原生對話框
    並下載載入器；打包工具只告知、不代勞）。

    **裝完卻查不到時會等一下再查一次。** 登錄表的寫入可能還沒落地，而對一個
    剛裝完的使用者說「還是沒有」是最沒有說服力的錯誤訊息。重查只做一次：
    再多做也只是把「失敗」這件事拖久一點。

    失敗不重試安裝本身。下載失敗與使用者在微軟的畫面按取消，都不是重跑一次
    會改變的事。
    """
    version = find_version(registry)
    if version:
        return Outcome(INSTALLED, version)

    if not ask():
        return Outcome(DECLINED, "")

    if not install():
        return Outcome(FAILED, "")

    version = find_version(registry)
    if not version:
        (sleep or time.sleep)(recheck_delay)
        version = find_version(registry)
    if not version:
        return Outcome(FAILED, "")
    return Outcome(JUST_INSTALLED, version)
