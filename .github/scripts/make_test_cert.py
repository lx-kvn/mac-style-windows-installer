"""為 CI 探針產生一張自簽的程式碼簽章憑證，並印出套件清單該填的發行者。

供 `.github/workflows/test-packaging-options.yml` 的 MSIX 引擎驗證使用。
憑證的定位是開發與測試手段，不是散布方案（`docs/proposals/MSIX輸出規劃.md`
第二輪決議第一項），且只存在於用完即丟的 runner 上。

發行者字串刻意透過本專案自己的 `cert_subject.py` 取得，而不是在這裡另外
組一份——那個字串的形式並不直覺（順序、分隔符、引號規則，見該模組說明），
若 CI 用另一套算法算出正確的值，就驗不到產品程式碼算得對不對。

不使用 PowerShell 的 `New-SelfSignedCertificate`，因為後者會把憑證寫進
使用者的憑證存放區，這裡不需要那個副作用（信任的建立是 workflow 另外一個
明確的步驟）。
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cert_subject

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pfx", required=True, help="憑證輸出路徑")
    parser.add_argument("--cer", required=True, help="公開憑證輸出路徑（供匯入信任存放區）")
    parser.add_argument("--password", required=True)
    parser.add_argument("--common-name", default="MSWI CI Probe")
    parser.add_argument("--publisher-out", required=True, help="把發行者字串寫進這個檔案")
    args = parser.parse_args()

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, args.common_name),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "TW"),
    ])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open(args.pfx, "wb") as f:
        f.write(pkcs12.serialize_key_and_certificates(
            b"mswi-ci", key, certificate, None,
            serialization.BestAvailableEncryption(args.password.encode())))
    with open(args.cer, "wb") as f:
        f.write(certificate.public_bytes(serialization.Encoding.DER))

    publisher = cert_subject.read_from_pfx(args.pfx, args.password)
    with open(args.publisher_out, "w", encoding="utf-8") as f:
        f.write(publisher)

    print(f"憑證：{args.pfx}")
    print(f"發行者（由本專案的 cert_subject.py 算出）：{publisher}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
