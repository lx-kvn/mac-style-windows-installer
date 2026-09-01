"""產生 CI 驗證用的 PNG 與 ICO 圖示。

不使用影像處理套件：本專案沒有那個相依（第五輪決議第一項），而 CI 上為了
產生兩張純色方塊而裝一個影像處理套件，等於在驗證環境裡引進產品沒有的東西。
PNG 與 ICO 的檔頭結構固定，標準函式庫寫得出來。

MSIX 的套件圖示必須是正方形的 PNG，且三個宣告位置的邊長分別至少 150／44／50
（見 `png_size.py`）；這裡預設產生 256×256，三個位置都滿足。
"""
import argparse
import os
import struct
import zlib


def write_png(path, size):
    """一張純色的正方形 PNG。逐列前面那個 0 是 PNG 的濾波器位元組。"""
    raw = b"".join(b"\x00" + b"\x33\x66\x99\xff" * size for _ in range(size))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1))
            + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(blob)
    return path


def write_ico(path, png_path):
    """把一張 PNG 包成 ICO。

    Vista 之後的 ICO 允許直接內嵌 PNG（不必轉成 BMP）；目錄項裡的寬高欄位
    只有一個位元組，256 以 0 表示。
    """
    with open(png_path, "rb") as f:
        data = f.read()
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(data), 6 + 16)
    with open(path, "wb") as f:
        f.write(header + entry + data)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--png", default="icon.png")
    parser.add_argument("--ico", default="icon.ico")
    parser.add_argument("--size", type=int, default=256)
    args = parser.parse_args()

    write_png(args.png, args.size)
    write_ico(args.ico, args.png)
    print(f"{args.png}: {os.path.getsize(args.png)} bytes（{args.size}x{args.size}）")
    print(f"{args.ico}: {os.path.getsize(args.ico)} bytes")


if __name__ == "__main__":
    main()
