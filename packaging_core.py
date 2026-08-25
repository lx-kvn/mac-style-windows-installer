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
import re
import shutil
import subprocess

import packaging_settings
import dependency_defs
import windows_service

# installer_core.py/uninstall.py 這兩支 entry point 實際 import 的專案內部
# 深模組。真實抓到的 bug：install_scope.py/self_delete.py/system_entries.py
# 先後都漏列過，導致 frozen exe（mswi-gui.exe/mswi-cli.exe）打包出來的
# 安裝檔一執行就 ModuleNotFoundError——.py 直接執行完全不會踩到（工作目錄
# 本來就是原始碼目錄，什麼都找得到），只有透過這份清單被複製進工作目錄
# （ensure_workspace_files()）、也只有先被 build_config_tool.py 的
# --add-data 內嵌進 exe 裡，frozen exe 才真的找得到。新增任何一個
# installer_core.py/uninstall.py 會 import 的專案內部模組，都要同步加進
# 這裡（tests/test_shared_module_packaging.py 會自動比對、漏加會紅燈）。
ENTRY_SCRIPTS = ["installer_core.py", "uninstall.py"]
SHARED_DEEP_MODULES = [
    "window_drag.py", "disk_space.py", "file_assoc.py", "lang_detect.py",
    "restart_manager.py", "dependency_defs.py", "install_scope.py",
    "self_delete.py", "system_entries.py", "explorer_lock_release.py",
    "windows_service.py", "scheduled_task.py", "restore_point.py", "bits_download.py",
    "install_journal.py", "install_encryption.py", "progress_report.py",
    "dependency_install.py", "version_compare.py",
]


def get_resource_path(relative_path):
    """獲取資源絕對路徑，相容 .py 直接執行與 PyInstaller onefile 打包後的環境。

    原本用 os.path.abspath(".") 只在工作目錄剛好是原始碼目錄時才找得到 ui/config.html，
    打包成 onefile exe 後 --add-data 的內容會被解壓縮到 sys._MEIPASS 暫存目錄，
    不是工作目錄，原本的寫法在 exe 型態下一定找不到檔案，這裡一併修正。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


def default_workspace_dir():
    """frozen exe 情境下，保證可寫入的預設工作目錄。

    真實抓到的 bug：原本固定用「exe 自己所在的資料夾」，這支工具（GUI 版）
    如果被裝在 Program Files，一般權限執行時寫不進自己所在的資料夾，
    dist/、build/ 這些編譯產物建不出來，編譯/打包直接失敗——「裝完立刻
    啟動」之所以能用，是因為那次啟動繼承了安裝程式（--uac-admin）的
    提權權杖，之後從開始功能表/桌面捷徑正常雙擊打開就會踩到這個問題。
    改成固定用使用者層級、一定寫得進去的位置，跟這支 exe 裝在哪完全脫鉤。
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "mac-style-windows-installer", "workspace")


def get_workspace_dir():
    """決定這次建置作業要用的工作目錄。

    .py 直接執行：就是目前的工作目錄（跟原始碼放在一起，維持原行為）。
    frozen exe：優先用使用者透過 GUI 自訂並記住的位置（見
    packaging_settings.py），沒有自訂過就用 default_workspace_dir()。
    builder.py 需要在這裡找到（或被 ensure_workspace_files() 解壓出）
    installer_core.py、uninstall.py、ui/index.html，dist/、build/ 等編譯
    產物也會落在這裡。
    """
    if hasattr(sys, "_MEIPASS"):
        custom = packaging_settings.load_settings().get("workspace_dir")
        return custom or default_workspace_dir()
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


def list_app_dir_files(app_dir):
    """掃描 app_dir 底下所有檔案的相對路徑（不限副檔名，含子資料夾，用
    正斜線分隔），供 GUI 的分支圖勾選（gui_config.py 的 ConfigAPI）跟
    CLI 的 list-files 指令（builder_cli.py）共用同一份掃描邏輯。"""
    if not app_dir or not os.path.exists(app_dir):
        return []
    results = []
    for root, dirs, files in os.walk(app_dir):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), app_dir)
            results.append(rel.replace("\\", "/"))
    return sorted(results)


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
    # 獨立 exe 的進入點；SHARED_DEEP_MODULES 是它們匯入的共用深模組，同樣
    # 要在工作目錄裡才能被那兩次 pyinstaller 呼叫找到。
    required_scripts = ENTRY_SCRIPTS + SHARED_DEEP_MODULES

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
                if name in ("index.html", "uninstall.html"):
                    # 安裝端/解除安裝端介面實作，同樣不是使用者自訂項目，
                    # 要跟著同步更新
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


def _validate_signing_config(signing_raw):
    """驗證 signing 設定，回傳 (signing_dict_or_None, error_or_None)。

    signing 的驗證規則（憑證檔案存在、密碼環境變數有值）只跟 signing 自己
    有關，跟 custom_dependencies/no_admin_install 等其他欄位完全無關，
    原本混在 validate_and_build_pack_data() 那個大函式裡，只是因為大家都
    要塞進同一個 pack_data dict——獨立成一個函式，才能不用建構一整包
    app_dir/png_path 等其他欄位，直接單獨測 signing 的驗證規則。"""
    if not signing_raw:
        return None, None
    cert_path = str(signing_raw.get("cert_path", "")).strip()
    cert_password_env = str(signing_raw.get("cert_password_env", "")).strip()
    timestamp_url = str(signing_raw.get("timestamp_url", "")).strip()
    if not cert_path or not os.path.exists(cert_path):
        return None, "欄位驗證失敗：<br>signing.cert_path 必須指向一個實際存在的憑證檔案（.pfx）。"
    if not cert_password_env:
        return None, "欄位驗證失敗：<br>signing.cert_password_env 必須指定存放憑證密碼的環境變數名稱（密碼本身不放在設定檔裡）。"
    if not os.environ.get(cert_password_env):
        return None, f"欄位驗證失敗：<br>環境變數「{cert_password_env}」目前沒有值，請先設定好憑證密碼再打包。"
    return {
        "cert_path": cert_path,
        "cert_password_env": cert_password_env,
        "timestamp_url": timestamp_url or "http://timestamp.digicert.com",
    }, None


def _validate_install_password_env(install_password_env_raw):
    """驗證 install_password_env（安裝密碼保護，見 CONTEXT.md「安裝密碼
    保護」一節），回傳 (install_password_env_or_empty_string, error_or_None)。

    比照 _validate_signing_config() 的 cert_password_env 規則：密碼本身
    不放在設定檔裡，只存存放密碼的環境變數名稱；只檢查環境變數有沒有
    值，不額外要求密碼長度/複雜度——這是使用者自己開發環境裡設定的
    密碼，工具沒有立場替使用者決定「多長才算安全」，也要跟結構幾乎
    一樣的 cert_password_env 保持同一套規則，不要兩邊不同調。"""
    install_password_env = str(install_password_env_raw or "").strip()
    if not install_password_env:
        return "", None
    if not os.environ.get(install_password_env):
        return None, f"欄位驗證失敗：<br>環境變數「{install_password_env}」目前沒有值，請先設定好安裝密碼再打包。"
    return install_password_env, None


def _validate_dependency_policy(dependencies, custom_dependencies_raw, bundle_dependencies_raw):
    """驗證 custom_dependencies/bundle_dependencies，回傳
    (custom_dependencies, bundle_dependencies, error_or_None)。

    這三個欄位只跟彼此有關——bundle_dependencies 要交叉比對
    custom_dependencies 算出來的 key 清單，才知道「內嵌」這個要求指的是
    哪個相依元件——跟 signing/no_admin_install 這些不相關的欄位無關，
    獨立成一個函式才能單獨測交叉驗證的規則，不用管其他欄位。

    真實抓到的問題（A3：config schema 單一真實來源）：內建相依元件的 key
    原本是這個函式自己寫死一份 {"vcredist_x64", "dotnet_desktop"}，跟
    installer_core.py 實際用來下載/安裝這兩個相依元件的
    dependency_defs.BUILT_IN_DEPENDENCIES 完全脫鉤——哪天那邊新增/移除
    一個內建相依元件，這裡的驗證邏輯不會自動跟著變。改成動態算出來。"""
    built_in_dependency_keys = set(dependency_defs.BUILT_IN_DEPENDENCIES.keys())
    custom_dependencies = []
    seen_custom_keys = set()
    for entry in custom_dependencies_raw:
        if not isinstance(entry, dict):
            return None, None, "欄位驗證失敗：<br>custom_dependencies 裡每一筆都必須是物件（字典）。"
        key = str(entry.get("key", "")).strip()
        display_name = str(entry.get("display_name", "")).strip()
        download_url = str(entry.get("download_url", "")).strip()
        registry_check = entry.get("registry_check", {}) or {}
        if not key or not display_name or not download_url or not registry_check.get("path"):
            return None, None, "欄位驗證失敗：<br>custom_dependencies 裡每一筆都必須填 key、display_name、download_url、registry_check.path。"
        # 真實抓到的安全性問題：download_url 原本沒有限制協定，http:// 的
        # 相依元件會被安裝端下載後直接執行——中間人可以竄改成任意惡意
        # 程式，這支安裝程式預設是 --uac-admin 編譯的，等於是遠端程式碼
        # 執行。打包階段就擋掉，不要等使用者的機器上才出事。
        if not download_url.lower().startswith("https://"):
            return None, None, f"欄位驗證失敗：<br>custom_dependencies 的「{key}」download_url 必須是 https:// 開頭，不接受未加密的下載連結。"
        if key in built_in_dependency_keys:
            return None, None, f"欄位驗證失敗：<br>custom_dependencies 的 key「{key}」跟內建的相依元件撞名，請改用其他名稱。"
        if key in seen_custom_keys:
            return None, None, f"欄位驗證失敗：<br>custom_dependencies 的 key「{key}」重複了。"
        seen_custom_keys.add(key)

        # sha256（選填）：下載完成後、執行前用來驗證檔案完整性/沒被竄改，
        # 見 installer_core.install_dependency()。格式必須是 64 位十六進位
        # 字元（SHA-256 摘要長度），統一正規化成小寫，比對時不用擔心大小寫。
        sha256_raw = entry.get("sha256")
        sha256 = None
        if sha256_raw:
            sha256_candidate = str(sha256_raw).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", sha256_candidate):
                return None, None, f"欄位驗證失敗：<br>custom_dependencies 的「{key}」sha256 格式不正確，必須是 64 位十六進位字元（SHA-256 摘要）。"
            sha256 = sha256_candidate

        custom_dependencies.append({
            "key": key,
            "display_name": display_name,
            "download_url": download_url,
            "silent_args": list(entry.get("silent_args", []) or []),
            "sha256": sha256,
            "registry_check": {
                "hive": registry_check.get("hive", "HKLM"),
                "path": registry_check.get("path", ""),
                "value_name": registry_check.get("value_name"),
                "expected": registry_check.get("expected"),
                # 真實抓到的 bug：這裡原本只挑 hive/path/value_name/expected
                # 四個鍵重建 registry_check，min_version/enum_subkeys 被悄悄
                # 丟掉——installer_core._make_custom_dependency_checker() 明明
                # 支援讀 min_version 改走版本比較，這兩個欄位傳不到那裡就
                # 形同無效，而且比單純無效更糟：使用者填了 min_version 卻
                # 沒填 expected，會退回 exact-match 語意，變成 value==None
                # 恆為 False，這個相依元件會在任何機器上都被誤判成未安裝。
                "min_version": registry_check.get("min_version"),
                "enum_subkeys": bool(registry_check.get("enum_subkeys", False)),
            },
        })

    if isinstance(bundle_dependencies_raw, str):
        bundle_dependencies_raw = bundle_dependencies_raw.replace("，", ",").split(",")
    bundle_dependencies = [str(k).strip() for k in bundle_dependencies_raw if str(k).strip()]
    known_dependency_keys = set(dependencies) | seen_custom_keys | built_in_dependency_keys
    for key in bundle_dependencies:
        if key not in known_dependency_keys or key not in dependencies:
            return None, None, f"欄位驗證失敗：<br>bundle_dependencies 的「{key}」必須同時列在 dependencies 清單裡，才知道要內嵌哪個相依元件。"

    return custom_dependencies, bundle_dependencies, None


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
    # 偵測並結束鎖定安裝檔案的程式：最終決定權還是在使用者手上（互動式
    # 解除安裝一定會先跳警示問過使用者才會真的結束），打包時讓開發者關掉
    # 這個偵測反而只是徒增要理解的設定項，改成不管傳什麼一律內建開啟。
    restart_explorer_on_update = True
    no_admin_install = bool(data.get("no_admin_install", False))
    custom_install_dir = data.get("custom_install_dir", "").strip() if isinstance(data.get("custom_install_dir"), str) else ""
    pre_install_script = data.get("pre_install_script", "").strip() if isinstance(data.get("pre_install_script"), str) else ""
    post_install_script = data.get("post_install_script", "").strip() if isinstance(data.get("post_install_script"), str) else ""
    custom_dependencies_raw = data.get("custom_dependencies", []) or []
    bundle_dependencies_raw = data.get("bundle_dependencies", []) or []
    signing_raw = data.get("signing", {}) or {}
    install_password_env_raw = data.get("install_password_env", "")
    windows_service_raw = data.get("windows_service", {}) or {}
    scheduled_task_raw = data.get("scheduled_task", {}) or {}
    dependencies_min_version_raw = data.get("dependencies_min_version", {}) or {}
    create_restore_point_before_install = bool(data.get("create_restore_point_before_install", False))

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

    for script_field, script_rel in (("pre_install_script", pre_install_script), ("post_install_script", post_install_script)):
        if script_rel and not os.path.exists(os.path.join(app_dir, script_rel)):
            return None, f"欄位驗證失敗：<br>指定的{'安裝前置' if script_field == 'pre_install_script' else '安裝後置'}腳本「{script_rel}」不存在於應用程式資料夾中，請重新選擇。"

    # windows_service/scheduled_task：真實抓到的問題——這兩個新欄位原本
    # 完全沒有驗證，半填的設定（例如只填了名稱、執行檔還沒選）會直接
    # 打包成功，裝到使用者機器上時 installer_core.py 靜默跳過整個建立
    # 動作，不會有任何錯誤或警告；exe_relative_path 打錯字也不會被
    # 攔下來——sc.exe/schtasks.exe 都不會驗證目標路徑存不存在，會註冊
    # 一個永久壞掉的服務/排程工作。有填其中一個欄位（代表使用者是真的
    # 想用這個功能，不是完全沒填的情境）就要求兩者都齊全、且執行檔真的
    # 存在於 app_dir。
    if windows_service_raw.get("service_name") or windows_service_raw.get("exe_relative_path"):
        service_name = str(windows_service_raw.get("service_name", "")).strip()
        exe_rel = str(windows_service_raw.get("exe_relative_path", "")).strip()
        if not service_name or not exe_rel:
            return None, "欄位驗證失敗：<br>windows_service 的 service_name 跟 exe_relative_path 必須同時填寫，或都留空不使用這個功能。"
        if not os.path.exists(os.path.join(app_dir, exe_rel)):
            return None, f"欄位驗證失敗：<br>windows_service 指定的執行檔「{exe_rel}」不存在於應用程式資料夾中，請重新選擇。"
        start_type = str(windows_service_raw.get("start_type", "auto")).strip()
        # A3：合法值從 windows_service.VALID_START_TYPES 讀，不是這裡自己
        # 另外寫死一份——windows_service.py 才是真正知道 sc.exe 支援哪些
        # start_type 值的模組。
        if start_type not in windows_service.VALID_START_TYPES:
            valid_list = "/".join(sorted(windows_service.VALID_START_TYPES))
            return None, f"欄位驗證失敗：<br>windows_service 的 start_type「{start_type}」不是有效值，必須是 {valid_list} 其中之一。"

    if scheduled_task_raw.get("task_name") or scheduled_task_raw.get("exe_relative_path"):
        task_name = str(scheduled_task_raw.get("task_name", "")).strip()
        exe_rel = str(scheduled_task_raw.get("exe_relative_path", "")).strip()
        if not task_name or not exe_rel:
            return None, "欄位驗證失敗：<br>scheduled_task 的 task_name 跟 exe_relative_path 必須同時填寫，或都留空不使用這個功能。"
        if not os.path.exists(os.path.join(app_dir, exe_rel)):
            return None, f"欄位驗證失敗：<br>scheduled_task 指定的執行檔「{exe_rel}」不存在於應用程式資料夾中，請重新選擇。"

    # dependencies_min_version：真實抓到的問題——key 完全沒有跟 dependencies
    # 清單交叉比對過。installer_core._build_dependency_checkers() 只有對
    # 內建的 vcredist_x64/dotnet_desktop 兩個 key 套用這裡設定的最低版本
    # （custom_dependencies 走各自 registry_check.min_version 這個獨立
    # 欄位，見 F6），填了沒啟用的 key、或填了 custom_dependencies 的 key，
    # 都會被靜默忽略，使用者以為設定生效了、其實完全沒有。
    for dep_key in dependencies_min_version_raw:
        if dep_key not in dependencies:
            return None, f"欄位驗證失敗：<br>dependencies_min_version 的「{dep_key}」沒有在 dependencies 清單裡啟用，這個最低版本設定不會生效。"
        if dep_key not in dependency_defs.BUILT_IN_DEPENDENCIES:
            return None, f"欄位驗證失敗：<br>dependencies_min_version 只支援內建相依元件（vcredist_x64/dotnet_desktop）；自訂相依元件「{dep_key}」的最低版本請改用 custom_dependencies 裡對應項目的 registry_check.min_version。"

    # custom_dependencies/bundle_dependencies 只跟彼此有關，驗證規則收在
    # _validate_dependency_policy()（見上方），這裡不用知道細節。
    custom_dependencies, bundle_dependencies, error = _validate_dependency_policy(
        dependencies, custom_dependencies_raw, bundle_dependencies_raw
    )
    if error:
        return None, error

    # signing 只跟自己有關，驗證規則收在 _validate_signing_config()（見上方）。
    signing, error = _validate_signing_config(signing_raw)
    if error:
        return None, error

    # install_password_env 只跟自己有關，驗證規則收在
    # _validate_install_password_env()（見上方）。
    install_password_env, error = _validate_install_password_env(install_password_env_raw)
    if error:
        return None, error

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
    pack_data["no_admin_install"] = no_admin_install
    pack_data["custom_install_dir"] = custom_install_dir
    pack_data["pre_install_script"] = pre_install_script
    pack_data["post_install_script"] = post_install_script
    pack_data["custom_dependencies"] = custom_dependencies
    pack_data["bundle_dependencies"] = bundle_dependencies
    pack_data["signing"] = signing
    pack_data["install_password_env"] = install_password_env
    return pack_data, None
