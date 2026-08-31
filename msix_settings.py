"""
msix_settings.py
-----------------
打包設定裡 `msix` 這個巢狀區塊的驗證與正規化。欄位命名見
`docs/proposals/MSIX輸出規劃.md`「第九輪定案決議」：

```json
"msix": {
  "identity_name": "MyCompany.MyApp",
  "certificate_subject": "CN=My Company, O=My Company, C=TW",
  "min_windows_version": "10.0.17763.0"
}
```

檢查在 `packaging_core.validate_and_build_pack_data()` 這個純函式裡執行，
於流程產生任何副作用（清空 `dist/`、`build/`）之前攔截——此慣例由
[ADR-0003](docs/adr/0003-allow-prerelease-suffix-in-version-string.md) 決定四
建立，[ADR-0007](docs/adr/0007-package-identity-name-is-an-explicit-required-field.md)
決定三沿用。

本模組只驗證與正規化，不產生套件清單（`AppxManifest.xml`）——後者尚未實作。
`msix.icons`（第五輪決議第一項的三張圖示個別覆蓋）也尚未納入：該決議的成立
以「尺寸與宣告不符的圖示是否會被系統拒絕部署」這項未驗證的前提為條件，
前提若不成立，整個決議要改採自動縮放或要求使用者提供三張，欄位形狀會跟著變。
"""
import re

# 第六輪查證結果第一項：依據是微軟支援矩陣的起點（`.msix` 格式支援表與桌面
# 功能支援表皆自 1809 起算），不是格式的絕對下限（1709／10.0.16299.0）。
# 填入絕對下限等同對兩個已終止支援、且不在該矩陣內的版本作出無人驗證的承諾。
DEFAULT_MIN_WINDOWS_VERSION = "10.0.17763.0"

# 版本號每段的上限，官方定義 DotQuad 的最大值為 65535.65535.65535.65535。
VERSION_SEGMENT_MAX = 65535

# --- 套件身分名稱的字元規則（官方稱為 Package String）---------------------
#
# 這幾條規則來自 Microsoft 的 Package String 定義，不是憑印象寫的。其中
# 三條憑直覺不會想到：保留字的比對是大小寫不敏感的（只擋小寫等於漏掉一半）、
# 不能以 `xn--`（punycode 前綴）開頭、不能包含 `.xn--`。
IDENTITY_NAME_MIN = 3
IDENTITY_NAME_MAX = 50
_IDENTITY_NAME_ALLOWED = re.compile(r"^[A-Za-z0-9.\-]+$")

_RESERVED_NAMES = (
    ".", "..",
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
)
# 「不能以保留字加句點開頭」的清單不含 "." 與 ".."：那兩個本身就以句點開頭，
# 而以句點開頭的名稱另有規則涵蓋（結尾規則與長度規則）。
_RESERVED_PREFIXES = tuple(
    f"{name}." for name in _RESERVED_NAMES if name not in (".", "..")
) + ("xn--",)


class InvalidVersion(Exception):
    """版本號無法轉成 MSIX 要求的四段純數字形式。"""


def validate_identity_name(name):
    """檢查套件身分名稱，通過回傳 None，否則回傳錯誤訊息。

    訊息一律指名 `identity_name` 這個欄位：它不由任何其他欄位推導
    （ADR-0007 決定一），使用者看到訊息時要知道該去改哪裡。
    """
    name = (name or "").strip()
    if not name:
        return (
            "msix.identity_name 是必填的：它是系統用來判定「兩包套件是否為同一個"
            "應用程式」的唯一依據，一經發布即不可變更，改了系統會當成另一個不相關"
            "的應用程式並存安裝。本工具不由 app_name 推導這個值，因為會踩到該情境"
            "的正是不知道需要覆蓋它的那些人。"
        )
    if not IDENTITY_NAME_MIN <= len(name) <= IDENTITY_NAME_MAX:
        return (
            f"msix.identity_name 長度必須在 {IDENTITY_NAME_MIN} 到 "
            f"{IDENTITY_NAME_MAX} 個字元之間，目前是 {len(name)} 個。"
        )
    if not _IDENTITY_NAME_ALLOWED.match(name):
        return (
            "msix.identity_name 只能使用英文字母、數字、句點與連字號，"
            "不能有空白或其他字元。"
        )
    lowered = name.lower()
    if lowered in _RESERVED_NAMES:
        return f"msix.identity_name 不能是保留字「{name}」（比對不分大小寫）。"
    if any(lowered.startswith(prefix) for prefix in _RESERVED_PREFIXES):
        return (
            f"msix.identity_name 不能以「{name.split('.')[0]}.」這類保留字開頭，"
            "也不能以 xn-- 開頭。"
        )
    if name.endswith("."):
        return "msix.identity_name 不能以句點結尾。"
    if ".xn--" in lowered:
        return "msix.identity_name 不能包含「.xn--」。"
    return None


def to_quad_version(version):
    """把設定裡的版本號轉成 MSIX 要求的四段純數字形式。

    三段以下自動補零到四段，這是無損轉換。帶預發布後綴（`1.0.0-rc1`）的
    版本號直接報錯，不捨棄後綴——捨棄之後 `1.0.0-rc1` 與 `1.0.0` 的版本號
    完全相同，Windows 判定套件是否需要升級的依據正是版本號遞增，系統會認定
    兩者為同一版本而不執行升級。該問題在打包階段不產生任何錯誤，要到實際
    升級失敗時才會發現（第二輪決議第十項）。

    也不把後綴編碼成第四段數字：那個對應規則由工具自行定義、使用者未曾要求，
    且規則一經確立即不可變更（變更會使已發布的套件版本序中斷）。
    """
    raw = (version or "").strip()
    if not raw:
        raise InvalidVersion("版本號是必填的。")
    parts = raw.split(".")
    if len(parts) > 4 or not all(part.isdigit() for part in parts):
        raise InvalidVersion(
            f"MSIX 模式的版本號必須是一到四段純數字，「{raw}」不符合。"
            "帶預發布後綴的版本號（例如 1.0.0-rc1）在 MSIX 模式無法使用："
            "捨棄後綴會讓它與 1.0.0 的版本號完全相同，而 Windows 判斷要不要"
            "升級看的正是版本號遞增，系統會認定兩者是同一版而不執行升級，"
            "且這個問題在打包階段不會有任何錯誤，要到升級失敗才發現。"
        )
    numbers = [int(part) for part in parts]
    if any(n > VERSION_SEGMENT_MAX for n in numbers):
        raise InvalidVersion(
            f"MSIX 模式的版本號每一段都不能超過 {VERSION_SEGMENT_MAX}，「{raw}」超出範圍。"
        )
    numbers += [0] * (4 - len(numbers))
    return ".".join(str(n) for n in numbers)


def _validate_min_windows_version(value):
    if not value:
        return DEFAULT_MIN_WINDOWS_VERSION, None
    try:
        return to_quad_version(value), None
    except InvalidVersion:
        return None, (
            f"msix.min_windows_version 必須是四段純數字（例如 "
            f"{DEFAULT_MIN_WINDOWS_VERSION}），收到的是「{value}」。"
        )


def validate(block, cert_subject=None):
    """驗證並正規化 `msix` 區塊，回傳 (normalized, error_message)。

    錯誤一次列出全部，與引擎相容性檢查（`install_engine.py`）的作法一致：
    切換到 MSIX 引擎通常會一次缺好幾個欄位，逐條回報會讓使用者每補一個就
    重跑一次建置。

    `cert_subject`：從簽章憑證讀出的發行者字串（`cert_subject.py`），憑證
    不在本機時為 None。第二輪決議第十一項要求兩種來源都支援——兩截式流程的
    第一個步驟即產出含發行者宣告的套件清單，而該步驟先於簽章發生，雲端代簽
    情境下工具在該時點拿不到憑證，因此不能只做自動讀取。
    """
    if block is None:
        block = {}
    if not isinstance(block, dict):
        return None, "msix 必須是一個物件（字典），例如 {\"identity_name\": \"...\"}。"

    problems = []
    identity_name = str(block.get("identity_name", "") or "").strip()
    identity_error = validate_identity_name(identity_name)
    if identity_error:
        problems.append(identity_error)

    certificate_subject = str(block.get("certificate_subject", "") or "").strip()
    if not certificate_subject and cert_subject:
        # 憑證讀得到就自動填入，使用者不必自己去查憑證上的字串長什麼樣——
        # 那個字串的形式（順序、分隔符、引號規則）並不直覺，見 cert_subject.py。
        certificate_subject = cert_subject
    elif not certificate_subject:
        problems.append(
            "msix.certificate_subject 是必填的：它會寫進套件清單的發行者欄位，"
            "而該值必須與簽章憑證上記載的名稱完全一致（例如 CN=某某, O=某某, C=TW），"
            "不一致時系統直接拒絕安裝，且錯誤訊息不會指向這個原因。"
        )
    elif cert_subject and certificate_subject != cert_subject:
        # 逐字比對，不做大小寫或空白的正規化：系統比對時也不做，抹平差異
        # 只會讓打包通過而把失敗推遲到終端使用者安裝的時候。
        problems.append(
            "msix.certificate_subject 與簽章憑證上記載的名稱不一致，"
            "這樣簽出來的套件系統會直接拒絕安裝，而且它的錯誤訊息不會指向這個原因。\n"
            f"    設定裡寫的：{certificate_subject}\n"
            f"    憑證上實際是：{cert_subject}\n"
            "    （把設定改成憑證上那一個，或清空這個欄位讓工具自動填入。）"
        )

    min_version, min_version_error = _validate_min_windows_version(
        str(block.get("min_windows_version", "") or "").strip()
    )
    if min_version_error:
        problems.append(min_version_error)

    if problems:
        return None, "\n".join(problems)
    return {
        "identity_name": identity_name,
        "certificate_subject": certificate_subject,
        "min_windows_version": min_version,
    }, None
