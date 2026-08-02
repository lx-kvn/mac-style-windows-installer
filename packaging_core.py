"""
packaging_core.py
------------------
打包工具（#1）真正的核心邏輯：跟 pywebview／GUI 完全無關，GUI
（`gui_config.py` 的 `ConfigAPI`）跟 CLI（`builder_cli.py`）都呼叫這裡的
函式，各自只負責「資料從哪裡來」（表單 vs. JSON/命令列參數）跟「進度/
結果怎麼呈現」（`window.evaluate_js()` vs. 印到 stdout）。

拆分紀錄：這幾個函式原本都定義在 `gui_config.py` 裡，其實從來沒有依賴
`webview`／`splash`——真正 GUI 專屬的只有 `ConfigAPI` 這個 class（視窗
拖曳/縮放、檔案選擇對話框、`start_pack()` 的背景執行緒 + JS 進度回報）。
但 `gui_config.py` 檔案開頭就 `import webview`，只要 CLI 進入點直接
`from gui_config import ...`，就會強迫純指令、可能沒有 GUI 環境（例如
CI）的使用情境也要裝 pywebview，這裡搬出來就是為了讓 CLI 完全不需要
pywebview 也能跑。比照這個專案既有「拆出共用深模組」的慣例（見
`file_assoc.py`/`window_drag.py`/`disk_space.py`/`lang_detect.py`）。
"""

import sys
import os
import shutil
import subprocess


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
    （或被 ensure_workspace_files() 解壓出）installer_core.py、uninstall.py、
    ui/index.html，dist/、build/ 等編譯產物也會落在這裡，方便使用者找到輸出結果。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.path.abspath(".")


def check_build_environment():
    """檢查「編譯安裝檔」這個動作背後需要的外部環境。

    注意這跟「這支工具自己開不開得起來」是兩件事：不管這裡檢查的外部環境
    齊不齊全，工具本身（GUI 或 CLI）一定跑得起來。這裡檢查的是 builder.py
    執行編譯時會另外呼叫的外部 pyinstaller 指令，以及它背後那個 python
    直譯器有沒有裝 pywebview（installer_core.py 需要 import webview，沒裝
    的話 pyinstaller 分析階段就會直接失敗）。pywin32 只影響捷徑功能，
    缺了不擋編譯，單獨標示為建議安裝。
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
    # 獨立 exe 的進入點；window_drag.py / disk_space.py / file_assoc.py /
    # lang_detect.py / restart_manager.py 是它們匯入的共用深模組，同樣要在
    # 工作目錄裡才能被那兩次 pyinstaller 呼叫找到。
    required_scripts = [
        "installer_core.py", "uninstall.py",
        "window_drag.py", "disk_space.py", "file_assoc.py", "lang_detect.py",
        "restart_manager.py",
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
            f"或改把打包工具移到有寫入權限的資料夾再執行。"
        )


def validate_and_build_pack_data(data, app_dir, png_path, ico_path, doc_icon_path_selected):
    """驗證表單/JSON 資料，並組出要交給 builder.build_all() 的 pack_data。

    純函式：不碰執行緒、不呼叫 check_build_environment()/ensure_workspace_files()
    這類有外部副作用的檢查——那些留在呼叫端（GUI 的 start_pack()、CLI 的
    pack 子指令）裡，跟這裡回傳的結果合併。這樣驗證邏輯可以直接單元測試，
    不需要真的啟動背景執行緒或呼叫外部指令。GUI 跟 CLI 共用同一份驗證，
    不會有兩邊規則兜不起來的問題。

    回傳 (pack_data, None) 表示驗證通過；(None, error_message) 表示驗證失敗，
    error_message 就是原本要包進 {"status": "error", "message": ...} 的內容。
    """
    app_name = data.get("app_name", "").strip()
    folder_name = data.get("folder_name", "").strip() or app_name
    version = data.get("version", "").strip()
    publisher = data.get("publisher", "").strip()
    exe_name = data.get("exe_name", "").strip()
    main_exe = data.get("main_exe", "").strip()
    eula_texts_raw = data.get("eula_texts", {}) or {}
    eula_texts = {
        str(lang).strip(): text.strip()
        for lang, text in eula_texts_raw.items()
        if str(lang).strip() and str(text).strip()
    }
    eula_default_lang = data.get("eula_default_lang", "").strip()
    dependencies = data.get("dependencies", []) or []
    file_assoc_raw = data.get("file_associations", "").strip()
    need_file_assoc = bool(data.get("need_file_assoc", False))
    use_custom_doc_icon = bool(data.get("use_custom_doc_icon", False))
    add_to_path = bool(data.get("add_to_path", False))
    path_target_exe = data.get("path_target_exe", "").strip()
    local_appdata_files_raw = data.get("local_appdata_files", []) or []
    restart_explorer_on_update = bool(data.get("restart_explorer_on_update", False))

    if not app_name or not version or not publisher or not exe_name:
        return None, "欄位驗證失敗：<br>所有文字欄位（名稱、版本、發行者、安裝檔名）皆為必填項目，請檢查是否有欄位遺漏。"

    if need_file_assoc and not file_assoc_raw:
        return None, "欄位驗證失敗：<br>已勾選「需要註冊檔案關聯」，請填入至少一個副檔名，或取消勾選。"

    if eula_texts and eula_default_lang not in eula_texts:
        return None, "欄位驗證失敗：<br>已新增多語言 EULA，請從中選擇一個「預設/回退語言」。"

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

    if add_to_path and path_target_exe and not os.path.exists(os.path.join(app_dir, path_target_exe)):
        return None, "欄位驗證失敗：<br>「加入 PATH」指定的執行檔不存在於應用程式資料夾中，請重新選擇。"

    if isinstance(local_appdata_files_raw, str):
        local_appdata_files_raw = local_appdata_files_raw.replace("，", ",").split(",")
    local_appdata_files = [str(f).strip().replace("\\", "/") for f in local_appdata_files_raw if str(f).strip()]
    for rel in local_appdata_files:
        if not os.path.exists(os.path.join(app_dir, rel)):
            return None, f"欄位驗證失敗：<br>指定改裝到 %LOCALAPPDATA% 的檔案「{rel}」不存在於應用程式資料夾中，請重新選擇。"

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

    # 每個副檔名各自的專屬文件圖示（選填）：{副檔名: 圖示絕對路徑}，
    # 不在這裡指定的副檔名會 fallback 用共用的 doc_icon，兩者都沒有就沿用
    # 主程式圖示（實際的 fallback 順序在 installer_core.py 的
    # _resolve_doc_icon_ref()）。
    doc_icons_raw = data.get("doc_icons", {}) or {}
    doc_icons = {}
    for raw_ext, icon_path in doc_icons_raw.items():
        ext = str(raw_ext).strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        icon_path = str(icon_path or "").strip()
        if not icon_path:
            continue
        if ext not in file_associations:
            return None, f"欄位驗證失敗：<br>幫副檔名「{ext}」設定了專屬圖示，但它不在檔案關聯清單裡，請先把它加進檔案關聯清單，或移除這個圖示設定。"
        if not icon_path.lower().endswith(".ico"):
            return None, f"欄位驗證失敗：<br>副檔名「{ext}」指定的專屬圖示不是有效的 ICO 檔案，請重新選擇。"
        doc_icons[ext] = icon_path

    pack_data = dict(data)
    pack_data["folder_name"] = folder_name
    pack_data["file_associations"] = file_associations
    pack_data["doc_icon_path"] = doc_icon_path
    pack_data["doc_icons"] = doc_icons
    pack_data["dependencies"] = dependencies
    pack_data["eula_texts"] = eula_texts
    pack_data["eula_default_lang"] = eula_default_lang
    pack_data["main_exe"] = main_exe
    pack_data["add_to_path"] = add_to_path
    pack_data["path_target_exe"] = path_target_exe if add_to_path else ""
    pack_data["local_appdata_files"] = local_appdata_files
    pack_data["restart_explorer_on_update"] = restart_explorer_on_update
    return pack_data, None
