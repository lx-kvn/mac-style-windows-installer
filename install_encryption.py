"""install_encryption.py
----------------------
安裝密碼保護（見 CONTEXT.md「安裝密碼保護」一節）：把整個 app_contents
資料夾加密成一份檔案，安裝時用使用者輸入的密碼解密回一個暫存資料夾。

定位是**存取控制**，不是防範有心人暴力破解的資安機制——這個定位決定了
這裡沒有做「限制嘗試次數」這類 UI 層防護，安全性完全建立在
PBKDF2 的高迭代次數（讓每次嘗試密碼都要花時間）跟使用者自己選的密碼
強度上。

格式：`salt(16 bytes) + nonce(12 bytes) + AES-256-GCM(zip(source_dir))`。
salt/nonce 都不是秘密，明文存在檔案開頭即可——AES-GCM 的機密性/完整性
保證不依賴它們保密，只依賴每次加密都用新的隨機值（重複使用 nonce 會
直接破壞 GCM 的機密性保證，這裡每次呼叫 `encrypt_directory()` 都重新
產生一組隨機 salt/nonce，不共用）。

真實抓到的問題：`cryptography` 這個第三方套件的 import 刻意不放在檔案
最上層，改成延遲到 `encrypt_directory()`/`decrypt_to_directory()` 內部
才 import——這個模組會被 `builder.py`/`installer_core.py` 兩個 entry
point 匯入，兩者又會被 `gui_config.py`/`builder_cli.py` 匯入，如果放在
最上層，等於讓 `cryptography` 從「安裝密碼保護這個選填功能的相依套件」
變成「整個打包工具開不開得起來的硬性依賴」，違反
`packaging_core.check_build_environment()` docstring 明講的設計原則：
不管外部建置環境（pyinstaller/pywebview）齊不齊全，工具本身一定要跑
得起來。沒有真的用到 `install_password_env` 這個功能的使用者，不應該
因為沒裝 `cryptography` 就連 GUI/CLI 都開不了。
"""
import io
import ntpath
import os
import shutil
import zipfile

_SALT_SIZE = 16
_NONCE_SIZE = 12
_PBKDF2_ITERATIONS = 600_000  # OWASP 2023 建議的 PBKDF2-HMAC-SHA256 最低迭代次數


class UnsafeArchiveEntry(Exception):
    """解密出來的內容裡有一項會落在解壓目錄之外（見 _extract_within()）。

    與 WrongPasswordError 分開：密碼錯誤是使用者可以自行處理的事，這一項
    代表這份安裝檔的內容不對，重試沒有意義。
    """


class WrongPasswordError(Exception):
    """密碼錯誤，或密文已損毀（AES-GCM 認證標籤驗證失敗，兩者從外部
    無法區分，統一視為『這個密碼打不開這份內容』）。"""


def _derive_key(password, salt):
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_PBKDF2_ITERATIONS)
    return kdf.derive(password.encode("utf-8"))


def _zip_directory(source_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(source_dir):
            for name in files:
                full_path = os.path.join(root, name)
                arcname = os.path.relpath(full_path, source_dir)
                zf.write(full_path, arcname)
    return buf.getvalue()


def _write_encrypted(plaintext, dest_file, password):
    """把一段位元組加密後寫到 dest_file。

    與 `encrypt_directory()` 分開，是為了讓「壓成 zip」與「加密並落地」兩件
    事各自可測——後者的正確性（每次都是新的隨機 salt/nonce）不該只能透過
    一個真的資料夾來驗證。
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = os.urandom(_SALT_SIZE)
    nonce = os.urandom(_NONCE_SIZE)
    key = _derive_key(password, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=None)
    with open(dest_file, "wb") as f:
        f.write(salt)
        f.write(nonce)
        f.write(ciphertext)


def encrypt_directory(source_dir, dest_file, password):
    """把 source_dir 整包壓成 zip、加密，寫到 dest_file。"""
    _write_encrypted(_zip_directory(source_dir), dest_file, password)


def decrypt_to_directory(encrypted_file, dest_dir, password):
    """驗證密碼並解密回 dest_dir。密碼錯誤（或密文損毀）拋
    WrongPasswordError；dest_dir 必須已存在。"""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with open(encrypted_file, "rb") as f:
        data = f.read()
    salt, nonce, ciphertext = data[:_SALT_SIZE], data[_SALT_SIZE:_SALT_SIZE + _NONCE_SIZE], data[_SALT_SIZE + _NONCE_SIZE:]
    key = _derive_key(password, salt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)
    except InvalidTag:
        raise WrongPasswordError("密碼錯誤，或安裝檔內容已損毀") from None

    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(plaintext)) as zf:
        _extract_within(zf, dest_dir)


def _extract_within(zip_file, dest_dir):
    """逐項解壓，並確認每一項的落點都仍在 dest_dir 之內。

    這份 zip 由 encrypt_directory() 以 os.walk 產生，實務上不會出現穿越
    項目，因此這道檢查在正常路徑上永遠通過。加上它的理由是一致性：
    sdk_tools._safe_extract_bin() 對一份已通過 SHA-256 驗證的檔案尚且檢查
    落點，其理由是「不該由『檔案內容可信』推導出『可以把它寫到它自己指定
    的任何路徑』」——而這裡的密文來源是安裝檔本身，安裝檔會被傳來傳去。

    不使用 zipfile 自己的 extractall()：它會把不合法的項目名稱靜默地改成
    合法的（去掉開頭的斜線、丟掉 `..`），結果是檔案落在一個與封裝時不同
    的位置而沒有任何人知道。這裡選擇出聲。
    """
    dest_root = os.path.realpath(dest_dir)
    for info in zip_file.infolist():
        name = info.filename.replace("\\", "/")
        # 絕對路徑與帶磁碟機代號的項目要獨立擋下，不能只靠下面的落點比對：
        # os.path.join() 對 "C:" 這種片段的處理是把它當成磁碟機規格，結果
        # 是那個項目安靜地落回 dest_dir 底下——沒有穿越，但也沒有出聲，而
        # 「安靜地改寫成另一個位置」正是這個函式不採用 extractall() 的原因。
        if name.startswith("/") or ntpath.splitdrive(name)[0]:
            raise UnsafeArchiveEntry(
                f"安裝檔內容含有絕對路徑的項目，已中止：{info.filename}"
            )
        target = os.path.realpath(os.path.join(dest_root, *name.split("/")))
        if target != dest_root and not target.startswith(dest_root + os.sep):
            raise UnsafeArchiveEntry(
                f"安裝檔內容含有指向解壓目錄之外的項目，已中止：{info.filename}"
            )
        if info.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zip_file.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
