import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import install_encryption


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
