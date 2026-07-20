"""
splash_helper.py
-----------------
極簡的獨立載入畫面小程式，由 splash.py 的 show() 以子行程方式呼叫。

刻意獨立成一支只依賴標準函式庫（tkinter、ctypes）的檔案，用系統上真正的
python 直譯器執行（而不是重新呼叫打包後的主程式本身）。這樣不管主程式被打包成
多重、內含 PyQt5/pythonnet 等重量級依賴的 onefile exe，這個載入畫面都不會被
拖累，可以很快跳出來。

用法：
    python splash_helper.py "顯示文字"

視窗會一直開著、跑一個不停轉動、跟實際安裝/編譯進度無關的進度條，
直到被外部（splash.py 的 close()，用 subprocess 的 terminate()）關掉為止，
自己不會主動判斷何時該關閉。
"""

import sys
import ctypes
import tkinter as tk
from tkinter import ttk


def _set_dpi_aware():
    """避免 Windows 非 100% 縮放比例下把視窗當點陣圖拉伸，文字模糊。"""
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "正在載入中..."

    _set_dpi_aware()

    root = tk.Tk()
    root.overrideredirect(True)
    width, height = 300, 120
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 2}")
    root.configure(bg="#09090b")
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    tk.Label(root, text=text, fg="#fafafa", bg="#09090b",
             font=("Microsoft JhengHei", 11)).pack(expand=True, pady=(24, 8))

    style = ttk.Style()
    try:
        style.theme_use("default")
    except Exception:
        pass
    style.configure("Splash.Horizontal.TProgressbar", troughcolor="#27272a", background="#fafafa", thickness=4)
    bar = ttk.Progressbar(root, mode="indeterminate", style="Splash.Horizontal.TProgressbar", length=220)
    bar.pack(pady=(0, 20))
    bar.start(12)  # 跟實際進度無關，純粹持續轉動表示「還在忙」

    root.mainloop()


if __name__ == "__main__":
    main()