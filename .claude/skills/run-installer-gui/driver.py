"""driver.py — launch and drive this project's generated installer/uninstaller
pywebview GUI (installer_core.py / uninstall.py), for visual verification of
ui/*.html changes. See SKILL.md in this same directory for usage.

Windows-only (uses ctypes user32/gdi32 directly — no pywin32/Pillow dependency
required, though Pillow is used by the caller for pixel-level measurement in
some workflows, not by this script itself).
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _declare_dpi_awareness():
    """宣告 per-monitor DPI 感知。**必須在任何 user32 視窗呼叫之前執行。**

    真實抓到的缺陷：未宣告時，`GetWindowRect()` 回傳的是被系統縮放過的邏輯
    尺寸——在 150% 縮放的螢幕上，一個 600x420 的視窗被回報成 586x382，
    截出來的圖因此比實際視窗窄，右側內容被切掉。

    這個症狀特別會誤導：看起來就像「版面溢出、文字被截斷」，而那正是
    CLAUDE.md 要求截圖檢查的項目之一。實際驗證 MSIX 模式的目的地文字時
    就先被它誤導過一次，改用 DPI 感知的方式重抓才確認版面其實沒問題。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_declare_dpi_awareness()

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 2

# Minimal virtual-key map for the keys this driver actually needs.
VK = {"TAB": 0x09, "ENTER": 0x0D, "ESC": 0x1B, "SPACE": 0x20}

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _anonymous_ = ("_i",)
    _fields_ = [("type", wintypes.DWORD), ("_i", _I)]


def _find_window_by_title(title, timeout=10, poll_interval=0.5):
    deadline = time.monotonic() + timeout
    hwnd = None
    while time.monotonic() < deadline:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return hwnd
        time.sleep(poll_interval)
    return None


def cmd_setup(args):
    """建立測試用的 installer_config.json + app_contents(.enc)，放在 repo 根目錄
    （get_resource_path() 在 .py 直接執行模式下 fallback 到 cwd，所以要跟
    installer_core.py 放在一起才找得到）。"""
    sys.path.insert(0, REPO_ROOT)
    app_dir = os.path.join(REPO_ROOT, "_driver_scratch_app")
    os.makedirs(app_dir, exist_ok=True)
    main_exe = os.path.join(app_dir, "app.exe")
    with open(main_exe, "wb") as f:
        f.write(b"fake exe bytes for GUI smoke testing")

    config = {
        "app_name": args.app_name, "display_name": args.app_name,
        "folder_name": "DriverTestApp", "version": "1.0.0", "publisher": "Tester",
        "main_exe": "app.exe", "eula_texts": {}, "eula_default_lang": "",
        "dependencies": [], "custom_dependencies": [], "bundle_dependencies": [],
        "file_associations": [], "doc_icon": "", "doc_icons": {},
        "add_to_path": False, "path_target_exe": "", "local_appdata_files": [],
        "restart_explorer_on_update": False, "no_admin_install": True, "custom_install_dir": "",
        "windows_service": {}, "scheduled_task": {}, "dependencies_min_version": {},
        "create_restore_point_before_install": False, "pre_install_script": "", "post_install_script": "",
        "password_protected": bool(args.password_protected),
    }

    if args.password_protected:
        import install_encryption
        install_encryption.encrypt_directory(app_dir, os.path.join(REPO_ROOT, "app_contents.enc"), args.password)
    else:
        # 非密碼保護模式：get_resource_path("app_contents") 直接指向這個資料夾，
        # 不需要另外複製，app_dir 本身已經在 repo root 底下（cwd）。
        target = os.path.join(REPO_ROOT, "app_contents")
        if os.path.abspath(target) != os.path.abspath(app_dir):
            if os.path.exists(target):
                import shutil
                shutil.rmtree(target)
            os.rename(app_dir, target)

    with open(os.path.join(REPO_ROOT, "installer_config.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"[setup] wrote installer_config.json (password_protected={args.password_protected})")


def cmd_launch(args):
    """背景啟動 `python <entry>`（webview.start() 是 blocking call，一定要背景執行）。"""
    proc = subprocess.Popen(
        [sys.executable, args.entry], cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"[launch] pid={proc.pid}")


def cmd_screenshot(args):
    """用 PrintWindow 直接抓視窗內容（不需要搶前景，視窗被其他視窗擋住也抓得到，
    比 CopyFromScreen 全螢幕截圖安全——不會意外拍到桌面上其他不相干的東西）。"""
    hwnd = _find_window_by_title(args.title, timeout=args.timeout)
    if not hwnd:
        print(f"[screenshot] window titled {args.title!r} not found within {args.timeout}s", file=sys.stderr)
        sys.exit(1)

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)

    # 把 HBITMAP 存成 .bmp（不依賴 Pillow/GDI+，純 GetDIBits 手動組 BMP 檔頭，
    # 這樣這支 driver 除了標準函式庫以外沒有其他相依套件）。
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    # 真實踩到的坑：biHeight 的正負號一定要跟 GetDIBits() 呼叫時用的那份
    # 一致，不能其中一份用負值（top-down）、另一份用正值（bottom-up）——
    # 兩邊對不上，寫進檔案的像素資料排列方式（實際上是 top-down）跟檔頭
    # 宣稱的方向（bottom-up）不一致，看起來就會整張圖上下顛倒、左右鏡射
    # （相當於整張旋轉 180 度）。這裡統一固定用正值（bottom-up，BMP 檔案
    # 格式原生、最多看圖軟體都吃得下的排列方式），GetDIBits 用的 bmi 跟
    # 寫進檔案的 dib_header 兩邊都用同一個正值 h，不要再各自為政。
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = h  # bottom-up（BMP 原生排列，兩處呼叫都要用這個正值）
    bmi.biPlanes = 1
    bmi.biBitCount = 24
    bmi.biCompression = 0
    row_size = (w * 3 + 3) & ~3
    buf_size = row_size * h
    buf = ctypes.create_string_buffer(buf_size)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    bmp_header = b"BM" + (54 + buf_size).to_bytes(4, "little") + b"\x00\x00\x00\x00" + (54).to_bytes(4, "little")
    dib_header = (
        ctypes.sizeof(BITMAPINFOHEADER).to_bytes(4, "little") + w.to_bytes(4, "little", signed=True)
        + h.to_bytes(4, "little", signed=True) + (1).to_bytes(2, "little") + (24).to_bytes(2, "little")
        + (0).to_bytes(4, "little") + buf_size.to_bytes(4, "little") + (0).to_bytes(4, "little", signed=True)
        + (0).to_bytes(4, "little", signed=True) + (0).to_bytes(4, "little") + (0).to_bytes(4, "little")
    )
    with open(args.out, "wb") as f:
        f.write(bmp_header + dib_header + buf.raw)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)
    print(f"[screenshot] saved {args.out} ({w}x{h})")


def cmd_send_keys(args):
    """送鍵盤按鍵給指定視窗（用來驗證 Tab 鍵焦點框這類鍵盤操作行為）。
    一定要先把視窗帶到前景才收得到鍵盤輸入——這是 Windows 訊息路由的限制，
    PrintWindow 那種「不用搶前景」的技巧不適用在送鍵盤事件上。"""
    hwnd = _find_window_by_title(args.title, timeout=args.timeout)
    if not hwnd:
        print(f"[send-keys] window titled {args.title!r} not found", file=sys.stderr)
        sys.exit(1)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    vk = VK[args.key.upper()]
    for _ in range(args.count):
        inp_down = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None))
        inp_up = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))
        user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
        time.sleep(0.05)
        user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
        time.sleep(args.delay)
    print(f"[send-keys] sent {args.key} x{args.count}")


def cmd_stop(args):
    hwnd = _find_window_by_title(args.title, timeout=1)
    if not hwnd:
        print(f"[stop] window titled {args.title!r} not found (already closed?)")
        return
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    subprocess.run(["taskkill", "/F", "/PID", str(pid.value)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[stop] killed pid={pid.value}")


def cmd_teardown(args):
    for name in ("installer_config.json", "app_contents.enc"):
        path = os.path.join(REPO_ROOT, name)
        if os.path.exists(path):
            os.remove(path)
            print(f"[teardown] removed {path}")
    for name in ("app_contents", "_driver_scratch_app"):
        path = os.path.join(REPO_ROOT, name)
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
            print(f"[teardown] removed {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="建立測試用 installer_config.json/app_contents")
    p_setup.add_argument("--password-protected", action="store_true")
    p_setup.add_argument("--password", default="test1234")
    p_setup.add_argument("--app-name", default="DriverTestApp")
    p_setup.set_defaults(func=cmd_setup)

    p_launch = sub.add_parser("launch", help="背景啟動安裝/解除安裝 GUI")
    p_launch.add_argument("--entry", default="installer_core.py", choices=["installer_core.py", "uninstall.py"])
    p_launch.set_defaults(func=cmd_launch)

    p_shot = sub.add_parser("screenshot", help="用 PrintWindow 截圖指定標題的視窗")
    p_shot.add_argument("--title", default="安裝應用程式")
    p_shot.add_argument("--out", required=True)
    p_shot.add_argument("--timeout", type=float, default=10)
    p_shot.set_defaults(func=cmd_screenshot)

    p_keys = sub.add_parser("send-keys", help="送鍵盤按鍵給指定視窗（會短暫搶前景）")
    p_keys.add_argument("--title", default="安裝應用程式")
    p_keys.add_argument("--key", default="TAB", choices=list(VK.keys()))
    p_keys.add_argument("--count", type=int, default=1)
    p_keys.add_argument("--delay", type=float, default=0.2)
    p_keys.add_argument("--timeout", type=float, default=10)
    p_keys.set_defaults(func=cmd_send_keys)

    p_stop = sub.add_parser("stop", help="關閉指定標題的視窗行程")
    p_stop.add_argument("--title", default="安裝應用程式")
    p_stop.set_defaults(func=cmd_stop)

    p_teardown = sub.add_parser("teardown", help="清掉 setup 產生的暫存檔案")
    p_teardown.set_defaults(func=cmd_teardown)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
