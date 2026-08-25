import ast
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import install_encryption


class TestCryptographyImportIsLazy(unittest.TestCase):
    """真實踩到的問題：v0.14.0 發布後 CI 直接爆掉——`cryptography` 只被
    加進 CI 的 pip install 清單漏掉，但更根本的問題是這個模組原本在
    檔案最上層 `from cryptography...import ...`，導致任何 import
    `install_encryption` 的模組（`builder.py`/`installer_core.py`/
    `gui_config.py` 都會遞移 import 到它）都變成硬性依賴 `cryptography`
    才能載入。這違反 `packaging_core.check_build_environment()` docstring
    明講的設計原則：「不管這裡檢查的外部環境齊不齊全，工具本身（GUI 或
    CLI）一定跑得起來」——`cryptography` 只有真的呼叫
    `encrypt_directory()`/`decrypt_to_directory()`（也就是使用者真的
    設定了 install_password_env）才應該是必要的，不該在完全沒用到密碼
    保護功能的情境下，變成連工具本身都開不起來的硬性依賴。這裡用 ast
    解析原始碼，確認檔案最上層（module-level）沒有任何一行
    import/from-import 是在 import `cryptography` 底下的東西——全部都
    應該收在函式內部，只有真的呼叫到才會觸發 import。"""

    def test_no_module_level_cryptography_import(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install_encryption.py")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in tree.body:  # 只看最上層陳述式，不遞迴進函式/class 內部
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            self.assertFalse(
                any(name == "cryptography" or name.startswith("cryptography.") for name in names),
                f"install_encryption.py 頂層不應該 import cryptography 相關模組，找到：{names}",
            )


class TestEncryptDecryptRoundTrip(unittest.TestCase):
    """安裝密碼保護：app_contents 整包加密成一份檔案，安裝時用正確密碼
    解密回一個暫存資料夾。這裡先驗證最基本的往返正確性——加密再解密要
    完全還原原始資料夾結構跟內容，包含巢狀子資料夾。"""

    def setUp(self):
        self.source_dir = tempfile.mkdtemp()
        self.dest_dir = tempfile.mkdtemp()
        self.encrypted_file = os.path.join(tempfile.mkdtemp(), "payload.enc")

    def tearDown(self):
        shutil.rmtree(self.source_dir, ignore_errors=True)
        shutil.rmtree(self.dest_dir, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.encrypted_file), ignore_errors=True)

    def _write(self, rel_path, content=b"fake content"):
        full = os.path.join(self.source_dir, rel_path)
        os.makedirs(os.path.dirname(full) or self.source_dir, exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)

    def test_round_trip_restores_flat_file(self):
        self._write("app.exe", b"\x4d\x5a fake exe bytes")
        install_encryption.encrypt_directory(self.source_dir, self.encrypted_file, "correct horse")
        install_encryption.decrypt_to_directory(self.encrypted_file, self.dest_dir, "correct horse")
        with open(os.path.join(self.dest_dir, "app.exe"), "rb") as f:
            self.assertEqual(f.read(), b"\x4d\x5a fake exe bytes")

    def test_round_trip_restores_nested_subdirectories(self):
        self._write("app.exe", b"main")
        self._write(os.path.join("assets", "icon.ico"), b"icon-bytes")
        self._write(os.path.join("assets", "lang", "en.json"), b'{"hello": "world"}')

        install_encryption.encrypt_directory(self.source_dir, self.encrypted_file, "pw123")
        install_encryption.decrypt_to_directory(self.encrypted_file, self.dest_dir, "pw123")

        with open(os.path.join(self.dest_dir, "assets", "icon.ico"), "rb") as f:
            self.assertEqual(f.read(), b"icon-bytes")
        with open(os.path.join(self.dest_dir, "assets", "lang", "en.json"), "rb") as f:
            self.assertEqual(f.read(), b'{"hello": "world"}')

    def test_wrong_password_raises_wrong_password_error(self):
        self._write("app.exe", b"main")
        install_encryption.encrypt_directory(self.source_dir, self.encrypted_file, "correct password")

        with self.assertRaises(install_encryption.WrongPasswordError):
            install_encryption.decrypt_to_directory(self.encrypted_file, self.dest_dir, "wrong password")

    def test_corrupted_ciphertext_raises_wrong_password_error_not_garbage(self):
        """真實會發生的情境：檔案在傳輸過程中損毀。AES-GCM 的認證標籤要能
        擋下這種情況，不能靜默解出一堆亂碼寫進磁碟。"""
        self._write("app.exe", b"main")
        install_encryption.encrypt_directory(self.source_dir, self.encrypted_file, "correct password")

        with open(self.encrypted_file, "r+b") as f:
            f.seek(-1, os.SEEK_END)
            last_byte = f.read(1)
            f.seek(-1, os.SEEK_END)
            f.write(bytes([last_byte[0] ^ 0xFF]))

        with self.assertRaises(install_encryption.WrongPasswordError):
            install_encryption.decrypt_to_directory(self.encrypted_file, self.dest_dir, "correct password")

    def test_two_encryptions_of_same_content_use_different_salt_and_nonce(self):
        """真實抓到的問題類型：如果每次加密都重複使用相同的 salt/nonce，
        用同一組密碼加密兩次會洩漏『這兩份內容是否相同』這種側信道資訊，
        AES-GCM 更嚴重的是 nonce 重複使用會直接破壞機密性保證。這裡驗證
        兩次獨立呼叫 encrypt_directory() 產生的檔案內容不同（salt/nonce
        隨機產生），即使明文和密碼都一樣。"""
        self._write("app.exe", b"identical content")
        encrypted_file_2 = os.path.join(tempfile.mkdtemp(), "payload2.enc")
        try:
            install_encryption.encrypt_directory(self.source_dir, self.encrypted_file, "same password")
            install_encryption.encrypt_directory(self.source_dir, encrypted_file_2, "same password")
            with open(self.encrypted_file, "rb") as f1, open(encrypted_file_2, "rb") as f2:
                self.assertNotEqual(f1.read(), f2.read())
        finally:
            shutil.rmtree(os.path.dirname(encrypted_file_2), ignore_errors=True)

    def test_empty_source_directory_round_trips(self):
        install_encryption.encrypt_directory(self.source_dir, self.encrypted_file, "pw")
        install_encryption.decrypt_to_directory(self.encrypted_file, self.dest_dir, "pw")
        self.assertEqual(os.listdir(self.dest_dir), [])


if __name__ == "__main__":
    unittest.main()
