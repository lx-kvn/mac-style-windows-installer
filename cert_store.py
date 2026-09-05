"""
cert_store.py
--------------
在 Windows 的**個人**憑證存放區（`My`）裡定位簽章用的憑證。

## 為什麼需要這個模組

`signing` 原本只有「憑證是一個 `.pfx` 檔案」這一種形狀，而 `signtool` 沒有
以環境變數或標準輸入傳遞 `.pfx` 密碼的選項——密碼只能以 `/p` 出現在命令列
上，而同一台機器上任何行程都讀得到別的行程的命令列（稽核 S1）。

讓密碼離開命令列的唯一途徑，是把憑證與私鑰放進存放區、簽章時以
`/sha1 <指紋>` 指定：私鑰由作業系統保管。這個模組負責「照指紋把那張憑證
找出來」，以及「列出有哪些可以用」。

## 個人存放區不是信任存放區

**這裡講的是個人存放區**（`My`，放憑證與私鑰，簽章要用）。
[ADR-0005](docs/adr/0005-installer-never-installs-certificates-into-trust-stores.md)
談的是**信任**存放區（`Root`／`TrustedPeople`，決定這台電腦信不信任某個
簽章者）。兩者是不同的東西，本模組不讀也不寫後者，因此與那份 ADR 的兩項
決定皆不衝突。

## 只以指紋指認

不提供以主體名稱片段比對的入口（`signtool /n` 的做法）。理由見
[ADR-0014](docs/adr/0014-signing-certificate-is-identified-by-thumbprint-only.md)
決定二：存放區裡有兩張符合時 `signtool` 不報錯、逕自選一張，而選錯的後果
在 MSIX 模式下是終端使用者裝不起來，且錯誤訊息不指向這個原因。

## 兩個存放區都找

檢索範圍是「目前使用者」與「本機電腦」兩者。指紋在定義上唯一，兩處都找不會
產生選錯的風險；找到之後由 `signtool_arguments()` 依實際位置決定要不要帶
`/sm`。這件事工具查得出來，不該變成一個使用者必須答對的設定欄位
（ADR-0014 決定三）。
"""
import ctypes
import ctypes.wintypes as wintypes
import re
from collections import namedtuple

import cert_subject
import messages

# 存放區位置。字串而非數值，因為它會被印進建置紀錄給人看。
CURRENT_USER = "current_user"
LOCAL_MACHINE = "local_machine"

# `CertOpenStore` 的旗標。存放區位置編在 dwFlags 的高位元組。
_CERT_STORE_PROV_SYSTEM_W = 10
_CERT_STORE_READONLY_FLAG = 0x00008000
_CERT_SYSTEM_STORE_LOCATION_SHIFT = 16
_CERT_SYSTEM_STORE_CURRENT_USER = 1 << _CERT_SYSTEM_STORE_LOCATION_SHIFT
_CERT_SYSTEM_STORE_LOCAL_MACHINE = 2 << _CERT_SYSTEM_STORE_LOCATION_SHIFT

# `CertGetCertificateContextProperty` 的屬性編號。有沒有私鑰要看這一個——
# 沒有私鑰的憑證簽不了東西，列在清單上只會讓使用者選了之後才失敗。
_CERT_KEY_PROV_INFO_PROP_ID = 2
# 指紋即 SHA-1 雜湊，Windows 以這個屬性提供。
_CERT_SHA1_HASH_PROP_ID = 3

# 加強金鑰使用方法（EKU）的識別碼。清單要靠它篩掉不能簽程式碼的憑證——
# 實測開發機的個人存放區有 194 張，絕大多數是 Fiddler 之類的工具產生的
# TLS 憑證，全部列出來使用者找不到自己那一張，而那些交給 signtool 也簽不了。
OID_CODE_SIGNING = "1.3.6.1.5.5.7.3.3"
OID_ANY_USAGE = "2.5.29.37.0"

_THUMBPRINT_LENGTH = 40
_HEX = re.compile(r"^[0-9A-F]+$")
# 使用者會從三個地方複製指紋過來：certmgr 的內容窗格（每兩個字元一個空格）、
# PowerShell（連續大寫）、各種文件（冒號分隔）。三種都認得——不認的話使用者
# 會得到「找不到憑證」，而他手上那張明明就在存放區裡。
_SEPARATORS = " :- "


MESSAGES = {
    "zh-TW": {
        "thumbprint.required": "signing.cert_thumbprint 是空的。",
        "thumbprint.format": "signing.cert_thumbprint 必須是 {length} 個十六進位字元（0-9、A-F），收到的是「{value}」。指紋可以從 certmgr、PowerShell 或本工具的憑證清單指令取得，空格與冒號會自動忽略。",
    },
    "en": {
        "thumbprint.required": "signing.cert_thumbprint is empty.",
        "thumbprint.format": "signing.cert_thumbprint must be {length} hexadecimal characters (0-9, A-F); received \"{value}\". You can get a thumbprint from certmgr, from PowerShell, or from this tool's certificate listing command; spaces and colons are ignored.",
    },
}


def _t(key, lang=messages.DEFAULT_LANGUAGE, /, **params):
    return messages.translate(MESSAGES, key, lang, **params)


class StoreCertificate(namedtuple(
        "StoreCertificate",
        "thumbprint subject store has_private_key not_after usages")):
    """存放區裡的一張憑證。

    `store` 是 `CURRENT_USER`／`LOCAL_MACHINE` 之一，決定 `signtool` 要不要
    帶 `/sm`。`not_after` 是可以直接顯示的到期日字串，列清單時讓使用者分得
    出哪一張還有效。
    """

    __slots__ = ()

    def describe(self):
        """建置紀錄印的那一行。

        指紋是四十個十六進位字元，人不易辨識——ADR-0014 決定二接受這個代價，
        條件是工具把主體印出來補償。少了這一行，使用者無從確認自己簽的是
        哪一張憑證。
        """
        where = "目前使用者" if self.store == CURRENT_USER else "本機電腦"
        return f"使用憑證：{self.subject}（指紋 {self.thumbprint}，{where}的存放區）"


def normalize_thumbprint(raw):
    """把使用者貼過來的指紋轉成標準形狀：去分隔符、轉大寫。"""
    value = str(raw or "")
    for separator in _SEPARATORS:
        value = value.replace(separator, "")
    return value.strip().upper()


def validate_thumbprint(raw, lang=messages.DEFAULT_LANGUAGE):
    """檢查指紋的**形式**，通過回傳 None。

    形式與「存不存在」分開：前者不必碰存放區，可以在任何機器上測；後者要。
    兩件事的錯誤訊息對使用者的意義也不同——一個是打錯字，一個是憑證不在
    這台機器上。
    """
    value = normalize_thumbprint(raw)
    if not value:
        return _t("thumbprint.required", lang)
    if len(value) != _THUMBPRINT_LENGTH or not _HEX.match(value):
        return _t("thumbprint.format", lang, length=_THUMBPRINT_LENGTH,
                  value=str(raw).strip())
    return None


def signtool_arguments(certificate):
    """這張憑證要用哪幾個參數交給 `signtool`。

    **回傳的參數裡永遠不會有密碼**——這個模式存在的理由就是這件事。
    """
    args = ["/sha1", certificate.thumbprint]
    if certificate.store == LOCAL_MACHINE:
        # /sm 讓 signtool 改看本機電腦的存放區。位置由實際找到的地方決定，
        # 不由設定決定（ADR-0014 決定三）。
        args.append("/sm")
    return args


def _open_store(location):
    crypt32 = ctypes.WinDLL("crypt32.dll")
    crypt32.CertOpenStore.restype = ctypes.c_void_p
    return crypt32, crypt32.CertOpenStore(
        _CERT_STORE_PROV_SYSTEM_W, 0, None,
        location | _CERT_STORE_READONLY_FLAG, ctypes.c_wchar_p("My"))


def _property_bytes(crypt32, context, prop_id):
    size = wintypes.DWORD()
    if not crypt32.CertGetCertificateContextProperty(
            context, prop_id, None, ctypes.byref(size)):
        return None
    buffer = (ctypes.c_byte * size.value)()
    if not crypt32.CertGetCertificateContextProperty(
            context, prop_id, buffer, ctypes.byref(size)):
        return None
    return bytes(bytearray(buffer))


def _format_not_after(filetime):
    """FILETIME 轉成可以顯示的日期。轉不出來時回傳空字串——到期日只是清單上
    的輔助資訊，讓它成為一條會拋例外的路沒有道理。"""
    try:
        import datetime
        ticks = (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime
        epoch = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
        moment = epoch + datetime.timedelta(microseconds=ticks // 10)
        return moment.strftime("%Y-%m-%d")
    except Exception:
        return ""


class _CTL_USAGE(ctypes.Structure):
    _fields_ = [("cUsageIdentifier", wintypes.DWORD),
                ("rgpszUsageIdentifier", ctypes.POINTER(ctypes.c_char_p))]


def _can_code_sign(usages):
    """這組加強金鑰使用方法能不能拿來簽程式碼。

    空的代表憑證沒有宣告任何用途限制——那種憑證什麼都能簽，可以用。有宣告
    的則必須含程式碼簽章或「任何用途」。

    純函式，不碰存放區：這條規則要在任何機器上都測得到，而存放區的內容不在
    測試的控制範圍內。
    """
    if not usages:
        return True
    return OID_CODE_SIGNING in usages or OID_ANY_USAGE in usages


def _enhanced_key_usages(crypt32, context):
    """讀出憑證宣告的加強金鑰使用方法；沒有宣告時回傳空清單。

    `CertGetEnhancedKeyUsage` 對「沒有宣告」與「呼叫失敗」都回傳 0 個項目，
    兩者在這裡的處置相同（視為不受限制），因此不區分。
    """
    size = wintypes.DWORD()
    if not crypt32.CertGetEnhancedKeyUsage(context, 0, None, ctypes.byref(size)):
        return []
    buffer = (ctypes.c_byte * size.value)()
    if not crypt32.CertGetEnhancedKeyUsage(context, 0, buffer, ctypes.byref(size)):
        return []
    usage = ctypes.cast(buffer, ctypes.POINTER(_CTL_USAGE)).contents
    return [usage.rgpszUsageIdentifier[i].decode("ascii", "replace")
            for i in range(usage.cUsageIdentifier)]


def _read(crypt32, context, location):
    thumbprint_bytes = _property_bytes(crypt32, context, _CERT_SHA1_HASH_PROP_ID)
    if not thumbprint_bytes:
        return None
    try:
        subject = cert_subject.subject_string_from_context(context)
    except cert_subject.CertificateReadError:
        # 主體讀不出來的憑證對使用者沒有意義——清單上會是一行他認不出來的
        # 東西，選了也不知道選到什麼。略過。
        return None
    has_key = _property_bytes(crypt32, context, _CERT_KEY_PROV_INFO_PROP_ID) is not None
    store = CURRENT_USER if location == _CERT_SYSTEM_STORE_CURRENT_USER else LOCAL_MACHINE
    return StoreCertificate(
        thumbprint="".join(f"{b:02X}" for b in thumbprint_bytes),
        subject=subject,
        store=store,
        has_private_key=has_key,
        not_after=_format_not_after(context.contents.pCertInfo.contents.NotAfter),
        usages=tuple(_enhanced_key_usages(crypt32, context)),
    )


def _enumerate(location):
    """列舉一個存放區裡的憑證。存放區開不起來時回傳空清單。

    開不起來是預期中的結局之一——「本機電腦」的存放區在權限不足時讀不到，
    而那不是故障，是正確的權限檢查。為此中止整個流程沒有道理：另一個存放區
    裡可能就有使用者要的那張。
    """
    crypt32, handle = _open_store(location)
    if not handle:
        return []
    crypt32.CertEnumCertificatesInStore.restype = ctypes.POINTER(cert_subject.CERT_CONTEXT)
    crypt32.CertEnumCertificatesInStore.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(cert_subject.CERT_CONTEXT)]
    found = []
    try:
        context = crypt32.CertEnumCertificatesInStore(handle, None)
        while context:
            entry = _read(crypt32, context, location)
            if entry is not None:
                found.append(entry)
            # CertEnumCertificatesInStore 會自己釋放前一個 context，不要另外
            # 呼叫 CertFreeCertificateContext——那會造成重複釋放。
            context = crypt32.CertEnumCertificatesInStore(handle, context)
    finally:
        crypt32.CertCloseStore(ctypes.c_void_p(handle), 0)
    return found


def list_signing_certificates():
    """兩個個人存放區裡可以用來簽章的憑證。

    同一張憑證兩個存放區都有時只回報一次，以「目前使用者」那一份為準——
    列出兩行會讓使用者以為自己有兩張，而 `signtool` 用哪一個存放區都簽得出
    同一個結果。

    兩種憑證不列入，理由相同——列出來只會讓使用者選了之後才失敗：沒有私鑰
    的（簽不了東西），以及宣告的用途不含程式碼簽章的（`_can_code_sign()`）。
    """
    seen = {}
    for location in (_CERT_SYSTEM_STORE_CURRENT_USER, _CERT_SYSTEM_STORE_LOCAL_MACHINE):
        for entry in _enumerate(location):
            if not entry.has_private_key or not _can_code_sign(entry.usages):
                continue
            seen.setdefault(entry.thumbprint, entry)
    return sorted(seen.values(), key=lambda e: (e.subject.lower(), e.thumbprint))


def find_by_thumbprint(thumbprint):
    """照指紋找出那張憑證；找不到回傳 None。

    形式不合法時也回傳 None 而不是拋例外：呼叫端已經先做過
    `validate_thumbprint()`，這裡再拋一次只是多一條要接的路。
    """
    wanted = normalize_thumbprint(thumbprint)
    if not wanted:
        return None
    for entry in list_signing_certificates():
        if entry.thumbprint == wanted:
            return entry
    return None
