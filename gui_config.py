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
from window_drag import WindowDragController


# 跟 __main__ 裡 webview.create_window() 的 min_size 保持一致，
# 自訂縮放邏輯（resize_move）用同一組數字做下限，避免縮到比原本設計的最小可用尺寸還小。
MIN_WINDOW_WIDTH = 650
MIN_WINDOW_HEIGHT = 720


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
        # 效能考量：原本這裡分兩次呼叫 subprocess（各自測 import webview / import win32com），
        # 每次都要重新啟動一個完整的 Python 直譯器，這個開銷不小，而且工具每次開啟
        # 都要重付一次。合併成一個子行程、一次測完兩件事，直接砍半這筆固定成本。
        probe_script = (
            "import sys\n"
            "try:\n"
            "    import webview\n"
            "    print('WEBVIEW_OK')\n"
            "except Exception:\n"
            "    pass\n"
            "try:\n"
            "    import win32com.client\n"
            "    print('PYWIN32_OK')\n"
            "except Exception:\n"
            "    pass\n"
        )
        try:
            proc = subprocess.run(
                [python_path, "-c", probe_script],
                capture_output=True, timeout=15, creationflags=creationflags, text=True,
            )
            output = proc.stdout or ""
            result["webview_found"] = "WEBVIEW_OK" in output
            result["pywin32_found"] = "PYWIN32_OK" in output
        except Exception:
            result["webview_found"] = False
            result["pywin32_found"] = False

    result["ready"] = result["pyinstaller_found"] and result["python_found"] and result["webview_found"]
    return result


def ensure_workspace_files(workspace_dir):
    """確保 installer_core.py、uninstall.py、以及 ui/ 資料夾底下所有靜態資源
    （index.html、folder_icon.png 等）都存在於工作目錄。

    只有 frozen exe 模式才需要真的動手複製：.py 直接執行時，這些檔案本來就
    跟原始碼放在一起，workspace_dir 就是原始碼目錄，不用處理。
    複製失敗（例如工作目錄沒有寫入權限）會回傳錯誤訊息字串；一切正常回傳 None。

    修正紀錄：
      - 原本只複製 ui/index.html 一個檔案，漏掉 index.html 裡實際引用到的
        folder_icon.png，導致編譯出來的安裝檔右側資料夾圖示消失。現在改成把內嵌的
        整個 ui 資料夾內容都複製過去，之後 index.html 不管引用到哪個靜態資源都不會漏。
      - 【重要】installer_core.py / uninstall.py / ui/index.html 原本用「只在工作目錄
        缺少這個檔案時才複製」，代表如果重複用同一個工作目錄打包新版 InstallerBuilder.exe
        （例如修了 bug 之後重新打包），工作目錄裡卡著的舊版本永遠不會被換掉——不管
        重新編譯幾次新的 exe，實際被拿去用的都還是最早那次留下的過期程式碼，任何
        後續修正都不會真的生效，卻不會有任何錯誤訊息提示。現在改成這幾個內部實作
        檔案一律覆蓋更新，隨時跟目前這顆 exe 內嵌的版本保持同步；至於 ui/ 裡其他
        使用者可能自訂過的靜態資源（例如 folder_icon.png），維持「只在缺少時才補」，
        不會覆蓋掉使用者自己換上去的圖示。
    """
    if not hasattr(sys, "_MEIPASS"):
        return None

    # installer_core.py / uninstall.py 是要被 builder.py 各自拉去重新編譯成
    # 獨立 exe 的進入點；window_drag.py / disk_space.py / file_assoc.py 是它們
    # 匯入的共用深模組，同樣要在工作目錄裡才能被那兩次 pyinstaller 呼叫找到。
    required_scripts = [
        "installer_core.py", "uninstall.py",
        "window_drag.py", "disk_space.py", "file_assoc.py",
    ]

    try:
        os.makedirs(os.path.join(workspace_dir, "ui"), exist_ok=True)

        for name in required_scripts:
            dest = os.path.join(workspace_dir, name)
            shutil.copy2(get_resource_path(name), dest)

        embedded_ui_dir = get_resource_path("ui")
        if os.path.isdir(embedded_ui_dir):
            for name in os.listdir(embedded_ui_dir):
                src = os.path.join(embedded_ui_dir, name)
                dest = os.path.join(workspace_dir, "ui", name)
                if not os.path.isfile(src):
                    continue
                if name == "index.html":
                    # 安裝端介面實作，同樣不是使用者自訂項目，要跟著同步更新
                    shutil.copy2(src, dest)
                elif not os.path.exists(dest):
                    # 其他靜態資源使用者可能自己換過（例如 folder_icon.png），
                    # 只在缺少時才補上，不要覆蓋使用者的客製化。
                    shutil.copy2(src, dest)

        return None
    except Exception as e:
        return (
            f"無法在工作目錄（{workspace_dir}）準備必要的建置檔案：{e}。"
            f"請確認這個資料夾有寫入權限（例如不要放在 C:\\Program Files 底下），"
            f"或改把 InstallerBuilder.exe 移到有寫入權限的資料夾再執行。"
        )


def validate_and_build_pack_data(data, app_dir, png_path, ico_path, doc_icon_path_selected):
    """驗證 start_pack() 收到的表單資料，並組出要交給 builder.build_all() 的 pack_data。

    純函式：不碰執行緒、不呼叫 check_build_environment()/ensure_workspace_files()
    這類有外部副作用的檢查——那些留在 start_pack() 裡，跟這裡回傳的結果合併。
    這樣驗證邏輯可以直接單元測試，不需要真的啟動背景執行緒或呼叫外部指令。

    回傳 (pack_data, None) 表示驗證通過；(None, error_message) 表示驗證失敗，
    error_message 就是原本要包進 {"status": "error", "message": ...} 的內容。
    """
    app_name = data.get("app_name", "").strip()
    folder_name = data.get("folder_name", "").strip() or app_name
    version = data.get("version", "").strip()
    publisher = data.get("publisher", "").strip()
    exe_name = data.get("exe_name", "").strip()
    main_exe = data.get("main_exe", "").strip()
    eula_text = data.get("eula_text", "").strip()
    dependencies = data.get("dependencies", []) or []
    file_assoc_raw = data.get("file_associations", "").strip()
    need_file_assoc = bool(data.get("need_file_assoc", False))
    use_custom_doc_icon = bool(data.get("use_custom_doc_icon", False))
    add_to_path = bool(data.get("add_to_path", False))

    if not app_name or not version or not publisher or not exe_name:
        return None, "欄位驗證失敗：<br>所有文字欄位（名稱、版本、發行者、安裝檔名）皆為必填項目，請檢查是否有欄位遺漏。"

    if need_file_assoc and not file_assoc_raw:
        return None, "欄位驗證失敗：<br>已勾選「需要註冊檔案關聯」，請填入至少一個副檔名，或取消勾選。"

    if not app_dir or not os.path.exists(app_dir):
        return None, "欄位驗證失敗：<br>請選擇有效的應用程式內容資料夾。"

    if not png_path or not png_path.lower().endswith('.png'):
        return None, "欄位驗證失敗：<br>請選擇介面拖拽專用的 PNG 圖示檔案。"

    if not ico_path or not ico_path.lower().endswith('.ico'):
        return None, "欄位驗證失敗：<br>請選擇執行檔封面專用的 ICO 圖示檔案。"

    if not main_exe:
        return None, "欄位驗證失敗：<br>請選擇應用程式的主要執行檔（.exe），這是建立捷徑、偵測執行中狀態、立即執行等功能所必需的。"

    if not os.path.exists(os.path.join(app_dir, main_exe)):
        return None, "欄位驗證失敗：<br>選擇的主要執行檔不存在於應用程式資料夾中，請重新選擇。"

    doc_icon_path = ""
    if use_custom_doc_icon:
        if not doc_icon_path_selected or not doc_icon_path_selected.lower().endswith('.ico'):
            return None, "欄位驗證失敗：<br>已勾選自訂文件圖示，請選擇一顆 ICO 檔案，或取消勾選改沿用應用程式圖示。"
        doc_icon_path = doc_icon_path_selected

    try:
        folder_contents = os.listdir(app_dir)
        if len(folder_contents) == 0:
            return None, "拒絕編譯：<br>所選的應用程式資料夾內部是空的，請確認已放入軟體檔案。"
    except Exception as e:
        return None, f"讀取資料夾失敗: {e}"

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
    pack_data["doc_icon_path"] = doc_icon_path
    pack_data["dependencies"] = dependencies
    pack_data["eula_text"] = eula_text
    pack_data["main_exe"] = main_exe
    pack_data["add_to_path"] = add_to_path
    return pack_data, None


class ConfigAPI:
    def __init__(self):
        self.app_dir = ""
        self.png_path = ""
        self.ico_path = ""
        self.doc_icon_path = ""
        self._window = None
        self._drag = WindowDragController()
        self._resize_origin = None

    def set_window(self, window):
        """安全地綁定視窗實體"""
        self._window = window

    def start_drag(self, cursor_x, cursor_y):
        self._drag.start_drag(self._window, cursor_x, cursor_y)

    def drag_move(self, cursor_x, cursor_y):
        self._drag.drag_move(self._window, cursor_x, cursor_y)

    def end_drag(self):
        self._drag.end_drag()

    def start_resize(self, edge, cursor_x, cursor_y):
        """自訂縮放開始：記錄按下當下的滑鼠座標與視窗當下大小。

        create_window() 雖然設了 resizable=True，但視窗同時是 frameless=True
        （無邊框），無邊框視窗沒有系統原生的邊界可以拖曳縮放，resizable=True
        形同虛設。跟拖曳視窗一樣，自己刻縮放邏輯：前端在視窗邊緣做幾條看不見的
        感應區，按下時呼叫這裡記錄基準點，之後用位移量算新的視窗大小。
        edge 是 'right' / 'bottom' / 'right-bottom' 其中之一，決定要動寬度、
        高度，還是兩個一起動。
        """
        if self._window:
            self._resize_origin = (edge, cursor_x, cursor_y, self._window.width, self._window.height)

    def resize_move(self, cursor_x, cursor_y):
        """縮放中：用滑鼠位移量算新的視窗大小，並套用最小尺寸限制。"""
        if not self._window or not self._resize_origin:
            return
        edge, start_cx, start_cy, start_w, start_h = self._resize_origin
        dx = cursor_x - start_cx
        dy = cursor_y - start_cy
        new_w, new_h = start_w, start_h
        if "right" in edge:
            new_w = max(MIN_WINDOW_WIDTH, start_w + dx)
        if "bottom" in edge:
            new_h = max(MIN_WINDOW_HEIGHT, start_h + dy)
        self._window.resize(int(new_w), int(new_h))

    def end_resize(self):
        """縮放結束：清掉基準點。"""
        self._resize_origin = None

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

    def select_doc_icon(self):
        """選擇檔案關聯用的專屬文件圖示（選填，不選就沿用主程式圖示）"""
        window = webview.active_window()
        res = window.create_file_dialog(webview.OPEN_DIALOG, file_types=['ICO Icon (*.ico)'])
        if res:
            self.doc_icon_path = res[0]
            return self.doc_icon_path
        return ""

    def clear_doc_icon(self):
        """取消勾選「自訂文件圖示」時呼叫，把後端記住的路徑也一併清空。

        原本只有前端畫面重置（勾選框、顯示文字），self.doc_icon_path 這個
        後端變數不會跟著清掉——機率很低，但如果同一次工具開啟期間使用者
        選過圖示又取消、後續操作路徑比較繞，理論上可能撿到舊路徑。
        補上這個方法，讓前後端狀態確實一致。
        """
        self.doc_icon_path = ""

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

        pack_data, error = validate_and_build_pack_data(
            data, self.app_dir, self.png_path, self.ico_path, self.doc_icon_path,
        )
        if error:
            return {"status": "error", "message": error}

        workspace_dir = get_workspace_dir()
        prep_error = ensure_workspace_files(workspace_dir)
        if prep_error:
            return {"status": "error", "message": f"環境準備失敗：<br>{prep_error}"}
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
                doc_icon_path=data.get("doc_icon_path", ""),
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
        min_size=(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT),
        frameless=True,
        easy_drag=False,
    )

    api.set_window(window)

    # 視窗內容真正載入完成、準備顯示的當下才關閉 splash，避免出現「splash 消失後還要再等一下」的空窗
    window.events.loaded += lambda: splash.close()

    webview.start(debug=False)