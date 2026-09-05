"""
build_config_tool.py
---------------------
「配置精靈」打包器：把 gui_config.py（打包工具的 GUI 版）跟 builder_cli.py
（打包工具的 CLI 版，見 builder_cli.py 的說明）各自編譯成獨立 exe。

兩種執行模式：
  - 互動模式（不帶參數，`python build_config_tool.py`）：開 Tkinter 視窗，
    只編譯 GUI 版（`InstallerBuilder.exe`），供開發者手動、偶爾用一下，
    有進度條 + log 區塊的視覺回饋。
  - 非互動模式（`python build_config_tool.py --cli [--version X.Y.Z]
    [--icon xxx.ico]`）：不開任何視窗，依序編譯 GUI 版跟 CLI 版兩顆 exe，
    檔名各自嵌入版本號（`mac-style-windows-installer_GUI_vX.Y.Z.exe` /
    `mac-style-windows-installer_CLI_vX.Y.Z.exe`），給 `/released` 這類
    自動化流程呼叫，不需要人在電腦前面點按鈕。

跟 builder.py 的差異：builder.py 是這個工具「執行時」用來生成別人安裝檔的模組；
這支腳本是拿來打包這個工具「自己」（GUI 跟 CLI 兩個版本），職責不同所以獨立成一支檔案。

用 Tkinter 而不是 pywebview 做互動模式：這支只是內部偶爾會用到的建置小工具，
Tkinter 是標準函式庫、啟動快、不需要額外依賴，場合上比較合適。

使用前提：
  - 這支腳本要跟 gui_config.py、packaging_core.py、builder_cli.py、splash.py、
    splash_helper.py、builder.py、installer_core.py、uninstall.py、ui/
    放在同一層。
  - 執行環境要先安裝 pyinstaller、pywebview（`pip install -r requirements.txt`，
    套件清單與各項用途見該檔）。

修正/新增紀錄：
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
    現在內嵌進去後，packaging_core.py 的 ensure_workspace_files() 會在需要時
    自動把它們解壓到工作目錄，不需要使用者手動複製檔案。
  - 拿掉 --splash=splash.png（PyInstaller 原生的靜態圖片載入畫面），改成
    --add-data=splash_helper.py;.，內嵌一支獨立的載入畫面腳本，執行時用
    系統上真正的 python 直譯器去跑它，開出一個貨真價實、持續轉動的
    Tkinter 進度條視窗，取代原本只能放靜態圖片的做法。細節見 splash.py。
  - 打包前清除舊產物原本是整個清空 dist/、build/ 資料夾，在 --cli 模式
    依序編譯 GUI、CLI 兩顆 exe 時，編第二顆會把第一顆已經編好、放在
    dist/ 底下的 exe 一起刪掉。改成只清除這次要編的目標自己的產物
    （dist/{output_name}.exe、build/{output_name}/、{output_name}.spec），
    不動其他目標已經編好的檔案。
  - 【架構調整】實際建置一顆 exe 的邏輯（清理 dist/build、組 PyInstaller
    指令、跑子行程、串流輸出、檢查產出）從 BuilderGUI 這個 class 裡抽成
    獨立的 build_one_exe() 函式：互動模式的 BuilderGUI 跟新增的 --cli
    非互動模式都呼叫同一份，只是進度/log 怎麼呈現不一樣（前者接回
    Tkinter 的 log_queue，後者直接印到 stdout）。同時新增 --cli 模式，
    讓打包工具本身也能被指令驅動、不需要開視窗，呼應「打包工具要有 GUI/
    CLI 兩種介面」的整體方向——這裡指的是 build_config_tool.py 自己，
    不是它打包出來的 gui_config.py/builder_cli.py（那兩個各自就是 GUI
    介面跟 CLI 介面本身）。
"""

import argparse
import os
import sys
import shutil
import subprocess
import tempfile
import threading
import queue
import time
import ctypes
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, ttk

import packaging_core
import version_info

ENTRY_SCRIPT = "gui_config.py"
CLI_ENTRY_SCRIPT = "builder_cli.py"
OUTPUT_NAME = "InstallerBuilder"
PROJECT_NAME = "mac-style-windows-installer"
FONT_FAMILY = "Microsoft JhengHei"

BG = "#ffffff"
CARD = "#f4f4f5"
FG = "#09090b"
MUTED = "#71717a"
BORDER = "#e4e4e7"
SUCCESS = "#34C759"
ERROR = "#FF6B6B"

# 兩顆 exe（GUI 版、CLI 版）都要內嵌的共用深模組：packaging_core.py 讀取
# installer_core.py/uninstall.py 的 ensure_workspace_files()，跟這兩支
# entry point 實際 import 的專案內部深模組，必須實際存在於磁碟上才能在
# builder.py 另外呼叫的 pyinstaller 子行程裡被找到。這份清單直接沿用
# packaging_core.py 的 ENTRY_SCRIPTS/SHARED_DEEP_MODULES（唯一真實來源），
# 不在這裡另外維護一份手動同步的複本——真實抓到的 bug：install_scope.py/
# self_delete.py/system_entries.py 都曾經因為兩邊清單各自維護、忘記同步
# 更新，導致打包出來的 exe 一執行就 ModuleNotFoundError（見
# tests/test_shared_module_packaging.py）。splash_helper.py 只有 GUI 版
# 需要（CLI 沒有 Tkinter 載入畫面）。
_SHARED_ADD_DATA = packaging_core.ENTRY_SCRIPTS + packaging_core.SHARED_DEEP_MODULES
_GUI_ADD_DATA = _SHARED_ADD_DATA + ["splash_helper.py", "packaging_core.py"]
_CLI_ADD_DATA = _SHARED_ADD_DATA + ["packaging_core.py", "builder.py"]

_REQUIRED_FILES = [
    ("gui_config.py", "gui_config.py"),
    ("builder_cli.py", "builder_cli.py"),
    ("packaging_core.py", "packaging_core.py"),
    ("builder.py", "builder.py"),
    ("ui/config.html", os.path.join("ui", "config.html")),
    ("ui/index.html", os.path.join("ui", "index.html")),
    ("ui/uninstall.html", os.path.join("ui", "uninstall.html")),
    # 拖曳手勢的共用實作，安裝端與解除安裝端都以 <script src> 載入它。
    # 缺了的話兩邊的畫面都還畫得出來，但圖示完全拖不動——編得出一顆
    # 跑起來才發現核心動作失效的安裝檔，所以列進編譯前的檢查。
    ("ui/spring.js", os.path.join("ui", "spring.js")),
    ("ui/drag_to_target.js", os.path.join("ui", "drag_to_target.js")),
    # 介面翻譯，三份畫面都以 <script src> 載入它。缺了的話畫面上全部是
    # data-i18n 的預設文字、按鈕也沒有文字，同樣是「編得出來但跑起來不對」。
    ("ui/i18n.js", os.path.join("ui", "i18n.js")),
    ("splash_helper.py", "splash_helper.py"),
] + [(name, name) for name in _SHARED_ADD_DATA]


def check_prerequisites():
    """檢查編譯這兩顆 exe 需要的原始碼檔案跟外部工具是否齊全，回傳問題清單
    （空清單代表一切正常）。互動模式（BuilderGUI）跟 --cli 模式共用同一份
    檢查，不會有兩邊標準兜不起來的情況。
    """
    problems = [f"找不到 {label}" for label, path in _REQUIRED_FILES if not os.path.exists(path)]
    if shutil.which("pyinstaller") is None:
        problems.append("找不到 pyinstaller，請先執行 pip install pyinstaller")
    return problems


def read_version(explicit_version=None):
    """決定要嵌進輸出檔名的版本號：明確傳入的優先，否則讀取 repo 根目錄的
    VERSION 檔案（單一真實來源，/released skill 發布時會更新它）。
    VERSION 不存在時用一個一看就知道是「還沒正式定版」的預設值，不會悄悄
    冒充一個看起來正常的版本號騙過使用者。
    """
    if explicit_version:
        return explicit_version
    version_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
    try:
        with open(version_path, "r", encoding="utf-8") as f:
            version = f.read().strip()
            if version:
                return version
    except Exception:
        pass
    print("[警告] 找不到 VERSION 檔案（或內容是空的），使用預設版本號 0.0.0-dev。")
    return "0.0.0-dev"


def build_one_exe(entry_script, output_name, icon_path=None, noconsole=True,
                   extra_add_data=None, on_log=None, on_progress=None,
                   version=None, publisher="", file_description=None):
    """建置單一顆 exe：清理 dist/build、組 PyInstaller 指令、跑子行程、
    串流輸出、檢查產出檔案。互動模式的 BuilderGUI 跟 --cli 非互動模式都
    呼叫這個函式，核心邏輯只有一份。

    version/publisher/file_description：有給 version 才會生成
    --version-file 讓輸出的 exe 帶上 Win32 VERSIONINFO 資源（見
    version_info.py）；沒給 version 就完全跳過（互動模式的 BuilderGUI
    目前還沒收集這幾個欄位，維持原本「不帶 --version-file」的行為，
    不強迫使用者一定要填）。

    on_log(line: str)：每一行 PyInstaller 輸出都會呼叫一次。
    on_progress(percent: float, status: str)：進度里程碑會呼叫。
    回傳 (success: bool, message: str, exe_path: str|None)。
    """
    def log(line):
        if on_log:
            on_log(line)

    def progress(value, status):
        if on_progress:
            on_progress(value, status)

    recent_lines = []

    def track(line):
        recent_lines.append(line)
        if len(recent_lines) > 20:
            recent_lines.pop(0)
        log(line)

    exe_name = f"{output_name}.exe"

    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # errors="replace"：text=True 未指定 errors 時依系統地區編碼解碼子行程
        # 輸出（繁體中文 Windows 是 cp950），遇到該編碼無法解碼的位元組會拋出
        # 例外，被下方的 except 當成「沒有在執行」——偵測失靈且不留痕跡，要到
        # PyInstaller 覆寫檔案失敗時才顯露。詳見 docs/investigations/子行程輸出的解碼修正.md。
        output = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
            text=True, errors="replace",
            stderr=subprocess.DEVNULL, creationflags=creationflags,
        )
        if exe_name.lower() in output.lower():
            return False, (
                f"打包失敗：偵測到 {exe_name} 目前正在執行中，"
                f"PyInstaller 無法覆寫正在執行的檔案，請先關閉它再重新打包。"
            ), None
    except Exception:
        pass

    stale_paths = [
        os.path.join("dist", exe_name),
        os.path.join("build", output_name),
        f"{output_name}.spec",
    ]
    for stale_path in stale_paths:
        if not os.path.exists(stale_path):
            continue
        remove = shutil.rmtree if os.path.isdir(stale_path) else os.remove
        last_error = None
        for attempt in range(3):
            try:
                remove(stale_path)
                last_error = None
                break
            except Exception as e:
                last_error = e
                time.sleep(1)
        if last_error is not None:
            return False, (
                f"無法清除舊的 {stale_path}，裡面可能有檔案正在被使用中"
                f"（例如上一次打包出來的 {exe_name} 還在執行、被防毒軟體掃描，"
                f"或被檔案總管開著預覽）。請關閉相關程式/視窗後再試一次。"
            ), None

    progress(15, "正在準備打包指令...")

    cmd = ["pyinstaller", "--onefile"]
    if noconsole:
        cmd.append("--noconsole")
    cmd.append(f"--name={output_name}")
    cmd.append("--add-data=ui;ui")
    for extra in (extra_add_data or []):
        cmd.append(f"--add-data={extra};.")
    cmd += [
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
    if icon_path:
        cmd.append(f"--icon={icon_path}")

    # 有給 version 才生成 --version-file，讓輸出的 exe 帶上 Win32
    # VERSIONINFO 資源（見 version_info.py）。互動模式的 BuilderGUI 目前
    # 沒收集 version/publisher，維持原本不帶這個旗標的行為。
    version_file_dir = None
    if version:
        version_file_dir = tempfile.mkdtemp()
        version_file_path = os.path.join(version_file_dir, "version_info.txt")
        version_info.write_version_file(
            version_file_path,
            product_name=PROJECT_NAME,
            file_version=version,
            file_description=file_description or output_name,
            company_name=publisher,
            legal_copyright=f"Copyright © {datetime.now().year} {publisher}",
        )
        cmd.append(f"--version-file={version_file_path}")

    cmd.append(entry_script)

    track("$ " + " ".join(cmd))
    progress(25, "正在執行 PyInstaller（此步驟需要數十秒）...")

    try:
        try:
            # encoding/errors：不使用 text=True 的預設解碼行為（依系統地區
            # 編碼），PyInstaller 輸出含有非該編碼的位元組時，下方的逐行讀取會
            # 當場拋出例外，整個打包中斷在一個與真正失敗原因無關的錯誤上。
            # 指定 UTF-8 是因為 PyInstaller 本身是 Python 程式。
            # 詳見 docs/investigations/子行程輸出的解碼修正.md。
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, universal_newlines=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            return False, "找不到 pyinstaller，請先執行 pip install pyinstaller。", None

        # 這裡送出的只是「目前已知的真實進度目標」，不是畫面上要顯示的數字。
        # PyInstaller 常常有一大段時間完全不輸出任何一行（尤其 Building EXE 那段），
        # 如果進度只綁在「每收到一行 +多少」，遇到長時間沒輸出就會整個卡住不動。
        # 呼叫端（BuilderGUI._poll_log_queue()）用計時器統一處理「追趕、追上後
        # 緩慢自行往前爬、封頂 99%」的動畫，不受有沒有新輸出行影響；CLI 模式
        # 則直接印這裡送出的里程碑，不做動畫。
        progress_target = 30.0
        for line in process.stdout:
            line = line.rstrip("\n")
            if line:
                track(line)
            progress_target = min(progress_target + 0.3, 85.0)
            progress(progress_target, "正在編譯...")

        returncode = process.wait()

        if returncode != 0:
            joined = "".join(recent_lines)
            if "PermissionError" in joined or "WinError 5" in joined:
                return False, (
                    f"打包失敗：存取被拒。{exe_name} 可能還在執行中，"
                    f"或被防毒軟體/檔案總管鎖住，請關閉後再試一次。"
                ), None
            return False, "打包失敗，請檢查上方輸出紀錄。", None

        exe_path = os.path.join("dist", exe_name)
        if not os.path.exists(exe_path):
            return False, "PyInstaller 回報成功，但找不到產出的 exe，請檢查上方輸出紀錄。", None

        return True, f"打包完成！輸出位置: {os.path.abspath(exe_path)}", os.path.abspath(exe_path)
    finally:
        if version_file_dir:
            shutil.rmtree(version_file_dir, ignore_errors=True)


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
        problems = check_prerequisites()
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

    def _run_build(self):
        try:
            success, message, exe_path = build_one_exe(
                entry_script=ENTRY_SCRIPT,
                output_name=OUTPUT_NAME,
                icon_path=self.icon_path or None,
                noconsole=True,
                extra_add_data=_GUI_ADD_DATA,
                on_log=self._append_log,
                on_progress=lambda value, status: self.log_queue.put(("progress", value, status)),
            )
            self.log_queue.put(("done", success, message))
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


def run_cli(version=None, icon_path=None, publisher=""):
    """非互動模式：依序編譯 GUI 版跟 CLI 版兩顆 exe，檔名嵌入版本號。
    給 /released skill 或其他自動化流程呼叫，不開任何視窗。回傳 process
    exit code（0 = 兩顆都成功，1 = 任一顆失敗）。
    """
    problems = check_prerequisites()
    if problems:
        print("環境檢查失敗：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    resolved_version = read_version(version)
    targets = [
        (ENTRY_SCRIPT, f"{PROJECT_NAME}_GUI_v{resolved_version}", True, _GUI_ADD_DATA,
         f"{PROJECT_NAME} GUI"),
        (CLI_ENTRY_SCRIPT, f"{PROJECT_NAME}_CLI_v{resolved_version}", False, _CLI_ADD_DATA,
         f"{PROJECT_NAME} CLI"),
    ]

    for entry_script, output_name, noconsole, extra_add_data, file_description in targets:
        print(f"=== 正在編譯 {output_name}.exe（進入點：{entry_script}）===")
        success, message, exe_path = build_one_exe(
            entry_script=entry_script,
            output_name=output_name,
            icon_path=icon_path,
            noconsole=noconsole,
            extra_add_data=extra_add_data,
            on_log=print,
            on_progress=lambda value, status: print(f"[{value:.0f}%] {status}"),
            version=resolved_version,
            publisher=publisher,
            file_description=file_description,
        )
        print(message)
        if not success:
            return 1

    return 0


def main():
    # 子行程（PyInstaller）的輸出以 errors="replace" 解碼，可能含有 cp950
    # 編不出來的替代字元；不先放寬這裡，在繁體中文的 Windows 上印那一行就會
    # 讓整個編譯中止。見 packaging_core.make_console_forgiving()。
    packaging_core.make_console_forgiving(sys.stdout)
    packaging_core.make_console_forgiving(sys.stderr)

    parser = argparse.ArgumentParser(description="打包工具建置器：編譯 InstallerBuilder 的 GUI/CLI 兩顆 exe。")
    parser.add_argument("--cli", action="store_true", help="非互動模式：依序編譯 GUI/CLI 兩顆 exe，不開視窗")
    parser.add_argument("--version", default=None, help="嵌進輸出檔名的版本號，沒帶就讀取 VERSION 檔案")
    parser.add_argument("--icon", default=None, help="輸出 exe 的圖示（.ico，選填）")
    parser.add_argument("--publisher", default="", help="嵌進輸出 exe 的 VERSIONINFO 資源的發行者/公司名稱（選填）")
    args = parser.parse_args()

    if args.cli:
        sys.exit(run_cli(version=args.version, icon_path=args.icon, publisher=args.publisher))

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
