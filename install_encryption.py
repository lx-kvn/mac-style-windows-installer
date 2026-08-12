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
"""
import io
import os
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

_SALT_SIZE = 16
_NONCE_SIZE = 12
_PBKDF2_ITERATIONS = 600_000  # OWASP 2023 建議的 PBKDF2-HMAC-SHA256 最低迭代次數


class WrongPasswordError(Exception):
    """密碼錯誤，或密文已損毀（AES-GCM 認證標籤驗證失敗，兩者從外部
    無法區分，統一視為『這個密碼打不開這份內容』）。"""


def _derive_key(password, salt):
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


def encrypt_directory(source_dir, dest_file, password):
    """把 source_dir 整包壓成 zip、加密，寫到 dest_file。"""
    salt = os.urandom(_SALT_SIZE)
    nonce = os.urandom(_NONCE_SIZE)
    key = _derive_key(password, salt)
    plaintext = _zip_directory(source_dir)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=None)
    with open(dest_file, "wb") as f:
        f.write(salt)
        f.write(nonce)
        f.write(ciphertext)


def decrypt_to_directory(encrypted_file, dest_dir, password):
    """驗證密碼並解密回 dest_dir。密碼錯誤（或密文損毀）拋
    WrongPasswordError；dest_dir 必須已存在。"""
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
        zf.extractall(dest_dir)
