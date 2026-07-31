"""
build_config_tool.py
---------------------
「配置精靈」打包器的圖形化介面版本。

負責把 gui_config.py（也就是「安裝軟體生成器」這個工具本身）打包成 exe，提供：
  - 自訂輸出 exe 的圖示（.ico）
  - 打包進度顯示：PyInstaller 執行期間即時把輸出串流到畫面上的 log 區塊，
    並用一個緩慢往前推進的進度條給出「還在跑」的視覺回饋
    （PyInstaller 本身不會回報精確百分比，這裡不假裝算得出來，
    真正可信的進度資訊是 log 裡的實際輸出跟最後的結束碼）。

跟 builder.py 的差異：builder.py 是這個工具「執行時」用來生成別人安裝檔的模組；
這支腳本是拿來打包這個工具「自己」，職責不同所以獨立成一支檔案。

用 Tkinter 而不是 pywebview：這支只是內部偶爾會用到的建置小工具，
Tkinter 是標準函式庫、啟動快、不需要額外依賴，場合上比較合適。

使用前提：
  - 這支腳本要跟 gui_config.py、splash.py、splash_helper.py、builder.py、
    installer_core.py、uninstall.py、ui/ 放在同一層。
  - 執行環境要先安裝 pyinstaller、pywebview（pip install pyinstaller pywebview）。

修正紀錄：
  - 打包前清除 dist/build 資料夾原本用 ignore_errors=True，遇到檔案被鎖住
    （最常見是上一次打包出來的 exe 還在執行中，或被防毒軟體掃描）會悶不吭聲地
    失敗，然後在 PyInstaller 最後一步才丟出一長串看不懂的 traceback。
    現在改成：打包前先偵測目標 exe 是不是還在執行中，並且清除資料夾失敗時
    給出清楚的中文錯誤訊息，而不是讓錯誤一路悶到 PyInstaller 那邊才爆炸。
  - 加入 DPI 感知宣告，避免 Windows 125% 等非 100% 縮放比例下整個視窗被當
    點陣圖拉伸、文字模糊的問題。
  - 配色改為白色主色調，字體改用微軟正黑體。
  - 新增 --add-data=installer_core.py;. 與 --add-data=uninstall.py;.：
    builder.py 執行時需要另外呼叫一次 pyinstaller 子行程去編譯這兩支檔案，
    原本沒有內嵌進 InstallerBuilder.exe，導致打包成 exe 後執行時，
    這兩支檔案（以及 ui/index.html）不存在於磁碟上，直接報「找不到 ui 資料夾」。
    現在內嵌進去後，gui_config.py 的 ensure_workspace_files() 會在需要時
    自動把它們解壓到工作目錄，不需要使用者手動複製檔案。
  - 拿掉 --splash=splash.png（PyInstaller 原生的靜態圖片載入畫面），改成
    --add-data=splash_helper.py;.，內嵌一支獨立的載入畫面腳本，執行時用
    系統上真正的 python 直譯器去跑它，開出一個貨真價實、持續轉動的
    Tkinter 進度條視窗，取代原本只能放靜態圖片的做法。細節見 splash.py。
"""

import os
import shutil
import subprocess
import threading
import queue
import time
import ctypes
import tkinter as tk
from tkinter import filedialog, ttk

ENTRY_SCRIPT = "gui_config.py"
OUTPUT_NAME = "InstallerBuilder"
FONT_FAMILY = "Microsoft JhengHei"

BG = "#ffffff"
CARD = "#f4f4f5"
FG = "#09090b"
MUTED = "#71717a"
BORDER = "#e4e4e7"
SUCCESS = "#34C759"
ERROR = "#FF6B6B"


class BuilderGUI:
    def __init__(self, root):
        self.root = root
        self.icon_path = ""
        self.log_queue = queue.Queue()
        self._progress_ceiling = 0.0
        self._progress_displayed = 0.0
        self._progress_stopped = False
        self._ceiling_reached_at = None
        self.building = False
        self._log_lines = []

        root.title("打包工具建置器")
        root.configure(bg=BG)
        root.geometry("560x540")
        root.minsize(520, 480)

        self._build_ui()
        self._check_prerequisites()
        self.root.after(100, self._poll_log_queue)

    # ------------------------------------------------------------------
    # 介面建構
    # ------------------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 20}

        tk.Label(self.root, text="配置精靈 打包器", fg=FG, bg=BG,
                 font=(FONT_FAMILY, 16, "bold")).pack(anchor="w", pady=(20, 4), **pad)
        tk.Label(self.root, text=f"將 {ENTRY_SCRIPT} 打包成 {OUTPUT_NAME}.exe", fg=MUTED, bg=BG,
                 font=(FONT_FAMILY, 10)).pack(anchor="w", pady=(0, 14), **pad)

        self.warning_label = tk.Label(self.root, text="", fg=ERROR, bg=BG, font=(FONT_FAMILY, 9),
                                       wraplength=500, justify="left")
        self.warning_label.pack(anchor="w", pady=(0, 8), **pad)

        # 圖示選擇
        icon_section = tk.Frame(self.root, bg=BG)
        icon_section.pack(fill="x", pady=(0, 16), **pad)
        tk.Label(icon_section, text="輸出 EXE 圖示 (.ico，選填)", fg=MUTED, bg=BG,
                 font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(0, 6))

        icon_row = tk.Frame(icon_section, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        icon_row.pack(fill="x")
        self.icon_btn = tk.Button(icon_row, text="選擇 ICO 檔案", command=self._browse_icon,
                                   bg=FG, fg=BG, relief="flat", font=(FONT_FAMILY, 10),
                                   activebackground="#27272a", cursor="hand2", padx=14, pady=6)
        self.icon_btn.pack(side="left", padx=8, pady=8)
        self.icon_label = tk.Label(icon_row, text="未選擇檔案（將使用 PyInstaller 預設圖示）",
                                    fg=MUTED, bg=CARD, font=(FONT_FAMILY, 9), anchor="w")
        self.icon_label.pack(side="left", fill="x", expand=True, padx=(0, 12))

        # 開始按鈕
        self.start_btn = tk.Button(self.root, text="開始打包", command=self._start_build,
                                    bg=FG, fg=BG, relief="flat", font=(FONT_FAMILY, 11, "bold"),
                                    activebackground="#27272a", cursor="hand2", pady=10)
        self.start_btn.pack(fill="x", pady=(0, 14), **pad)

        # 進度條 + 狀態文字
        self.status_label = tk.Label(self.root, text="尚未開始", fg=MUTED, bg=BG, font=(FONT_FAMILY, 9))
        self.status_label.pack(anchor="w", padx=20)

        style = ttk.Style()
        try:
            style.theme_use("default")
        except Exception:
            pass
        style.configure("Build.Horizontal.TProgressbar", troughcolor=BORDER, background=FG, thickness=6)
        self.progress = ttk.Progressbar(self.root, style="Build.Horizontal.TProgressbar", mode="determinate")
        self.progress.pack(fill="x", padx=20, pady=(4, 14))

        # 輸出 log
        tk.Label(self.root, text="輸出紀錄", fg=MUTED, bg=BG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20)
        log_frame = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(6, 20))
        self.log_text = tk.Text(log_frame, bg=CARD, fg=MUTED, font=(FONT_FAMILY, 9),
                                 relief="flat", wrap="word", state="disabled")
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scrollbar.pack(side="right", fill="y", pady=8)

    def _check_prerequisites(self):
        problems = []
        if not os.path.exists(ENTRY_SCRIPT):
            problems.append(f"找不到 {ENTRY_SCRIPT}")
        if not os.path.exists(os.path.join("ui", "config.html")):
            problems.append("找不到 ui/config.html")
        if not os.path.exists(os.path.join("ui", "index.html")):
            problems.append("找不到 ui/index.html")
        if not os.path.exists("installer_core.py"):
            problems.append("找不到 installer_core.py")
        if not os.path.exists("uninstall.py"):
            problems.append("找不到 uninstall.py")
        if not os.path.exists("splash_helper.py"):
            problems.append("找不到 splash_helper.py")
        if not os.path.exists("window_drag.py"):
            problems.append("找不到 window_drag.py")
        if not os.path.exists("disk_space.py"):
            problems.append("找不到 disk_space.py")
        if not os.path.exists("file_assoc.py"):
            problems.append("找不到 file_assoc.py")
        if not os.path.exists("lang_detect.py"):
            problems.append("找不到 lang_detect.py")
        if shutil.which("pyinstaller") is None:
            problems.append("找不到 pyinstaller，請先執行 pip install pyinstaller")
        if problems:
            self.warning_label.config(text="⚠ " + "；".join(problems) + "，請確認後再打包。")
            self.start_btn.config(state="disabled")

    def _browse_icon(self):
        path = filedialog.askopenfilename(title="選擇 ICO 圖示", filetypes=[("ICO Icon", "*.ico")])
        if path:
            self.icon_path = path
            self.icon_label.config(text=path, fg=FG)

    # ------------------------------------------------------------------
    # 打包流程（背景執行緒跑 PyInstaller，避免卡住整個視窗）
    # ------------------------------------------------------------------

    def _start_build(self):
        if self.building:
            return
        self.building = True
        self.start_btn.config(state="disabled", text="打包中...")
        self.icon_btn.config(state="disabled")
        self._clear_log()
        self.status_label.config(text="正在清理舊的編譯產物...", fg=MUTED)
        self._progress_ceiling = 5.0
        self._progress_displayed = 0.0
        self._progress_stopped = False
        self._ceiling_reached_at = None
        self.progress.config(value=0)
        threading.Thread(target=self._run_build, daemon=True).start()

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        self._log_lines = []

    def _append_log(self, line):
        self._log_lines.append(line)
        if len(self._log_lines) > 20:
            self._log_lines.pop(0)
        self.log_queue.put(("log", line))

    def _recent_log_lines(self):
        return self._log_lines

    def _is_output_exe_running(self):
        exe_name = f"{OUTPUT_NAME}.exe"
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            output = subprocess.check_output(
                f'tasklist /FI "IMAGENAME eq {exe_name}" /NH',
                shell=True, text=True, stderr=subprocess.DEVNULL, creationflags=creationflags,
            )
            return exe_name.lower() in output.lower()
        except Exception:
            return False

    def _clean_stale_dirs(self):
        """清除 dist/build，遇到檔案被鎖住時重試幾次，仍然失敗就丟出看得懂的中文錯誤，
        而不是讓 PyInstaller 在最後一步才丟出一長串 traceback。"""
        for stale_dir in ("dist", "build"):
            if not os.path.exists(stale_dir):
                continue
            last_error = None
            for attempt in range(3):
                try:
                    shutil.rmtree(stale_dir)
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    time.sleep(1)
            if last_error is not None:
                raise RuntimeError(
                    f"無法清除舊的 {stale_dir} 資料夾，裡面可能有檔案正在被使用中"
                    f"（例如上一次打包出來的 {OUTPUT_NAME}.exe 還在執行、被防毒軟體掃描，"
                    f"或被檔案總管開著預覽）。請關閉相關程式/視窗後再試一次。"
                )

    def _run_build(self):
        try:
            if self._is_output_exe_running():
                self.log_queue.put((
                    "done", False,
                    f"打包失敗：偵測到 {OUTPUT_NAME}.exe 目前正在執行中，"
                    f"PyInstaller 無法覆寫正在執行的檔案，請先關閉它再重新打包。",
                ))
                return

            self._clean_stale_dirs()

            self.log_queue.put(("progress", 15, "正在準備打包指令..."))

            cmd = [
                "pyinstaller",
                "--onefile",
                "--noconsole",
                f"--name={OUTPUT_NAME}",
                "--add-data=ui;ui",
                "--add-data=installer_core.py;.",
                "--add-data=uninstall.py;.",
                "--add-data=splash_helper.py;.",
                # window_drag.py / disk_space.py / file_assoc.py / lang_detect.py 是
                # installer_core.py 跟 uninstall.py 共用的深模組——這兩支之後會被
                # builder.py 另外拉去重新編譯成獨立的 exe（見 gui_config.py 的
                # ensure_workspace_files()），所以這裡也要當成資源內嵌，執行時才能
                # 複製到工作目錄讓那次編譯找得到。
                "--add-data=window_drag.py;.",
                "--add-data=disk_space.py;.",
                "--add-data=file_assoc.py;.",
                "--add-data=lang_detect.py;.",
                # pywebview 支援多種 GUI 後端，PyInstaller 靜態分析是保守做法，
                # 只要程式碼「有可能」用到某個後端就整包塞進去。Windows 上
                # pywebview 實際只會用 EdgeChromium（靠 pythonnet 接 WebView2），
                # PyQt5/PySide 這些替代後端一行都用不到，排除掉可以省下相當可觀的體積。
                "--exclude-module=PyQt5",
                "--exclude-module=PyQt6",
                "--exclude-module=PySide2",
                "--exclude-module=PySide6",
                "--exclude-module=gi",
            ]
            if self.icon_path:
                cmd.append(f"--icon={self.icon_path}")
            cmd.append(ENTRY_SCRIPT)

            self._append_log("$ " + " ".join(cmd))
            self.log_queue.put(("progress", 25, "正在執行 PyInstaller（此步驟需要數十秒）..."))

            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            # 這裡送出的只是「目前已知的真實進度目標」，不是畫面上要顯示的數字。
            # PyInstaller 常常有一大段時間完全不輸出任何一行（尤其 Building EXE 那段），
            # 如果進度只綁在「每收到一行 +多少」，遇到長時間沒輸出就會整個卡住不動。
            # 實際的動畫（追趕、追上後緩慢自行往前爬、封頂 99%）交給
            # _poll_log_queue() 用計時器統一處理，不受有沒有新輸出行影響。
            progress_target = 30.0
            for line in process.stdout:
                line = line.rstrip("\n")
                if line:
                    self._append_log(line)
                progress_target = min(progress_target + 0.3, 85.0)
                self.log_queue.put(("progress", progress_target, "正在編譯..."))

            returncode = process.wait()

            if returncode != 0:
                if "PermissionError" in "".join(self._recent_log_lines()) or "WinError 5" in "".join(self._recent_log_lines()):
                    self.log_queue.put((
                        "done", False,
                        f"打包失敗：存取被拒。{OUTPUT_NAME}.exe 可能還在執行中，"
                        f"或被防毒軟體/檔案總管鎖住，請關閉後再試一次。",
                    ))
                else:
                    self.log_queue.put(("done", False, "打包失敗，請檢查上方輸出紀錄。"))
                return

            exe_path = os.path.join("dist", f"{OUTPUT_NAME}.exe")
            if not os.path.exists(exe_path):
                self.log_queue.put(("done", False, "PyInstaller 回報成功，但找不到產出的 exe，請檢查上方輸出紀錄。"))
                return

            self.log_queue.put(("done", True, f"打包完成！輸出位置: {os.path.abspath(exe_path)}"))

        except FileNotFoundError:
            self.log_queue.put(("done", False, "找不到 pyinstaller，請先執行 pip install pyinstaller。"))
        except Exception as e:
            self.log_queue.put(("done", False, f"發生未預期的錯誤: {e}"))

    # ------------------------------------------------------------------
    # 背景執行緒 -> 主執行緒的橋接（Tkinter 元件只能在主執行緒操作）
    # ------------------------------------------------------------------

    def _poll_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._write_log(item[1])
                elif kind == "progress":
                    _, value, status = item
                    self._progress_ceiling = max(self._progress_ceiling, value)
                    self.status_label.config(text=status, fg=MUTED)
                elif kind == "done":
                    _, success, message = item
                    self._finish_build(success, message)
        except queue.Empty:
            pass

        self._advance_progress_animation()
        self.root.after(100, self._poll_log_queue)

    def _advance_progress_animation(self):
        """每 100ms 執行一次：平滑追趕 _progress_ceiling（背景執行緒回報的真實進度），
        追上之後如果還沒完成就自行緩慢往前爬，最高封頂 99%，避免長時間沒有新輸出
        （PyInstaller 常常好一段時間完全不印東西）時進度條整個凍住不動。
        只有真的完成（_progress_ceiling 被設為 100）才會加速追到 100%。

        自行往前爬的速度隨「追上真實進度之後過了多久」指數衰減：剛追上時爬快一點，
        等得越久爬得越慢，做出「快一下、越來越慢」的節奏，而不是死板的等速直線。
        這個階段通常等最久（尤其編譯主安裝檔那個最後階段），衰減曲線自然跑得最久、
        看起來也最慢，不需要另外特別判斷「這是不是最後階段」。

        跟 config.html 那邊用 requestAnimationFrame 做的是同一套邏輯，
        這裡改用 Tkinter 的 100ms 計時器驅動。"""
        if not self.building or self._progress_stopped:
            return

        now = time.monotonic()

        if self._progress_ceiling >= 100:
            self._progress_displayed += max(2.0, (100 - self._progress_displayed) * 0.3)
            if self._progress_displayed >= 99.9:
                self._progress_displayed = 100
            self._ceiling_reached_at = None
        elif self._progress_displayed < self._progress_ceiling:
            gap = self._progress_ceiling - self._progress_displayed
            self._progress_displayed = min(self._progress_displayed + max(0.5, gap * 0.15), self._progress_ceiling)
            self._ceiling_reached_at = None
        else:
            if self._ceiling_reached_at is None:
                self._ceiling_reached_at = now
            elapsed = now - self._ceiling_reached_at
            speed = 1.2 * (0.85 ** elapsed)  # 每次 tick（100ms）的量，隨 elapsed 秒數指數衰減
            self._progress_displayed = min(self._progress_displayed + max(speed, 0.02), 99)

        self.progress.config(value=self._progress_displayed)

    def _write_log(self, line):
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _finish_build(self, success, message):
        self.building = False
        self.start_btn.config(state="normal", text="開始打包")
        self.icon_btn.config(state="normal")
        if success:
            self._progress_ceiling = 100  # 讓 _advance_progress_animation 自己加速追到 100
            self.status_label.config(text=message, fg=SUCCESS)
        else:
            self._progress_stopped = True  # 失敗了，動畫停在目前位置，不要再繼續爬
            self._progress_displayed = 0
            self.progress.config(value=0)
            self.status_label.config(text=message, fg=ERROR)


def main():
    # 讓 Windows 在非 100% 縮放比例（例如 125%）下不要把整個視窗畫面當點陣圖拉伸，
    # 這步一定要在建立任何 Tk 視窗之前呼叫才有效。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE（改用系統層級，跟 pywebview 原生視窗拖曳交接的相容性較好，避免拖曳瞬間跳動）
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    BuilderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()