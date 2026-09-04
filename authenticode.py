"""
authenticode.py
----------------
驗證一個檔案的 Authenticode 數位簽章：簽章是否有效，以及簽章者是誰。

## 為什麼需要這個模組

稽核 S2（見 `docs/investigations/MSIX稽核與缺陷修正.md`）：
`webview2_runtime.acquire()` 把微軟的載入器下載到暫存目錄後直接執行，
把關只有 HTTPS 加上 Content-Length 比對——後者防的是「下載被截斷」，不是
「下載回來的是不是那個東西」。

這件事的判準已經寫在 `sdk_tools.py` 的模組說明裡：「判準不是『打包時是否
連網』，而是下載物在打包機器上是被內嵌還是被執行——後者的最壞情況是打包
機器遭入侵」。`webview2_runtime` 執行下載物的位置是**終端使用者的機器**，
而安裝檔常常是已提升權限的，同一條判準推出來的強度應該更高，不是更低。

`sdk_tools` 用的是釘死版本加 SHA-256。那個做法在這裡不適用：微軟的
Evergreen 載入器是一個內容會變動的永久連結，釘不住雜湊。可以驗的是簽章。

## 兩道關卡

1. **`WinVerifyTrust`**——簽章本身有效、憑證鏈接得到這台機器信任的根。
2. **簽章者的組織名稱**——只有第一道的話，任何一張有效憑證簽出來的檔案都
   會通過，而遭竊的程式碼簽章憑證是真實存在的東西。

第二道有一個不直覺的地方：**不能用「主體字串裡有沒有出現這段文字」判斷**。
`O=Microsoft Corporation Fake` 含有 `O=Microsoft Corporation` 這段文字，
用子字串比對會放行。比對必須以完整的 RDN 為單位，見 `_matches_organization()`。

## 為什麼是 ctypes

比照本專案既有的路線（`cert_subject.py` 的 `crypt32`、
`explorer_lock_release.py` 的 `Rstrtmgr.dll`）。主體字串的轉換直接重用
`cert_subject.subject_string_from_der()`——那件事在那裡已經寫過、測過，也
記載了「為什麼交給 Windows 而不是自己組字串」的三個理由。

## 這個模組不做的事

不檢查憑證是否被撤銷（`WTD_REVOKE_NONE`）。撤銷檢查需要連到憑證機構的
OCSP／CRL 端點，而這段程式碼執行的時機是使用者的網路狀況本來就有問題的
時候（他正在下載一個缺失的元件）。撤銷檢查逾時會讓安裝流程卡住數十秒，
而使用者看到的是一個沒有任何說明的停頓。
"""
import ctypes
import ctypes.wintypes as wintypes
import os

import cert_subject

# --- WinVerifyTrust ---------------------------------------------------------
WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2

# `dwProvFlags` 一律為 0。
#
# **不使用 `WTD_SAFER_FLAG`（0x100）**，雖然常見的建議是搭配 `WTD_UI_NONE`
# 一起帶上它。實測（2026-09-05）：對真正的
# `MicrosoftEdgeWebview2Setup.exe`（自
# https://go.microsoft.com/fwlink/p/?LinkId=2124703 下載，1,783,000 位元組），
# 帶 `WTD_SAFER_FLAG` 時 `WinVerifyTrust` 回報 `0x800B0109`
# （CERT_E_UNTRUSTEDROOT），不帶時回報 0；同一支檔案的簽章者主體為
# `CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US`。
# 也就是說該旗標會把真正的載入器判成不受信任。
#
# 這不是可以擇一的取捨：一道會拒絕正版檔案的驗證比沒有驗證更糟——使用者
# 永遠取得不到 WebView2，而錯誤訊息指向的是簽章，不是這個旗標。
# `WTD_UI_NONE` 本身已經足夠：需要使用者自行判斷的檔案會回報非 0，而本模組
# 只把 0 當成通過。
_PROV_FLAGS = 0

# WINTRUST_ACTION_GENERIC_VERIFY_V2。這個 GUID 指定的是「一般的檔案簽章
# 驗證」政策，也就是 Authenticode。
_ACTION_GENERIC_VERIFY_V2 = None

# --- CryptQueryObject 那一串 -------------------------------------------------
CERT_QUERY_OBJECT_FILE = 1
CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
CERT_QUERY_FORMAT_FLAG_BINARY = 1 << 1
CMSG_SIGNER_CERT_INFO_PARAM = 7
CERT_FIND_SUBJECT_CERT = 0x000B0000
X509_ASN_ENCODING = 0x00000001
PKCS_7_ASN_ENCODING = 0x00010000

# 主體字串裡各個項目之間的分隔符。`CertNameToStrW` 產生的形式固定是逗號
# 加空格（見 cert_subject.py 的說明第二點）。
_RDN_SEPARATOR = ", "


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD),
                ("pcwszFilePath", wintypes.LPCWSTR),
                ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.POINTER(_GUID))]


class _WINTRUST_DATA(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD),
                ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD),
                ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.POINTER(_WINTRUST_FILE_INFO)),
                ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", wintypes.HANDLE),
                ("pwszURLReference", wintypes.LPCWSTR),
                ("dwProvFlags", wintypes.DWORD),
                ("dwUIContext", wintypes.DWORD),
                ("pSignatureSettings", ctypes.c_void_p)]


class _CRYPT_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class _CERT_INFO(ctypes.Structure):
    """只需要讀到 `Subject` 這個欄位，但它的位置由前面所有欄位的大小決定，
    因此整個結構都要宣告正確。`SignatureAlgorithm` 與 `SubjectPublicKeyInfo`
    以位元組陣列占位——它們的內容用不到，只有大小重要。"""
    _fields_ = [("dwVersion", wintypes.DWORD),
                ("SerialNumber", _CRYPT_BLOB),
                ("SignatureAlgorithm", ctypes.c_byte * 24),
                ("Issuer", _CRYPT_BLOB),
                ("NotBefore", wintypes.FILETIME),
                ("NotAfter", wintypes.FILETIME),
                ("Subject", _CRYPT_BLOB),
                ("SubjectPublicKeyInfo", ctypes.c_byte * 32),
                ("IssuerUniqueId", _CRYPT_BLOB),
                ("SubjectUniqueId", _CRYPT_BLOB),
                ("cExtension", wintypes.DWORD),
                ("rgExtension", ctypes.c_void_p)]


class _CERT_CONTEXT(ctypes.Structure):
    _fields_ = [("dwCertEncodingType", wintypes.DWORD),
                ("pbCertEncoded", ctypes.POINTER(ctypes.c_byte)),
                ("cbCertEncoded", wintypes.DWORD),
                ("pCertInfo", ctypes.POINTER(_CERT_INFO)),
                ("hCertStore", ctypes.c_void_p)]


def _action_guid():
    global _ACTION_GENERIC_VERIFY_V2
    if _ACTION_GENERIC_VERIFY_V2 is None:
        _ACTION_GENERIC_VERIFY_V2 = _GUID(
            0x00AAC56B, 0xCD44, 0x11D0,
            (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    return _ACTION_GENERIC_VERIFY_V2


def _verify_trust(path):
    """回傳 `WinVerifyTrust` 的結果碼，0 代表信任。

    驗證完一定要再呼叫一次做 `WTD_STATEACTION_CLOSE`：第一次呼叫會配置一份
    狀態資料並掛在 `hWVTStateData` 上，不關掉就是洩漏。
    """
    file_info = _WINTRUST_FILE_INFO(ctypes.sizeof(_WINTRUST_FILE_INFO),
                                    os.path.abspath(path), None, None)
    data = _WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(_WINTRUST_DATA)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = WTD_REVOKE_NONE
    data.dwUnionChoice = WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = WTD_STATEACTION_VERIFY
    data.dwProvFlags = _PROV_FLAGS

    wintrust = ctypes.WinDLL("wintrust.dll")
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    try:
        result = wintrust.WinVerifyTrust(None, ctypes.byref(_action_guid()),
                                         ctypes.byref(data))
    finally:
        data.dwStateAction = WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(None, ctypes.byref(_action_guid()),
                                ctypes.byref(data))
    return result & 0xFFFFFFFF


def _signer_subject(path):
    """取出簽章者憑證的主體字串；取不到時回傳 None。

    路徑是「檔案 -> PKCS#7 訊息與憑證存放區 -> 簽章者的憑證識別資訊 ->
    在存放區裡找出那張憑證 -> 它的主體」。中間每一步都可能失敗，而失敗的
    意義相同（拿不到簽章者是誰），因此不逐一區分。
    """
    crypt32 = ctypes.WinDLL("crypt32.dll")
    store = ctypes.c_void_p()
    message = ctypes.c_void_p()
    ok = crypt32.CryptQueryObject(
        CERT_QUERY_OBJECT_FILE, ctypes.c_wchar_p(os.path.abspath(path)),
        CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
        CERT_QUERY_FORMAT_FLAG_BINARY, 0, None, None, None,
        ctypes.byref(store), ctypes.byref(message), None)
    if not ok:
        return None
    try:
        size = wintypes.DWORD()
        if not crypt32.CryptMsgGetParam(message, CMSG_SIGNER_CERT_INFO_PARAM,
                                        0, None, ctypes.byref(size)):
            return None
        buffer = (ctypes.c_byte * size.value)()
        if not crypt32.CryptMsgGetParam(message, CMSG_SIGNER_CERT_INFO_PARAM,
                                        0, buffer, ctypes.byref(size)):
            return None
        crypt32.CertFindCertificateInStore.restype = ctypes.POINTER(_CERT_CONTEXT)
        context = crypt32.CertFindCertificateInStore(
            store, X509_ASN_ENCODING | PKCS_7_ASN_ENCODING, 0,
            CERT_FIND_SUBJECT_CERT, buffer, None)
        if not context:
            return None
        try:
            blob = context.contents.pCertInfo.contents.Subject
            der = ctypes.string_at(blob.pbData, blob.cbData)
            # 字串的形式交給 cert_subject——那裡記載了為什麼不自己組。
            return cert_subject.subject_string_from_der(der)
        except cert_subject.CertificateReadError:
            return None
        finally:
            crypt32.CertFreeCertificateContext(context)
    finally:
        if message:
            crypt32.CryptMsgClose(message)
        if store:
            crypt32.CertCloseStore(store, 0)


def _matches_organization(subject, organization):
    """主體字串裡的 `O=` 這一項是不是恰好等於 `organization`。

    **不用子字串比對。** `O=Microsoft Corporation Fake` 含有
    `O=Microsoft Corporation` 這段文字，用 `in` 判斷會放行——而攻擊者要取得
    一張那樣的憑證，比取得一張真的微軟憑證容易得多。

    作法是在前後各補一個分隔符，再找完整的 `<分隔符>O=<名稱><分隔符>`。
    這樣第一項與最後一項也能以同一條規則處理，不需要另外判斷位置。
    """
    if not subject or not organization:
        return False
    padded = _RDN_SEPARATOR + str(subject).strip() + _RDN_SEPARATOR
    return f"{_RDN_SEPARATOR}O={organization}{_RDN_SEPARATOR}" in padded


def verify_file(path, expected_organization=None):
    """驗證一個檔案的簽章，回傳 `(是否通過, 說明)`。

    `expected_organization` 有值時，簽章者憑證的 `O=` 必須恰好等於它。
    沒有值時只驗簽章有效——保留這個形式，是因為「簽章有效」與「簽章者是誰」
    是兩個不同的問題，硬把後者變成必填會讓前者沒有單獨可用的形式。

    不拋例外：呼叫端的處置在所有失敗形態下相同（不要執行這個檔案），而說明
    文字是給人看的，不是給程式分支用的。
    """
    if not path or not os.path.isfile(path):
        return False, f"找不到要驗證的檔案：{path}"
    try:
        result = _verify_trust(path)
    except Exception as e:
        return False, f"無法驗證數位簽章：{e}"
    if result != 0:
        return False, (
            f"數位簽章驗證未通過（WinVerifyTrust 回報 0x{result:08X}）。"
            "這個檔案沒有簽章、簽章損毀，或簽章的憑證不受這台電腦信任。"
        )
    if not expected_organization:
        return True, "數位簽章有效。"

    try:
        subject = _signer_subject(path)
    except Exception as e:
        return False, f"簽章有效，但無法讀出簽章者是誰：{e}"
    if not subject:
        return False, "簽章有效，但讀不出簽章者的名稱。"
    if not _matches_organization(subject, expected_organization):
        return False, (
            f"簽章有效，但簽章者不是預期的組織。\n"
            f"    預期：O={expected_organization}\n"
            f"    實際：{subject}"
        )
    return True, f"數位簽章有效，簽章者為 {subject}。"
