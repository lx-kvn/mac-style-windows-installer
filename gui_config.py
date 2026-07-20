"""
gui_config.py
-------------
「配置精靈」視窗的後端 API（pywebview js_api）。

新增紀錄：
  - list_exe_files()：選完應用程式資料夾後，掃描裡面的 .exe 檔案回傳給前端，
    讓使用者從下拉選單選出「主要執行檔」，而不是用猜的。這個欄位是這一輪
    新功能（單一實例鎖、執行中偵測、捷徑目標、立即執行、PATH）共用的基礎資訊，
    所以驗證時列為必填。
  - start_pack() 新增驗證與傳遞：eula_text（可留空）、main_exe（必填）、
    dependencies（相依元件勾選清單）、file_associations（副檔名清單）、
    add_to_path（是否加入環境變數）。
  - 沿用上一輪修正：json.dumps() 安全傳字串進 JS，取代手動 escape。
  - 新增啟動載入畫面（splash）：不論是 .py 直接執行還是打包成 exe 都會顯示，
    細節見 splash.py（改版後統一用系統 python 執行獨立的 splash_helper.py，
    不再需要重新呼叫 gui_config.py 自己）。

修正紀錄（重要）：
  - builder.build_all() 需要 ui/index.html、installer_core.py、uninstall.py
    實際存在於磁碟上（它會另外呼叫一次 pyinstaller 子行程去編譯這些檔案）。
    這幾個檔案原本只有 .py 直接執行時才會跟原始碼放在一起；打包成 exe 後，
    這支 exe 執行時的工作目錄底下並不會自動有這些東西，導致
    「找不到 ui 資料夾或 ui/index.html 基礎資源」。
    現在改成：build_config_tool.py 打包時一併把 installer_core.py、
    uninstall.py、ui/index.html 內嵌進 InstallerBuilder.exe；執行時由
    _ensure_workspace_files() 偵測「工作目錄（exe 所在資料夾）底下缺什麼」，
    缺的話自動從內嵌資源解壓出來，讓使用者不需要手動複製檔案。
"""

import sys
import os
import json
import shutil
import subprocess
import webview
import splash
import builder
import threading


def get_resource_path(relative_path):
    """獲取資源絕對路徑，相容 .py 直接執行與 PyInstaller onefile 打包後的環境。

    原本用 os.path.abspath(".") 只在工作目錄剛好是原始碼目錄時才找得到 ui/config.html，
    打包成 onefile exe 後 --add-data 的內容會被解壓縮到 sys._MEIPASS 暫存目錄，
    不是工作目錄，原本的寫法在 exe 型態下一定找不到檔案，這裡一併修正。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


def get_workspace_dir():
    """決定這次建置作業要用的工作目錄。

    .py 直接執行：就是目前的工作目錄（跟原始碼放在一起，維持原行為）。
    frozen exe：固定用「exe 所在的資料夾」，因為 builder.py 需要在這裡找到
    （或被 _ensure_workspace_files() 解壓出）installer_core.py、uninstall.py、
    ui/index.html，dist/、build/ 等編譯產物也會落在這裡，方便使用者找到輸出結果。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.path.abspath(".")


def check_build_environment():
    """檢查「編譯安裝檔」這個動作背後需要的外部環境。

    注意這跟「這支工具自己開不開得起來」是兩件事：gui_config.py 打包成 exe 後，
    它自己的 UI 完全內嵌了 Python 執行環境，不管這裡檢查的外部環境齊不齊全，
    exe 本身一定打得開。這裡檢查的是 builder.py 執行編譯時會另外呼叫的外部
    pyinstaller 指令，以及它背後那個 python 直譯器有沒有裝 pywebview
    （installer_core.py 需要 import webview，沒裝的話 pyinstaller 分析階段
    就會直接失敗）。pywin32 只影響捷徑功能，缺了不擋編譯，單獨標示為建議安裝。
    """
    result = {
        "pyinstaller_found": False,
        "python_found": False,
        "python_path": "",
        "webview_found": False,
        "pywin32_found": False,
        "ready": False,
    }

    pyinstaller_path = shutil.which("pyinstaller")
    result["pyinstaller_found"] = pyinstaller_path is not None

    python_path = shutil.which("python") or shutil.which("python3") or shutil.which("py")
    result["python_found"] = python_path is not None
    result["python_path"] = python_path or ""

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    if python_path:
        try:
            proc = subprocess.run(
                [python_path, "-c", "import webview"],
                capture_output=True, timeout=15, creationflags=creationflags,
            )
            result["webview_found"] = proc.returncode == 0
        except Exception:
            result["webview_found"] = False

        try:
            proc = subprocess.run(
                [python_path, "-c", "import win32com.client"],
                capture_output=True, timeout=15, creationflags=creationflags,
            )
            result["pywin32_found"] = proc.returncode == 0
        except Exception:
            result["pywin32_found"] = False

    result["ready"] = result["pyinstaller_found"] and result["python_found"] and result["webview_found"]
    return result


def ensure_workspace_files(workspace_dir):
    """確保 installer_core.py、uninstall.py、以及 ui/ 資料夾底下所有靜態資源
    （index.html、folder_icon.png 等）都存在於工作目錄。

    只有 frozen exe 模式才需要真的動手複製：.py 直接執行時，這些檔案本來就
    跟原始碼放在一起，workspace_dir 就是原始碼目錄，不用處理。
    複製失敗（例如工作目錄沒有寫入權限）會回傳錯誤訊息字串；一切正常回傳 None。

    修正紀錄：原本只複製 ui/index.html 一個檔案，漏掉 index.html 裡實際引用到的
    folder_icon.png，導致編譯出來的安裝檔右側資料夾圖示消失。現在改成把內嵌的
    整個 ui 資料夾內容都複製過去，之後 index.html 不管引用到哪個靜態資源都不會漏。
    """
    if not hasattr(sys, "_MEIPASS"):
        return None

    required_scripts = ["installer_core.py", "uninstall.py"]

    try:
        os.makedirs(os.path.join(workspace_dir, "ui"), exist_ok=True)

        for name in required_scripts:
            dest = os.path.join(workspace_dir, name)
            if not os.path.exists(dest):
                shutil.copy2(get_resource_path(name), dest)

        embedded_ui_dir = get_resource_path("ui")
        if os.path.isdir(embedded_ui_dir):
            for name in os.listdir(embedded_ui_dir):
                src = os.path.join(embedded_ui_dir, name)
                dest = os.path.join(workspace_dir, "ui", name)
                if os.path.isfile(src) and not os.path.exists(dest):
                    shutil.copy2(src, dest)

        return None
    except Exception as e:
        return (
            f"無法在工作目錄（{workspace_dir}）準備必要的建置檔案：{e}。"
            f"請確認這個資料夾有寫入權限（例如不要放在 C:\\Program Files 底下），"
            f"或改把 InstallerBuilder.exe 移到有寫入權限的資料夾再執行。"
        )


class ConfigAPI:
    def __init__(self):
        self.app_dir = ""
        self.png_path = ""
        self.ico_path = ""
        self._window = None
        self._drag_origin = None

    def set_window(self, window):
        """安全地綁定視窗實體"""
        self._window = window

    def start_drag(self, cursor_x, cursor_y):
        """自訂拖曳開始：記錄按下當下的滑鼠螢幕座標與視窗當下座標，作為位移量的計算基準。

        不用 pywebview 內建的 pywebview-drag-region：那個機制在拖曳開始瞬間會讓視窗
        往左上方跳一下才跟上游標，100% 縮放下也會發生，判斷是機制本身的問題。
        改成完全自己算位移量、呼叫 window.move()，徹底繞開這個問題。
        """
        if self._window:
            self._drag_origin = (cursor_x, cursor_y, self._window.x, self._window.y)

    def drag_move(self, cursor_x, cursor_y):
        """拖曳中：用目前滑鼠螢幕座標相對於按下當下的位移量搬動視窗。"""
        if self._window and self._drag_origin:
            start_cx, start_cy, start_wx, start_wy = self._drag_origin
            dx = cursor_x - start_cx
            dy = cursor_y - start_cy
            self._window.move(start_wx + dx, start_wy + dy)

    def end_drag(self):
        """拖曳結束：清掉基準點。"""
        self._drag_origin = None

    def check_environment(self):
        """供前端在畫面載入時呼叫，檢查編譯安裝檔所需的外部環境是否齊全。"""
        return check_build_environment()

    def open_url(self, url):
        """讓前端可以開啟預設瀏覽器前往下載頁（例如缺 Python 時導去官網）。"""
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False

    def close_window(self):
        if self._window:
            self._window.destroy()

    def minimize_window(self):
        if self._window:
            self._window.minimize()

    def select_app_dir(self):
        """選擇要打包的應用程式資料夾"""
        window = webview.active_window()
        res = window.create_file_dialog(webview.FOLDER_DIALOG)
        if res:
            self.app_dir = res[0]
            return self.app_dir
        return ""

    def select_png_icon(self):
        window = webview.active_window()
        res = window.create_file_dialog(webview.OPEN_DIALOG, file_types=['PNG Image (*.png)'])
        if res:
            self.png_path = res[0]
            return self.png_path
        return ""

    def select_ico_icon(self):
        window = webview.active_window()
        res = window.create_file_dialog(webview.OPEN_DIALOG, file_types=['ICO Icon (*.ico)'])
        if res:
            self.ico_path = res[0]
            return self.ico_path
        return ""

    def list_exe_files(self):
        """掃描目前選定的 app_dir，回傳裡面所有 .exe 的相對路徑，供前端下拉選單選擇主執行檔"""
        if not self.app_dir or not os.path.exists(self.app_dir):
            return []
        results = []
        for root, dirs, files in os.walk(self.app_dir):
            for f in files:
                if f.lower().endswith(".exe"):
                    rel = os.path.relpath(os.path.join(root, f), self.app_dir)
                    results.append(rel.replace("\\", "/"))
        return results

    def start_pack(self, data):
        """接收前端表單資料，執行嚴格驗證並啟動背景線程打包"""
        env = check_build_environment()
        if not env["ready"]:
            missing = []
            if not env["pyinstaller_found"]:
                missing.append("pyinstaller")
            if not env["python_found"]:
                missing.append("python")
            if env["python_found"] and not env["webview_found"]:
                missing.append("pywebview")
            return {
                "status": "error",
                "message": "環境檢查失敗：<br>缺少 " + "、".join(missing) +
                            "，請先安裝必要環境後再試一次（畫面載入時的環境提示視窗有詳細安裝指令）。",
            }

        app_name = data.get("app_name", "").strip()
        folder_name = data.get("folder_name", "").strip() or app_name
        version = data.get("version", "").strip()
        publisher = data.get("publisher", "").strip()
        exe_name = data.get("exe_name", "").strip()
        main_exe = data.get("main_exe", "").strip()
        eula_text = data.get("eula_text", "").strip()
        dependencies = data.get("dependencies", []) or []
        file_assoc_raw = data.get("file_associations", "").strip()
        add_to_path = bool(data.get("add_to_path", False))

        if not app_name or not version or not publisher or not exe_name:
            return {
                "status": "error",
                "message": "欄位驗證失敗：<br>所有文字欄位（名稱、版本、發行者、安裝檔名）皆為必填項目，請檢查是否有欄位遺漏。",
            }

        if not self.app_dir or not os.path.exists(self.app_dir):
            return {"status": "error", "message": "欄位驗證失敗：<br>請選擇有效的應用程式內容資料夾。"}

        if not self.png_path or not self.png_path.lower().endswith('.png'):
            return {"status": "error", "message": "欄位驗證失敗：<br>請選擇介面拖拽專用的 PNG 圖示檔案。"}

        if not self.ico_path or not self.ico_path.lower().endswith('.ico'):
            return {"status": "error", "message": "欄位驗證失敗：<br>請選擇執行檔封面專用的 ICO 圖示檔案。"}

        if not main_exe:
            return {"status": "error", "message": "欄位驗證失敗：<br>請選擇應用程式的主要執行檔（.exe），這是建立捷徑、偵測執行中狀態、立即執行等功能所必需的。"}

        if not os.path.exists(os.path.join(self.app_dir, main_exe)):
            return {"status": "error", "message": "欄位驗證失敗：<br>選擇的主要執行檔不存在於應用程式資料夾中，請重新選擇。"}

        try:
            folder_contents = os.listdir(self.app_dir)
            if len(folder_contents) == 0:
                return {"status": "error", "message": "拒絕編譯：<br>所選的應用程式資料夾內部是空的，請確認已放入軟體檔案。"}
        except Exception as e:
            return {"status": "error", "message": f"讀取資料夾失敗: {e}"}

        workspace_dir = get_workspace_dir()
        prep_error = ensure_workspace_files(workspace_dir)
        if prep_error:
            return {"status": "error", "message": f"環境準備失敗：<br>{prep_error}"}

        # 解析副檔名清單："txt, .abc,xyz" -> [".txt", ".abc", ".xyz"]
        file_associations = []
        if file_assoc_raw:
            for part in file_assoc_raw.replace("，", ",").split(","):
                ext = part.strip()
                if not ext:
                    continue
                if not ext.startswith("."):
                    ext = "." + ext
                file_associations.append(ext.lower())

        pack_data = dict(data)
        pack_data["folder_name"] = folder_name
        pack_data["file_associations"] = file_associations
        pack_data["dependencies"] = dependencies
        pack_data["eula_text"] = eula_text
        pack_data["main_exe"] = main_exe
        pack_data["add_to_path"] = add_to_path
        pack_data["workspace_dir"] = workspace_dir

        threading.Thread(target=self._run_pack_thread, args=(pack_data,)).start()
        return {"status": "processing", "message": "驗證通過，開始編譯流程。"}

    def _run_pack_thread(self, data):
        """在背景線程中安全執行打包"""

        def progress_handler(percent, status_msg, cap=99, time_constant=15):
            safe_msg = json.dumps(status_msg, ensure_ascii=False)
            if self._window:
                self._window.evaluate_js(
                    f"window.updateProgress({percent}, {safe_msg}, {cap}, {time_constant})"
                )

        workspace_dir = data.get("workspace_dir", ".")
        exe_name = data.get("exe_name").strip()

        try:
            builder.build_all(
                app_dir=self.app_dir,
                exe_name=exe_name,
                app_name=data.get("app_name").strip(),
                folder_name=data.get("folder_name") or data.get("app_name").strip(),
                version=data.get("version").strip(),
                publisher=data.get("publisher").strip(),
                png_path=self.png_path,
                ico_path=self.ico_path,
                main_exe=data.get("main_exe"),
                eula_text=data.get("eula_text", ""),
                dependencies=data.get("dependencies", []),
                file_associations=data.get("file_associations", []),
                add_to_path=data.get("add_to_path", False),
                workspace_dir=workspace_dir,
                progress_callback=progress_handler,
            )
            if self._window:
                dist_path = os.path.join(workspace_dir, "dist", f"{exe_name}.exe")
                success_msg = json.dumps(f"編譯完成！安裝檔已成功建立：\n{dist_path}", ensure_ascii=False)
                self._window.evaluate_js(f"window.packComplete('success', {success_msg})")
        except Exception as e:
            safe_err = json.dumps(str(e), ensure_ascii=False)
            if self._window:
                self._window.evaluate_js(f"window.packComplete('error', {safe_err})")


if __name__ == '__main__':
    splash.set_dpi_aware()
    splash.show("正在啟動配置精靈...")

    api = ConfigAPI()

    # 開發模式（.py 直接執行）下，ui 資料夾不存在就順手建一個避免直接炸掉；
    # frozen exe 模式下 ui 資料夾內容已經在打包時內嵌進 _MEIPASS，不需要也不應該在這裡動工作目錄。
    if not hasattr(sys, "_MEIPASS") and not os.path.exists("ui"):
        os.makedirs("ui")

    html_path = get_resource_path(os.path.join("ui", "config.html"))

    window = webview.create_window(
        title='配置精靈',
        url=html_path if os.path.exists(html_path) else "about:blank",
        js_api=api,
        width=680,
        height=760,
        resizable=True,
        min_size=(650, 720),
        frameless=True,
        easy_drag=False,
    )

    api.set_window(window)

    # 視窗內容真正載入完成、準備顯示的當下才關閉 splash，避免出現「splash 消失後還要再等一下」的空窗
    window.events.loaded += lambda: splash.close()

    webview.start(debug=False)