"""
sdk_tools.py
-------------
Windows SDK 工具（`makeappx.exe`／`signtool.exe`）的定位與取得。

這兩支工具同屬 Windows SDK，用途分別是打包 MSIX 與簽署數位簽章。本模組
是它們在這個專案裡唯一的取得入口——`builder.py` 的 exe 簽章功能與尚未
實作的 MSIX 模式共用同一套邏輯，理由見
`docs/adr/0008-sdk-build-tools-are-fetched-on-explicit-request-only.md`
決定四：同一目錄下的兩支工具採用兩套檢索方式，是後續缺陷的來源。

本模組實作 ADR-0008 的五項決定：

1. **不自動下載**——`find_tool()` 找不到工具時中止並拋出 `SdkToolNotFound`，
   訊息內含使用者可直接複製執行的取得指令（`fetch_command_hint()`）。實際
   下載只在 `fetch_tools()` 被明確呼叫時發生，而該函式只由 CLI 的
   `fetch-sdk-tools` 子指令觸發。判準不是「打包時是否連網」，而是下載物在
   打包機器上是被內嵌還是被執行——後者的最壞情況是打包機器遭入侵，而打包
   機器通常存放簽章憑證。
2. **版本固定並驗證 SHA-256**——見 `PACKAGE_VERSION`／`PACKAGE_SHA256` 的
   維護說明。
3. **快取為獨立且持久的使用者層級位置，路徑含版本號**——見 `cache_dir()`。
4. **既有的 exe 簽章功能共用同一套取得機制**——`builder._sign_executable()`
   呼叫本模組的 `find_tool()`。
5. **來源優先序依使用者表達意圖的明確程度排列**——見 `find_tool()`。

取得動作的形狀為獨立子指令（`fetch-sdk-tools`），不是打包指令的旗標：
它是一次性的環境準備動作，混進打包指令會使「打包流程不自動下載」這項
決定在某些呼叫方式下自相矛盾。

測試接縫：`find_tool()` 的 `settings`／`environ` 與 `fetch_tools()` 的
`download` 都是可注入的參數（比照 `file_assoc.py` 的 registry seam），
測試因此不需要真實網路存取，也不依賴執行測試的機器上是否安裝 Windows SDK。
"""
import os
import shutil
import sys
import tempfile
import zipfile
from collections import namedtuple

import packaging_settings

# --- 固定的套件版本與雜湊（ADR-0008 決定二） ---------------------------------
#
# 這兩個常數必須同時更換，缺一不可：版本號改了而雜湊沒改，驗證會在下載完成
# 後失敗；雜湊改了而版本沒改，等於停用了驗證。
#
# 固定版本的理由有二。其一是安全性：本模組取得的是會在打包機器上「被執行」
# 的執行檔，驗證強度不該比照 `dependency_defs.py` 那些「下載來內嵌、不在
# 打包機器上執行」的項目（那些的網址是 `https://aka.ms/vc14/...` 這類指向
# 最新版的形式，內容會變動，本來就釘不住雜湊）。其二是可重現性：版本未固定
# 時，不同時間執行的兩次打包會使用不同版本的 `makeappx`，產出的 `.msix`
# 因此不同。NuGet 套件發布後內容不可變更，固定版本即等同固定內容。
#
# 更換版本的作法：下載新版 `.nupkg`，以 SHA-256 計算其摘要，兩個常數一併
# 更新。雜湊值取得一次後永久有效，不需要重新計算。
PACKAGE_ID = "Microsoft.Windows.SDK.BuildTools"
PACKAGE_VERSION = "10.0.26100.4948"
PACKAGE_SHA256 = "aed41d0c6a3c78b794f972f0218a8a57d8481851c45720bb0a076f32fcfe6a02"

PACKAGE_URL = (
    "https://api.nuget.org/v3-flatcontainer/"
    f"{PACKAGE_ID.lower()}/{PACKAGE_VERSION}/{PACKAGE_ID.lower()}.{PACKAGE_VERSION}.nupkg"
)

# 這兩支是本專案實際會用到的工具。套件內另有 MakeCert.exe 等其他工具，
# 不列入必要清單——`fetch_tools()` 以本清單判斷解壓結果是否可用，列入
# 用不到的工具會讓判斷因為無關的項目而失敗。
REQUIRED_TOOLS = ("makeappx.exe", "signtool.exe")

# 設定欄位名稱。兩者皆為「工具偏好」層級（`packaging_settings.py`，與
# `workspace_dir` 同一層），不是打包設定檔的欄位：它們描述的是「這台打包
# 機器上的東西在哪」，與「要打包成什麼產品」無關，換一台機器就該換值，
# 不該跟著打包設定檔一起進版控。
SETTING_TOOLS_DIR = "sdk_tools_dir"
SETTING_CACHE_DIR = "sdk_tools_cache_dir"

FETCH_SUBCOMMAND = "fetch-sdk-tools"

# 系統上既有 SDK 安裝的檢索範圍限於 Windows Kits 10，不含殘留的 8.1。
# 8.1 的工具早於 MSIX 這個格式，其 `makeappx` 產不出現行的套件；把兩個
# 世代混進同一套檢索，等於在剛以決定四消除不一致的地方重新製造一個。
_SDK_ROOT_ENV_KEYS = ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432")
_SDK_ROOT_SUBPATH = ("Windows Kits", "10")


class SdkToolNotFound(Exception):
    """找不到所需的 SDK 工具。

    訊息本身即為使用者的處置指引（含可直接複製執行的取得指令），呼叫端
    直接轉呈即可，不需要再包一層自己的說明。
    """


class ToolLocation(namedtuple("ToolLocation", "path source version tool")):
    """一次成功的檢索結果。

    `source` 為 `manual`／`cache`／`path`／`system` 之一，`version` 在無從
    得知時為空字串（手動指定的目錄與 PATH 上的執行檔都不帶版本資訊）。

    ADR-0008 決定五末段要求建置過程輸出本次實際採用的來源與版本：三個來源
    並存時，「兩台機器打包結果不同」這個問題若無此資訊將難以診斷。
    `describe()` 即為該行輸出。
    """

    __slots__ = ()

    def describe(self):
        label = _SOURCE_LABELS.get(self.source, self.source)
        version = f"，版本 {self.version}" if self.version else ""
        return f"{self.tool} 來源：{label}{version}（{self.path}）"


_SOURCE_LABELS = {
    "manual": f"手動指定（{SETTING_TOOLS_DIR}）",
    "cache": f"{FETCH_SUBCOMMAND} 下載的快取",
    "path": "PATH",
    "system": "系統上的 Windows SDK",
}

FetchResult = namedtuple("FetchResult", "cache_dir version tools")


def _settings(settings):
    """設定的預設來源。

    與 `packaging_core.get_workspace_dir()` 只在 frozen exe 情境下讀取
    設定不同，這裡一律讀取：SDK 工具的位置是這台機器的性質，以原始碼形式
    執行時同樣成立。
    """
    if settings is not None:
        return settings
    return packaging_settings.load_settings()


def _environ(environ):
    return os.environ if environ is None else environ


def _long_path(path):
    r"""回傳可以繞過 Windows 傳統 260 字元路徑上限的形式（加上 \\?\ 前綴）。

    真實抓到的缺陷：這個 NuGet 套件裡有
    `Microsoft.Windows.Build.Appx.AppxPackaging.dll.manifest` 這類很長的
    檔名，快取位置只要稍深一點（使用者的家目錄名稱、CI 指定的快取路徑
    深度都不在本工具的控制範圍內），解壓目標就會超過上限而失敗——而且
    失敗訊息是「找不到檔案」，指向的方向與真正的原因無關。

    只用於本模組內部的檔案操作，不會出現在回報給使用者的路徑字串裡：
    這個前綴對使用者而言是雜訊，而錯誤訊息與 describe() 的用途是給人看。
    """
    if os.name != "nt" or not path:
        return path
    path = os.path.abspath(path)
    prefix = "\\\\?\\"
    if path.startswith(prefix):
        return path
    if path.startswith("\\\\"):
        # UNC 路徑（\\伺服器\分享）的形式是 \\?\UNC\伺服器\分享。
        return prefix + "UNC" + path[1:]
    return prefix + path


def _version_key(name):
    """把 `10.0.22621.0` 這種目錄名轉成可比大小的鍵。

    非數字的片段一律排在最後（給 -1），因為版本目錄理應全為數字，出現
    非數字代表那不是我們認得的版本目錄，不該因為字串排序碰巧靠前而被選中。
    """
    key = []
    for chunk in str(name).split("."):
        key.append(int(chunk) if chunk.isdigit() else -1)
    return key


def arch_candidates(environ=None):
    """依這台機器的處理器架構決定 `bin/<版本>/<架構>/` 要找哪個子目錄。

    回傳的是候選順序而非單一值：x64 機器上找不到 x64 版時，x86 版一樣
    可以執行，沒有理由因此判定「找不到工具」。
    """
    env = _environ(environ)
    arch = (env.get("PROCESSOR_ARCHITEW6432") or env.get("PROCESSOR_ARCHITECTURE") or "").upper()
    if arch == "ARM64":
        return ("arm64", "x64", "x86")
    if arch in ("AMD64", "IA64"):
        return ("x64", "x86")
    return ("x86", "x64")


def _lookup_in_root(root, tool, archs):
    """在一個目錄底下找指定工具，回傳 `(路徑, 版本)`，找不到回傳 None。

    容許四種形狀，理由是使用者手動指定路徑時，指到哪一層都應該能用——
    要求使用者精確指到 `bin/10.0.26100.0/x64` 這種深度，是把工具自己
    查得到的事轉嫁給使用者：

      <root>/<工具>                        直接指向工具所在目錄
      <root>/<架構>/<工具>                 舊版 SDK 的無版本層形狀
      <root>/bin/<版本>/<架構>/<工具>      NuGet 套件與現行 SDK 的形狀
      <root>/bin/<架構>/<工具>             舊版 SDK 的 bin 底下無版本層形狀
    """
    if not root or not os.path.isdir(_long_path(root)):
        return None

    direct = os.path.join(root, tool)
    if os.path.isfile(_long_path(direct)):
        return (direct, "")

    for arch in archs:
        candidate = os.path.join(root, arch, tool)
        if os.path.isfile(_long_path(candidate)):
            return (candidate, "")

    bin_root = os.path.join(root, "bin")
    if not os.path.isdir(_long_path(bin_root)):
        return None

    versioned = []
    try:
        entries = os.listdir(_long_path(bin_root))
    except OSError:
        entries = []
    for entry in entries:
        for arch in archs:
            candidate = os.path.join(bin_root, entry, arch, tool)
            if os.path.isfile(_long_path(candidate)):
                versioned.append((_version_key(entry), entry, candidate))
                break
    if versioned:
        # 多版本並存時取最新，理由是舊版工具產出的套件格式可能落後於系統
        # 的支援程度，而使用者留著舊版通常只是沒有清理，不是刻意指定。
        versioned.sort()
        _, entry, candidate = versioned[-1]
        return (candidate, entry)

    for arch in archs:
        candidate = os.path.join(bin_root, arch, tool)
        if os.path.isfile(_long_path(candidate)):
            return (candidate, "")
    return None


def cache_dir(settings=None, environ=None):
    """`fetch-sdk-tools` 下載的工具存放位置（ADR-0008 決定三）。

    路徑含版本號，使更換版本時新版落於新目錄，不覆寫舊版，也不會產生
    新舊混雜的中間狀態。

    不放在編譯工作目錄（`packaging_core.default_workspace_dir()`），因為
    該目錄是每次建置開頭即清空的暫存區。此點與決定一相關：決定一之所以
    成立，前提是「取得一次即持續有效」；快取若不持久，該前提不成立。

    `SETTING_CACHE_DIR` 覆蓋的是版本目錄的上層，不是版本目錄本身——CI 把
    這個位置納入自己的快取機制時，仍然要保有「換版本即換目錄」這個性質。
    """
    conf = _settings(settings)
    base = (conf.get(SETTING_CACHE_DIR) or "").strip()
    if not base:
        env = _environ(environ)
        local = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        base = os.path.join(local, "mac-style-windows-installer", "sdk-tools")
    return os.path.join(base, PACKAGE_VERSION)


def settings_with_overrides(tools_dir=None, cache_dir=None, settings=None):
    """把命令列旗標疊在持久設定之上，回傳這一次建置要用的設定字典。

    需求性質與既有的 `--workspace-dir` 相同：CI 需要在不改動這台機器持久
    設定的前提下，指定這一次建置要用哪裡的工具。因此是疊加而非寫入——
    旗標的效力只及於這一次執行。

    不修改傳入的字典：呼叫端拿到的通常是 `packaging_settings.load_settings()`
    的結果，就地改寫會讓「持久設定」與「這次的覆蓋」在後續難以分辨。
    """
    merged = dict(_settings(settings))
    if tools_dir:
        merged[SETTING_TOOLS_DIR] = tools_dir
    if cache_dir:
        merged[SETTING_CACHE_DIR] = cache_dir
    return merged


def system_sdk_roots(environ=None):
    """系統上可能安裝了 Windows SDK 的位置。"""
    env = _environ(environ)
    roots = []
    for key in _SDK_ROOT_ENV_KEYS:
        base = (env.get(key) or "").strip()
        if not base:
            continue
        root = os.path.join(base, *_SDK_ROOT_SUBPATH)
        if root not in roots:
            roots.append(root)
    return roots


def fetch_command_hint(frozen=None, executable=None):
    """找不到工具時，錯誤訊息裡那行可直接複製執行的取得指令。

    ADR-0008 決定一保留了自動下載的絕大部分效益，靠的就是這行字：原本的
    門檻是「使用者需自行得知要安裝 Windows SDK、找到它、完成數 GB 的
    安裝」，這行把它降為「照著訊息複製一行指令」。訊息裡沒有這行，該決定
    就退化成單純的死路。
    """
    is_frozen = hasattr(sys, "_MEIPASS") if frozen is None else frozen
    exe = sys.executable if executable is None else executable
    if is_frozen:
        return f'"{exe}" {FETCH_SUBCOMMAND}'
    return f'"{exe}" builder_cli.py {FETCH_SUBCOMMAND}'


def find_tool(tool, settings=None, environ=None):
    """依 ADR-0008 決定五的優先序找出一支 SDK 工具。

    順序為：手動指定 -> 下載的快取 -> PATH -> 系統上的 SDK 安裝。排序依據
    是「使用者表達該意圖的明確程度」，而非來源的技術特性；此依據使順序
    可被推導而不需記憶，並在未來新增來源時提供插入位置的判準。

    PATH 是 ADR-0008 決定五三個來源之外的第四個，插在快取與系統 SDK 之間。
    列入的理由是不製造回歸：本模組取代的既有實作（`shutil.which("signtool")`）
    只認 PATH，移除它會讓現行唯一能成功的那群使用者失去可用路徑。位置的
    依據是同一條判準——把工具加進 PATH 是使用者的主動行為，比「碰巧裝了
    SDK」明確；但它不是對著本工具表達的，因此低於「曾執行過取得指令」的
    快取。

    手動指定的目錄若不含所求的工具，直接報錯而非改用下一個來源：手動指定
    是使用者最明確的意圖表達，安靜地改用別的來源會讓使用者以為自己指定的
    路徑正在生效（ADR-0008 已知限制第三項要求使用前驗證該路徑）。
    """
    conf = _settings(settings)
    env = _environ(environ)
    archs = arch_candidates(env)

    manual = (conf.get(SETTING_TOOLS_DIR) or "").strip()
    if manual:
        hit = _lookup_in_root(manual, tool, archs)
        if hit:
            return ToolLocation(hit[0], "manual", hit[1], tool)
        raise SdkToolNotFound(
            f"設定的 {SETTING_TOOLS_DIR} 指向 {manual}，但該位置底下找不到 {tool}。\n"
            f"請確認該路徑是解壓出來的 {PACKAGE_ID} 目錄或 Windows SDK 的安裝位置，"
            f"或清除這項設定改用自動檢索。"
        )

    hit = _lookup_in_root(cache_dir(conf, env), tool, archs)
    if hit:
        # 快取的版本以固定的套件版本回報，不用內部目錄名：套件版本
        # （10.0.26100.4948）與其內部的工具組目錄（10.0.26100.0）並不相同，
        # 而使用者要對照的是自己執行取得指令時拿到的那個版本。
        return ToolLocation(hit[0], "cache", PACKAGE_VERSION, tool)

    path_env = env.get("PATH") or ""
    if path_env.strip():
        on_path = shutil.which(tool, path=path_env)
        if on_path:
            return ToolLocation(on_path, "path", "", tool)

    for root in system_sdk_roots(env):
        hit = _lookup_in_root(root, tool, archs)
        if hit:
            return ToolLocation(hit[0], "system", hit[1], tool)

    raise SdkToolNotFound(
        f"找不到 {tool}。這支工具屬於 Windows SDK，本工具不會自動下載它"
        f"（見 docs/adr/0008）。\n"
        f"執行以下指令取得（約 22 MB，解壓後即可使用，不需安裝 SDK、不需系統管理員權限）：\n"
        f"    {fetch_command_hint()}\n"
        f"若這台機器上已有 SDK 或已解壓過該套件，也可以把設定 {SETTING_TOOLS_DIR} "
        f"指向它所在的目錄。"
    )


def _safe_extract_bin(zip_path, dest_dir):
    """把套件裡 `bin/` 底下的內容解壓到 dest_dir，回傳解壓出的檔案數。

    只取 `bin/`：套件另含 nuspec 與各種輔助檔案，本專案用不到，解壓它們
    只是佔用使用者的磁碟。

    每一項都驗證解壓後的絕對路徑仍落在 dest_dir 內。zip 的項目名稱來自
    下載回來的檔案，即使有雜湊驗證把關，也不該由「檔案內容可信」推導出
    「可以把它寫到它自己指定的任何路徑」。
    """
    dest_root = os.path.realpath(dest_dir)
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or not name.lower().startswith("bin/"):
                continue
            target = os.path.realpath(os.path.join(dest_root, *name.split("/")))
            if target != dest_root and not target.startswith(dest_root + os.sep):
                raise Exception(f"套件內含指向解壓目錄之外的項目，已中止：{info.filename}")
            os.makedirs(_long_path(os.path.dirname(target)), exist_ok=True)
            with z.open(info) as src, open(_long_path(target), "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def _installed_tools(root, environ=None):
    """檢查一個目錄底下 REQUIRED_TOOLS 是否齊全，回傳 {工具名: 路徑}。"""
    archs = arch_candidates(environ)
    found = {}
    for tool in REQUIRED_TOOLS:
        hit = _lookup_in_root(root, tool, archs)
        if hit:
            found[tool] = hit[0]
    return found


def fetch_tools(settings=None, environ=None, download=None, force=False, log=None):
    """取得 SDK 工具：下載固定版本的 NuGet 套件、驗證雜湊、解壓進快取。

    這是本模組唯一會存取網路的函式，且只由 CLI 的 `fetch-sdk-tools` 子指令
    呼叫——打包流程不呼叫它（ADR-0008 決定一）。

    `download` 是測試接縫，介面與 `builder._download_file()` 相同。預設值
    以延遲匯入取得該函式：`builder` 為了簽章功能會匯入本模組，模組層級的
    反向匯入會形成循環。

    失敗時不留下任何內容於快取目錄——解壓先落在同層的暫存目錄，確認必要
    工具齊全後才搬進定位。半套的快取比沒有快取更糟：它會在下一次被
    `find_tool()` 當成有效來源採用。
    """
    conf = _settings(settings)
    env = _environ(environ)
    target = cache_dir(conf, env)

    def emit(message):
        if log:
            log(message)

    existing = _installed_tools(target, env)
    if not force and len(existing) == len(REQUIRED_TOOLS):
        emit(f"SDK 工具已存在於快取，跳過下載：{target}")
        return FetchResult(target, PACKAGE_VERSION, existing)

    if download is None:
        from builder import _download_file as download  # noqa: PLC0415（見 docstring）

    parent = os.path.dirname(target) or "."
    os.makedirs(_long_path(parent), exist_ok=True)
    # 前綴短一點：暫存目錄名稱本身也會計入解壓目標的路徑長度。
    # dir 傳長路徑形式，mkdtemp 回傳的路徑因此也帶著該前綴，往下傳給
    # 解壓與更名時剛好是需要的形式；這個前綴不會外流到回傳值。
    staging = tempfile.mkdtemp(prefix="_tmp", dir=_long_path(parent))
    archive = os.path.join(staging, f"{PACKAGE_ID}.{PACKAGE_VERSION}.nupkg")
    try:
        emit(f"正在下載 {PACKAGE_ID} {PACKAGE_VERSION}...")
        download(PACKAGE_URL, archive, expected_sha256=PACKAGE_SHA256)

        extracted = os.path.join(staging, "x")
        os.makedirs(_long_path(extracted), exist_ok=True)
        _safe_extract_bin(archive, extracted)

        tools = _installed_tools(extracted, env)
        missing = [t for t in REQUIRED_TOOLS if t not in tools]
        if missing:
            raise Exception(
                f"下載的套件裡找不到 {'、'.join(missing)}，內容與預期不符，已中止。"
            )

        if os.path.isdir(_long_path(target)):
            shutil.rmtree(_long_path(target), ignore_errors=True)
        # 同一個磁碟區內的更名，不是複製：解壓已經完成，這一步要嘛整份
        # 就位、要嘛什麼都沒發生，不會留下解到一半的快取。
        os.replace(_long_path(extracted), _long_path(target))
    finally:
        shutil.rmtree(_long_path(staging), ignore_errors=True)

    tools = _installed_tools(target, env)
    emit(f"SDK 工具已取得：{target}")
    return FetchResult(target, PACKAGE_VERSION, tools)
