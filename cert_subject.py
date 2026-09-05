"""
cert_subject.py
----------------
從簽章憑證讀出 MSIX 套件清單的發行者（`Identity/@Publisher`）該填的字串。

該值必須與簽章憑證上記載的名稱**完全一致**，不一致時系統直接拒絕安裝，
而且錯誤訊息不指向這個原因（見 `docs/proposals/MSIX輸出規劃.md` 第二輪
決議第十一項）。因此這個字串的形式不能猜。

## 為什麼是呼叫 Windows，而不是自己組字串

實測結果（規劃文件「第十輪 spike 結果」）：`makeappx` 與 `signtool` 接受的
形式，就是 Windows 的 `crypt32!CertNameToStrW` 產生的那一個。自行組字串
會在三個地方出錯，前兩個在多數憑證上不會顯現、第三個要到真實公司名稱才
踩到：

1. **順序是反的。** 憑證宣告 `CN, O, C` 時，正確的字串是
   `C=..., O=..., CN=...`。用宣告順序組出來的字串 `makeappx` 會收，
   `signtool` 則以 `0x8007000b` 拒絕——與「發行者根本填錯」的錯誤完全相同。
2. **分隔符必須是逗號加空格。** `makeappx` 拒絕沒有空格的形式，而
   `cryptography` 的 `rfc4514_string()` 給的正是無空格的形式。
3. **值裡含特殊字元時的引號規則不同。** RFC 4514 用反斜線轉義
   （`CN=Foo\\, Inc.`），Windows 用雙引號包起來（`CN="Foo, Inc."`），
   而 `makeappx` 只接受後者。需要引號的情形包含逗號、加號、等號、雙引號
   （雙引號本身要重複成兩個）、以及前後有空白。公司名稱含逗號
   （`Foo, Inc.`）是常見情形，這一條並非罕見邊界。

把規則交給 Windows 自己，正確與否就不依賴本專案的推論。作法比照本專案
既有的 ctypes 路線（`Rstrtmgr.dll`、Win32 API 直接呼叫）。
"""
import ctypes
import ctypes.wintypes as wintypes
import os

import messages

X509_ASN_ENCODING = 0x00000001
# CERT_X500_NAME_STR：X.500 的字串表示（`CN=..., O=...`）。
# CERT_NAME_STR_REVERSE_FLAG：以與 DER 相反的順序輸出，即 X.500 的慣例。
# 兩者合用產生的結果與 .NET 的 X509Certificate2.Subject 完全一致（實測）。
CERT_X500_NAME_STR = 3
CERT_NAME_STR_REVERSE_FLAG = 0x02000000



# 訊息表。機制在 messages.py，那裡也說明了為什麼表留在各模組。
MESSAGES = {
    "zh-TW": {
        "empty_subject": "憑證主體是空的，無法取得發行者名稱。",
        "unparsable": "無法從憑證主體取得發行者名稱：Windows 無法解析這份主體資料。",
        "empty_result": "無法從憑證主體取得發行者名稱：轉換結果為空。",
        "not_found": "找不到憑證檔案：{path}",
        "missing_dependency": "缺少 cryptography 套件，無法讀取憑證：{reason}",
        "load_failed": "無法讀取憑證 {name}：{reason}（密碼不正確、或檔案不是有效的 .pfx 都會造成這個結果）",
        "key_only": "憑證檔案 {name} 裡沒有憑證，只有私鑰。",
    },
    "en": {
        "empty_subject": "The certificate subject is empty; the publisher name cannot be read.",
        "unparsable": "Cannot read the publisher name from the certificate subject: Windows could not parse this subject data.",
        "empty_result": "Cannot read the publisher name from the certificate subject: the conversion produced nothing.",
        "not_found": "Certificate file not found: {path}",
        "missing_dependency": "The cryptography package is missing, so the certificate cannot be read: {reason}",
        "load_failed": "Cannot read the certificate {name}: {reason} (a wrong password, or a file that is not a valid .pfx, both produce this result)",
        "key_only": "The certificate file {name} contains no certificate, only a private key.",
    },
}


def _t(key, lang=messages.DEFAULT_LANGUAGE, /, **params):
    return messages.translate(MESSAGES, key, lang, **params)


class CertificateReadError(Exception):
    """憑證讀不出來，或主體無法轉成字串。

    攜帶的是訊息表的鍵與參數，不是現成的句子——留著現成句子的話，呼叫端
    會直接印它，翻譯就永遠只做了一半。`str(e)` 仍然給出預設語言的句子，
    讓「直接印出例外」這種既有寫法的行為不變。
    """

    def __init__(self, key, **params):
        self.key = key
        self.params = params
        super().__init__(_t(key, **params))

    def localized(self, lang=messages.DEFAULT_LANGUAGE):
        return _t(self.key, lang, **self.params)


class CERT_NAME_BLOB(ctypes.Structure):
    """一段長度加指標的位元組。Win32 的 `CRYPT_INTEGER_BLOB`、`CERT_NAME_BLOB`
    等等都是同一個形狀，因此只宣告一份。"""

    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class CERT_INFO(ctypes.Structure):
    """憑證的解析結果。實際只會讀到 `Subject` 與 `NotAfter`，但它們的位置由
    前面所有欄位的大小決定，因此整個結構都要宣告正確。`SignatureAlgorithm`
    與 `SubjectPublicKeyInfo` 以位元組陣列占位——內容用不到，只有大小重要。"""

    _fields_ = [("dwVersion", wintypes.DWORD),
                ("SerialNumber", CERT_NAME_BLOB),
                ("SignatureAlgorithm", ctypes.c_byte * 24),
                ("Issuer", CERT_NAME_BLOB),
                ("NotBefore", wintypes.FILETIME),
                ("NotAfter", wintypes.FILETIME),
                ("Subject", CERT_NAME_BLOB),
                ("SubjectPublicKeyInfo", ctypes.c_byte * 32),
                ("IssuerUniqueId", CERT_NAME_BLOB),
                ("SubjectUniqueId", CERT_NAME_BLOB),
                ("cExtension", wintypes.DWORD),
                ("rgExtension", ctypes.c_void_p)]


class CERT_CONTEXT(ctypes.Structure):
    _fields_ = [("dwCertEncodingType", wintypes.DWORD),
                ("pbCertEncoded", ctypes.POINTER(ctypes.c_byte)),
                ("cbCertEncoded", wintypes.DWORD),
                ("pCertInfo", ctypes.POINTER(CERT_INFO)),
                ("hCertStore", ctypes.c_void_p)]


def subject_string_from_context(context):
    """從一個 `CERT_CONTEXT` 指標取出主體字串。

    `subject_string_from_der()` 的另一個入口：憑證來自存放區時拿到的是
    context，來自 `.pfx` 時拿到的是 DER。字串的組法兩邊共用同一份——那件事
    的規則不直覺（見模組說明），有兩份就會有一份是錯的。
    """
    blob = context.contents.pCertInfo.contents.Subject
    return subject_string_from_der(
        ctypes.string_at(blob.pbData, blob.cbData))


def subject_string_from_der(der_bytes):
    """把憑證主體的 DER 交給 Windows，取回它的 X.500 字串表示。

    讀不出來時拋例外而不是回傳空字串：空字串會被呼叫端當成「這張憑證沒有
    主體」，進而在套件清單裡寫出一個空的發行者，而那個錯誤要到終端使用者
    安裝失敗時才會顯現。
    """
    if not der_bytes:
        raise CertificateReadError("empty_subject")
    buffer = (ctypes.c_byte * len(der_bytes)).from_buffer_copy(der_bytes)
    blob = CERT_NAME_BLOB(
        len(der_bytes), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    )
    crypt32 = ctypes.WinDLL("crypt32.dll")
    flags = CERT_X500_NAME_STR | CERT_NAME_STR_REVERSE_FLAG
    # 先問長度再配置緩衝區，這是 CertNameToStrW 的標準用法。回傳值含結尾的
    # 空字元，因此 1 代表「只有結尾空字元」，也就是轉不出任何內容。
    size = crypt32.CertNameToStrW(X509_ASN_ENCODING, ctypes.byref(blob), flags, None, 0)
    if size <= 1:
        raise CertificateReadError("unparsable")
    out = ctypes.create_unicode_buffer(size)
    crypt32.CertNameToStrW(X509_ASN_ENCODING, ctypes.byref(blob), flags, out, size)
    if not out.value:
        raise CertificateReadError("empty_result")
    return out.value


def read_from_pfx(pfx_path, password):
    """讀 `.pfx` 憑證檔，回傳套件清單的發行者該填的字串。

    `cryptography` 只用來解開 PKCS#12 與取出主體的 DER，字串形式仍由
    Windows 決定（見模組說明）。該套件已是本專案的相依（`install_encryption.py`
    的安裝密碼保護也用它），不因此新增相依。
    """
    if not pfx_path or not os.path.isfile(pfx_path):
        raise CertificateReadError("not_found", path=pfx_path)
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
    except ImportError as e:  # pragma: no cover - 相依缺失由呼叫端的環境檢查涵蓋
        raise CertificateReadError("missing_dependency", reason=e)
    try:
        with open(pfx_path, "rb") as f:
            data = f.read()
        _, certificate, _ = pkcs12.load_key_and_certificates(
            data, (password or "").encode("utf-8")
        )
    except Exception as e:
        raise CertificateReadError("load_failed", name=os.path.basename(pfx_path), reason=e)
    if certificate is None:
        raise CertificateReadError("key_only", name=os.path.basename(pfx_path))
    return subject_string_from_der(certificate.subject.public_bytes())
