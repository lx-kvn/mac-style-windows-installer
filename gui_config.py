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
    ensure_workspace_files() 偵測「工作目錄（exe 所在資料夾）底下缺什麼」，
    缺的話自動從內嵌資源解壓出來，讓使用者不需要手動複製檔案。

架構拆分（GUI/CLI 雙介面）：
  - `get_resource_path`/`get_workspace_dir`/`check_build_environment`/
    `ensure_workspace_files`/`validate_and_build_pack_data` 這幾個函式
    原本定義在這裡，其實完全不依賴 pywebview，已經搬到 `packaging_core.py`
    （新的、不需要 pywebview 就能 import 的共用核心模組），CLI 進入點
    `builder_cli.py` 才能在沒裝 pywebview 的環境也能跑。這支檔案現在只留
    `ConfigAPI` 這個 GUI 專屬 class（視窗拖曳/縮放、檔案選擇對話框、
    `start_pack()` 的背景執行緒 + JS 進度回報），透過
    `from packaging_core import ...` 取用共用邏輯。
"""

import sys
import os
import json
import webview
import splash
import builder
import threading
import lang_detect
import install_engine
import packaging_settings
import sdk_tools
from window_drag import WindowDragController
from packaging_core import (
    get_resource_path,
    get_workspace_dir,
    check_build_environment,
    ensure_workspace_files,
    validate_and_build_pack_data,
    list_app_dir_files as scan_app_dir_files,
)

# 打包工具本身的介面語言選項，對應 ui/config.html 內嵌的 I18N 翻譯表。
BUILDER_UI_LANGUAGES = ["zh-TW", "en"]
DEFAULT_BUILDER_UI_LANGUAGE = "zh-TW"


# 跟 __main__ 裡 webview.create_window() 的 min_size 保持一致，
# 自訂縮放邏輯（resize_move）用同一組數字做下限，避免縮到比原本設計的最小可用尺寸還小。
MIN_WINDOW_WIDTH = 650
MIN_WINDOW_HEIGHT = 720


class ConfigAPI:
    def __init__(self):
        self.app_dir = ""
        self.png_path = ""
        self.ico_path = ""
        self.doc_icon_path = ""
        self.cert_path = ""
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

    def get_system_language(self):
        """供前端決定語言下拉選單的初始值：使用者第一次開啟本工具、
        localStorage 裡還沒記住任何選擇時，用這台電腦的系統語言當預設值。
        """
        return lang_detect.detect_system_language(BUILDER_UI_LANGUAGES, DEFAULT_BUILDER_UI_LANGUAGE)

    def get_engine_field_categories(self, engine, lang=None):
        """這個引擎下有哪些設定不相容，各屬哪一類、就地提示要顯示什麼。

        回傳 `{欄位: {"category": ..., "hint": ...}}`，與這次填了什麼無關——
        就地標記的用途是事前告知，使用者還沒填就該看得到這一格在這個模式下
        不能用（第十四輪決議第四項）。

        分類與文字都由 `install_engine.py` 提供，前端不自行維護一份欄位
        清單：那份清單與後端分岔時的症狀是某個欄位悄悄不再被標記，而那不會
        有任何東西會叫。

        認不得的引擎回傳空字典而不拋例外：引擎值來自前端，為此拋例外會讓
        整個畫面停住。
        """
        if engine != install_engine.MSIX:
            return {}
        lang = lang or install_engine.DEFAULT_LANGUAGE
        return {
            field: {
                "category": category,
                "hint": install_engine.category_hint(category, lang),
            }
            for field, category in install_engine.field_categories().items()
        }

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

    def get_current_workspace_dir(self):
        """供前端在畫面載入時呼叫，顯示目前實際會用到的編譯工作目錄
        （使用者自訂過就是那個位置，沒自訂過就是保證可寫入的預設值，
        見 packaging_core.get_workspace_dir()）。"""
        return get_workspace_dir()

    def select_workspace_dir(self):
        """讓使用者自訂編譯工作目錄（例如想改放到別的磁碟），選好立刻
        持久化記住，下次開啟這支工具會直接沿用，不用每次重選。"""
        window = webview.active_window()
        res = window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return ""
        chosen = res[0]
        settings = packaging_settings.load_settings()
        settings["workspace_dir"] = chosen
        packaging_settings.save_settings(settings)
        return chosen

    def reset_workspace_dir(self):
        """取消自訂，改回保證可寫入的預設工作目錄。"""
        settings = packaging_settings.load_settings()
        settings.pop("workspace_dir", None)
        packaging_settings.save_settings(settings)
        return get_workspace_dir()

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

    def select_extension_icon(self):
        """選擇某個副檔名專屬的文件圖示（見 config.html 的個別副檔名圖示區塊）。

        故意不寫回 self.doc_icon_path——那是「所有副檔名共用的預設圖示」
        單獨欄位用的後端狀態，這裡只是單純的檔案選擇對話框，選到的路徑
        直接回傳給前端，由前端自己記在對應副檔名的 JS 物件裡，兩邊狀態
        不會互相污染。
        """
        window = webview.active_window()
        res = window.create_file_dialog(webview.OPEN_DIALOG, file_types=['ICO Icon (*.ico)'])
        if res:
            return res[0]
        return ""

    def select_msix_icon(self):
        """選擇 MSIX 三個宣告位置之一的覆蓋圖示（見 config.html 的 msix 區塊）。

        與 select_extension_icon() 同樣不寫回任何 self.*_path：那些屬性是
        「單一欄位對單一路徑」用的後端狀態，三個位置共用一組會互相蓋掉。
        選到的路徑直接回傳，由前端記在對應位置上。

        只收 PNG：MSIX 的套件圖示必須是 PNG（第五輪決議第一項），這裡先擋
        住可以讓使用者當場就知道，不必等到打包階段才被驗證退回。
        """
        window = webview.active_window()
        res = window.create_file_dialog(webview.OPEN_DIALOG, file_types=['PNG Image (*.png)'])
        if res:
            return res[0]
        return ""

    def select_cert_file(self):
        """選擇數位簽章用的 PFX 憑證檔案（signing 設定，見規格文件 §8.27）"""
        window = webview.active_window()
        res = window.create_file_dialog(webview.OPEN_DIALOG, file_types=['PFX Certificate (*.pfx)'])
        if res:
            self.cert_path = res[0]
            return self.cert_path
        return ""

    def clear_doc_icon(self):
        """取消勾選「自訂文件圖示」時呼叫，把後端記住的路徑也一併清空。

        原本只有前端畫面重置（勾選框、顯示文字），self.doc_icon_path 這個
        後端變數不會跟著清掉——機率很低，但如果同一次工具開啟期間使用者
        選過圖示又取消、後續操作路徑比較繞，理論上可能撿到舊路徑。
        補上這個方法，讓前後端狀態確實一致。
        """
        self.doc_icon_path = ""

    def list_app_dir_files(self):
        """掃描目前選定的 app_dir，回傳裡面所有檔案的相對路徑（不限副檔名），
        供前端渲染成分支圖勾選要改裝到 %LOCALAPPDATA% 的檔案（見
        local_appdata_files），取代原本要手動輸入逗號分隔路徑的做法。
        掃描邏輯收在 packaging_core.list_app_dir_files()，CLI 的
        list-files 指令共用同一份實作。"""
        return scan_app_dir_files(self.app_dir)

    def list_exe_files(self):
        """掃描目前選定的 app_dir，回傳裡面所有 .exe 的相對路徑，供前端下拉選單選擇主執行檔"""
        return [p for p in self.list_app_dir_files() if p.lower().endswith(".exe")]

    def start_pack(self, data, install_password="", lang=None):
        """接收前端表單資料，執行嚴格驗證並啟動背景線程打包。

        install_password：使用者在「啟用安裝密碼保護」區塊選擇「直接輸入
        密碼」時填的那組密碼。它是一個獨立參數、不在 `data` 裡，理由見
        docs/adr/0004——`data` 的欄位集合就是設定檔的格式，把密碼放進去
        等於同時讓設定檔能寫明文密碼。同理它也不會被寫進 pack_data，而是
        一路以獨立參數傳到 builder.build_all()。
        """
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
            has_inline_password=bool(install_password),
            # 介面語言由前端送來：後端沒有別的管道知道使用者在畫面上選了
            # 哪一種語言（GUI 的語言記在 localStorage）。沒送就用預設值。
            lang=lang or install_engine.DEFAULT_LANGUAGE,
        )
        if error:
            return {"status": "error", "message": error}

        workspace_dir = get_workspace_dir()
        prep_error = ensure_workspace_files(workspace_dir)
        if prep_error:
            return {"status": "error", "message": f"環境準備失敗：<br>{prep_error}"}
        # 在動手之前先問一次工作目錄齊不齊。MSIX 模式下 makeappx 打包與
        # signtool 簽章（含一次連到時間戳記伺服器的往返）都發生在 build_all
        # 之前，資源檢查若留到那時才跑，那些力氣就白花了，而且使用者看到的
        # 是進度跑了一半才失敗，不是按下去就被擋。
        missing = builder.missing_workspace_resources(
            workspace_dir,
            is_msix=pack_data.get("install_engine") == install_engine.MSIX,
        )
        if missing:
            return {"status": "error", "message": f"環境準備失敗：<br>{missing}"}
        pack_data["workspace_dir"] = workspace_dir

        threading.Thread(
            target=self._run_pack_thread, args=(pack_data, install_password),
        ).start()
        return {"status": "processing", "message": "驗證通過，開始編譯流程。"}

    def _run_pack_thread(self, data, install_password=""):
        """在背景線程中安全執行打包。install_password 見 start_pack()。"""

        def progress_handler(percent, status_msg, cap=99, time_constant=15):
            safe_msg = json.dumps(status_msg, ensure_ascii=False)
            if self._window:
                self._window.evaluate_js(
                    f"window.updateProgress({percent}, {safe_msg}, {cap}, {time_constant})"
                )

        workspace_dir = data.get("workspace_dir", ".")
        exe_name = data.get("exe_name").strip()
        sdk_settings = packaging_settings.load_settings()

        signed_msix = ""
        if data.get("install_engine") == install_engine.MSIX:
            # 憑證在不在本機決定走哪一條（第十三輪決議第三項，判準與 CLI 相同）。
            # `signing.cert_path` 一律是本機 .pfx（驗證階段已確認檔案存在），
            # 因此「有 signing」與「憑證是本機檔案」是同一件事。
            signing = data.get("signing") or None
            try:
                packed = builder.build_msix(
                    app_dir=self.app_dir,
                    pack_data=data,
                    png_path=self.png_path,
                    # 放在工作目錄底下、不放進 dist/：後者會在編 bootstrapper
                    # exe 之前被清空，中間產物擺在那裡會在被內嵌之前就消失。
                    output_path=os.path.join(
                        workspace_dir,
                        f"{(data.get('msix') or {}).get('identity_name', 'package')}.msix",
                    ),
                    workspace_dir=workspace_dir,
                    doc_icon_path=data.get("doc_icon_path", ""),
                    signing=signing,
                    sdk_tools_settings=sdk_settings,
                    log=lambda message: progress_handler(30, message, 99, 2),
                )
            except Exception as e:
                if self._window:
                    safe_err = json.dumps(str(e), ensure_ascii=False)
                    self._window.evaluate_js(f"window.packComplete('error', {safe_err})")
                return

            if not signing:
                # 憑證不在本機，流程到此為止。這不是失敗——使用者拿到了一份
                # 真正可用的產物；但也不能報成「編譯完成」，按下按鈕的人本來
                # 預期拿到一顆安裝檔，說成完成會讓他以為東西已經齊了。
                if self._window:
                    msg = json.dumps(
                        f"套件已產出：\n{packed}\n\n"
                        "這份套件尚未簽章，因此還沒有編出安裝檔——未簽章的套件無法部署。\n"
                        "請自行簽章（本機憑證或雲端代簽），再以 CLI 的\n"
                        "  builder_cli.py pack --signed-msix <已簽章的.msix>\n"
                        "編出安裝檔。\n\n"
                        "若憑證就在這台機器上，把它填進「數位簽章」區塊，"
                        "這兩步會自動一次完成，不需要分開跑。",
                        ensure_ascii=False,
                    )
                    self._window.evaluate_js(f"window.packComplete('success', {msg})")
                return
            signed_msix = packed

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
                eula_texts=data.get("eula_texts", {}),
                eula_default_lang=data.get("eula_default_lang", ""),
                dependencies=data.get("dependencies", []),
                file_associations=data.get("file_associations", []),
                doc_icon_path=data.get("doc_icon_path", ""),
                doc_icons=data.get("doc_icons", {}),
                add_to_path=data.get("add_to_path", False),
                path_target_exe=data.get("path_target_exe", ""),
                local_appdata_files=data.get("local_appdata_files", []),
                restart_explorer_on_update=data.get("restart_explorer_on_update", False),
                no_admin_install=data.get("no_admin_install", False),
                custom_install_dir=data.get("custom_install_dir", ""),
                pre_install_script=data.get("pre_install_script", ""),
                post_install_script=data.get("post_install_script", ""),
                custom_dependencies=data.get("custom_dependencies", []),
                bundle_dependencies=data.get("bundle_dependencies", []),
                signing=data.get("signing"),
                windows_service=data.get("windows_service", {}),
                scheduled_task=data.get("scheduled_task", {}),
                dependencies_min_version=data.get("dependencies_min_version", {}),
                create_restore_point_before_install=data.get("create_restore_point_before_install", False),
                install_password_env=data.get("install_password_env", ""),
                # 密碼本身來自獨立參數，不是 data 的一個欄位（見 docs/adr/0004）。
                install_password=install_password,
                workspace_dir=workspace_dir,
                install_engine=data.get("install_engine", install_engine.TRADITIONAL),
                signed_msix=signed_msix,
                engine_notices=data.get("engine_notices"),
                sdk_tools_settings=sdk_settings,
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