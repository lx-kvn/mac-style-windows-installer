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

本模組只驗證與正規化，不產生套件清單（`AppxManifest.xml`）——後者見
`msix_manifest.py`。

`msix.icons` 這裡只檢查鍵名與結構，不檢查圖片本身：那需要讀檔案，而本模組
拿到的是設定值、不保證路徑相對於什麼。圖片尺寸的檢查在 `packaging_core`，
那裡才知道路徑怎麼解析（規則見 `png_size.py`）。
"""
import re

import messages

# 第六輪查證結果第一項：依據是微軟支援矩陣的起點（`.msix` 格式支援表與桌面
# 功能支援表皆自 1809 起算），不是格式的絕對下限（1709／10.0.16299.0）。
# 填入絕對下限等同對兩個已終止支援、且不在該矩陣內的版本作出無人驗證的承諾。
DEFAULT_MIN_WINDOWS_VERSION = "10.0.17763.0"

# 三個圖示位置在套件清單裡宣告的尺寸。各自的最小邊長不同，用同一個門檻
# 會把一張完全夠用的 44x44 工作列圖示擋下來。
ICON_MINIMUM_SIZES = {"tile": 150, "taskbar": 44, "store": 50}

# 沒有個別覆蓋時，同一張 png_icon 要同時填三個位置，因此要滿足最大的
# 那一個（第五輪決議第一項）。
SHARED_ICON_MINIMUM = max(ICON_MINIMUM_SIZES.values())

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


# 訊息表。機制在 messages.py，那裡也說明了為什麼表留在各模組而不是集中一張。
MESSAGES = {
    "zh-TW": {
        "identity.required": "msix.identity_name 是必填的：它是系統用來判定「兩包套件是否為同一個應用程式」的唯一依據，一經發布即不可變更，改了系統會當成另一個不相關的應用程式並存安裝。本工具不由 app_name 推導這個值，因為會踩到該情境的正是不知道需要覆蓋它的那些人。",
        "identity.length": "msix.identity_name 長度必須在 {minimum} 到 {maximum} 個字元之間，目前是 {actual} 個。",
        "identity.charset": "msix.identity_name 只能使用英文字母、數字、句點與連字號，不能有空白或其他字元。",
        "identity.reserved": "msix.identity_name 不能是保留字「{name}」（比對不分大小寫）。",
        "identity.reserved_prefix": "msix.identity_name 不能以「{prefix}.」這類保留字開頭，也不能以 xn-- 開頭。",
        "identity.trailing_dot": "msix.identity_name 不能以句點結尾。",
        "identity.contains_xn": "msix.identity_name 不能包含「.xn--」。",
        "version.required": "版本號是必填的。",
        "version.not_numeric": "MSIX 模式的版本號必須是一到四段純數字，「{raw}」不符合。帶預發布後綴的版本號（例如 1.0.0-rc1）在 MSIX 模式無法使用：捨棄後綴會讓它與 1.0.0 的版本號完全相同，而 Windows 判斷要不要升級看的正是版本號遞增，系統會認定兩者是同一版而不執行升級，且這個問題在打包階段不會有任何錯誤，要到升級失敗才發現。",
        "version.segment_too_large": "MSIX 模式的版本號每一段都不能超過 {maximum}，「{raw}」超出範圍。",
        "min_version.format": "msix.min_windows_version 必須是四段純數字（例如 {example}），收到的是「{value}」。",
        "icons.not_object": "msix.icons 必須是一個物件（字典），例如 {{\"tile\": \"tile.png\"}}。",
        "icons.unknown_keys": "msix.icons 只認得 {known} 這三個位置，收到的還有：{unknown}。",
        "block.not_object": "msix 必須是一個物件（字典），例如 {{\"identity_name\": \"...\"}}。",
        "subject.required": "msix.certificate_subject 是必填的：它會寫進套件清單的發行者欄位，而該值必須與簽章憑證上記載的名稱完全一致（例如 CN=某某, O=某某, C=TW），不一致時系統直接拒絕安裝，且錯誤訊息不會指向這個原因。",
        "subject.mismatch": "msix.certificate_subject 與簽章憑證上記載的名稱不一致，這樣簽出來的套件系統會直接拒絕安裝，而且它的錯誤訊息不會指向這個原因。\n    設定裡寫的：{configured}\n    憑證上實際是：{actual}\n    （把設定改成憑證上那一個，或清空這個欄位讓工具自動填入。）",
        "list.separator": "、",
    },
    "en": {
        "identity.required": "msix.identity_name is required: it is the only thing the system uses to decide whether two packages are the same application. It can never be changed once published — change it and the system installs the result alongside the old one as an unrelated application. This tool does not derive it from app_name, because the people who would hit that trap are exactly the ones who do not know they need to override it.",
        "identity.length": "msix.identity_name must be between {minimum} and {maximum} characters; it is currently {actual}.",
        "identity.charset": "msix.identity_name may only use letters, digits, periods and hyphens — no spaces or other characters.",
        "identity.reserved": "msix.identity_name cannot be the reserved name \"{name}\" (compared case-insensitively).",
        "identity.reserved_prefix": "msix.identity_name cannot start with a reserved prefix like \"{prefix}.\", nor with xn--.",
        "identity.trailing_dot": "msix.identity_name cannot end with a period.",
        "identity.contains_xn": "msix.identity_name cannot contain \".xn--\".",
        "version.required": "A version number is required.",
        "version.not_numeric": "An MSIX version number must be one to four groups of digits; \"{raw}\" does not qualify. Version numbers with a prerelease suffix (1.0.0-rc1, for instance) cannot be used in MSIX mode: dropping the suffix would make it identical to 1.0.0, and what Windows uses to decide whether to upgrade is precisely an increasing version number — it would treat the two as the same version and skip the upgrade. That failure produces no error at packaging time; it only surfaces when an upgrade fails.",
        "version.segment_too_large": "No group in an MSIX version number may exceed {maximum}; \"{raw}\" is out of range.",
        "min_version.format": "msix.min_windows_version must be four groups of digits ({example}, for instance); received \"{value}\".",
        "icons.not_object": "msix.icons must be an object (a dictionary), for instance {{\"tile\": \"tile.png\"}}.",
        "icons.unknown_keys": "msix.icons only recognises the three positions {known}; it also received: {unknown}.",
        "block.not_object": "msix must be an object (a dictionary), for instance {{\"identity_name\": \"...\"}}.",
        "subject.required": "msix.certificate_subject is required: it goes into the package manifest's publisher field, and it must match the name recorded on your signing certificate exactly (CN=Something, O=Something, C=TW, for instance). If it does not match, the system simply refuses to install, and its error message does not point at this cause.",
        "subject.mismatch": "msix.certificate_subject does not match the name recorded on the signing certificate. A package signed this way is refused outright by the system, and its error message does not point at this cause.\n    In the config: {configured}\n    On the certificate: {actual}\n    (Set the config to the certificate's value, or clear this field and let the tool fill it in.)",
        "list.separator": ", ",
    },
}


def _t(key, lang=messages.DEFAULT_LANGUAGE, /, **params):
    return messages.translate(MESSAGES, key, lang, **params)


class InvalidVersion(Exception):
    """版本號無法轉成 MSIX 要求的四段純數字形式。

    攜帶訊息表的鍵與參數而非現成句子，理由同 png_size.NotAPng：留著現成
    句子的話，呼叫端會直接印它，翻譯就永遠只做了一半。
    """

    def __init__(self, key, **params):
        self.key = key
        self.params = params
        super().__init__(_t(key, **params))

    def localized(self, lang=messages.DEFAULT_LANGUAGE):
        return _t(self.key, lang, **self.params)


def validate_identity_name(name, lang=messages.DEFAULT_LANGUAGE):
    """檢查套件身分名稱，通過回傳 None，否則回傳錯誤訊息。

    訊息一律指名 `identity_name` 這個欄位：它不由任何其他欄位推導
    （ADR-0007 決定一），使用者看到訊息時要知道該去改哪裡。
    """
    name = (name or "").strip()
    if not name:
        return _t("identity.required", lang)
    if not IDENTITY_NAME_MIN <= len(name) <= IDENTITY_NAME_MAX:
        return _t("identity.length", lang, minimum=IDENTITY_NAME_MIN,
                  maximum=IDENTITY_NAME_MAX, actual=len(name))
    if not _IDENTITY_NAME_ALLOWED.match(name):
        return _t("identity.charset", lang)
    lowered = name.lower()
    if lowered in _RESERVED_NAMES:
        return _t("identity.reserved", lang, name=name)
    if any(lowered.startswith(prefix) for prefix in _RESERVED_PREFIXES):
        return _t("identity.reserved_prefix", lang, prefix=name.split(".")[0])
    if name.endswith("."):
        return _t("identity.trailing_dot", lang)
    if ".xn--" in lowered:
        return _t("identity.contains_xn", lang)
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
        raise InvalidVersion("version.required")
    parts = raw.split(".")
    if len(parts) > 4 or not all(part.isdigit() for part in parts):
        raise InvalidVersion("version.not_numeric", raw=raw)
    numbers = [int(part) for part in parts]
    if any(n > VERSION_SEGMENT_MAX for n in numbers):
        raise InvalidVersion("version.segment_too_large",
                             maximum=VERSION_SEGMENT_MAX, raw=raw)
    numbers += [0] * (4 - len(numbers))
    return ".".join(str(n) for n in numbers)


def _validate_min_windows_version(value, lang=messages.DEFAULT_LANGUAGE):
    if not value:
        return DEFAULT_MIN_WINDOWS_VERSION, None
    try:
        return to_quad_version(value), None
    except InvalidVersion:
        return None, _t("min_version.format", lang,
                        example=DEFAULT_MIN_WINDOWS_VERSION, value=value)


def _validate_icons(block, lang=messages.DEFAULT_LANGUAGE):
    """檢查 `msix.icons` 的鍵，回傳 (正規化後的字典, 錯誤訊息)。

    只檢查鍵名與結構，不在這裡檢查圖片本身——那需要讀檔案，而這個
    模組拿到的是設定值、不保證路徑相對於什麼。圖片尺寸的檢查在
    `packaging_core`，那裡才知道路徑怎麼解析（見 `png_size.py`）。
    """
    if not block:
        return {}, None
    if not isinstance(block, dict):
        return None, _t("icons.not_object", lang)
    unknown = [k for k in block if k not in ICON_MINIMUM_SIZES]
    if unknown:
        separator = _t("list.separator", lang)
        return None, _t("icons.unknown_keys", lang,
                        known=separator.join(ICON_MINIMUM_SIZES),
                        unknown=separator.join(unknown))
    return {k: str(v or "").strip() for k, v in block.items() if str(v or "").strip()}, None


def validate(block, cert_subject=None, lang=messages.DEFAULT_LANGUAGE):
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
        return None, _t("block.not_object", lang)

    problems = []
    identity_name = str(block.get("identity_name", "") or "").strip()
    identity_error = validate_identity_name(identity_name, lang)
    if identity_error:
        problems.append(identity_error)

    certificate_subject = str(block.get("certificate_subject", "") or "").strip()
    if not certificate_subject and cert_subject:
        # 憑證讀得到就自動填入，使用者不必自己去查憑證上的字串長什麼樣——
        # 那個字串的形式（順序、分隔符、引號規則）並不直覺，見 cert_subject.py。
        certificate_subject = cert_subject
    elif not certificate_subject:
        problems.append(_t("subject.required", lang))
    elif cert_subject and certificate_subject != cert_subject:
        # 逐字比對，不做大小寫或空白的正規化：系統比對時也不做，抹平差異
        # 只會讓打包通過而把失敗推遲到終端使用者安裝的時候。
        problems.append(_t("subject.mismatch", lang,
                           configured=certificate_subject, actual=cert_subject))

    icons, icons_error = _validate_icons(block.get("icons"), lang)
    if icons_error:
        problems.append(icons_error)

    min_version, min_version_error = _validate_min_windows_version(
        str(block.get("min_windows_version", "") or "").strip(), lang
    )
    if min_version_error:
        problems.append(min_version_error)

    if problems:
        return None, "\n".join(problems)
    return {
        "identity_name": identity_name,
        "certificate_subject": certificate_subject,
        "min_windows_version": min_version,
        "icons": icons,
    }, None
