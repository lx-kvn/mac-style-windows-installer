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
import cert_subject
import install_engine
import msix_settings
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
    "dependency_install.py", "version_compare.py", "upgrade.py",
]

# `ui/` 底下「使用者可能自己換掉」的靜態資源。ensure_workspace_files() 只有
# 這幾個是「缺少時才補、絕不覆蓋」，**其餘一律視為介面實作、無條件覆蓋**。
#
# 這個方向是反過來的，而且是有意的。原本的規則是一份寫死的覆蓋白名單
# （`name in ("index.html", "uninstall.html")`），新增任何介面實作檔案
# （例如把共用的前端邏輯抽成 ui/*.js）都會落進「只在缺少時才補」那一邊——
# 重複使用同一個工作目錄的人，卡在那裡的舊版永遠不會被換掉，不管重新編譯
# 幾次都一樣，而且沒有任何錯誤訊息。那正是這個函式說明文字裡以「【重要】」
# 標記、已經修過一次的缺陷，換一道門重新出現。
#
# 白名單反轉之後，新增介面實作檔案不需要記得更新任何清單；真正需要維護的
# 只剩「哪些是使用者的東西」，而那是一份本來就該被明確宣告、也很少變動的
# 清單。
USER_CUSTOMIZABLE_UI_ASSETS = frozenset({
    "folder_icon.png",   # 安裝畫面右側的安裝目的地圖示
    "trash_body.svg",    # 解除安裝畫面的垃圾桶（桶身）
    "trash_lid.svg",     # 解除安裝畫面的垃圾桶（桶蓋，會開闔）
})

# 打包當下才產生、不存在於版本庫的 `ui/` 資源。`app_icon.png` 是
# builder.build_all() 把開發者選的那張 PNG 複製過去的（見 builder.py 的
# `temp_icon`），每次打包都會重新產生，而且內容取決於這次打包的設定。
#
# 它們不是「使用者可自訂的靜態資源」（那是指工作目錄裡被換掉、要保留的
# 東西），也不是介面實作。獨立列出來是為了讓「ui/ 底下每個檔案都有明確
# 歸屬」這件事成立——三份 HTML 都引用 app_icon.png，檢查引用完整性的測試
# 必須知道它是預期在版本庫裡缺席的，否則會永遠紅燈。
BUILD_GENERATED_UI_ASSETS = frozenset({
    "app_icon.png",      # 安裝/解除安裝畫面左側的應用程式圖示
})


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
                if name in USER_CUSTOMIZABLE_UI_ASSETS:
                    # 使用者可能自己換過的靜態資源，只在缺少時才補上，
                    # 不要覆蓋使用者的客製化。
                    if not os.path.exists(dest):
                        shutil.copy2(src, dest)
                else:
                    # 其餘一律視為介面實作，跟 installer_core.py/uninstall.py
                    # 一樣無條件覆蓋，隨時跟目前這顆 exe 內嵌的版本保持同步。
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


def _encryption_backend_available():
    """加密實作（install_encryption.py）需要的 `cryptography` 套件在不在。

    那個 import 位於函式內部而非檔案頂端（見 install_encryption.py 的說明：
    這個模組會被兩個 entry point 匯入，其中一個不一定需要加密功能），所以
    缺少時不會在匯入階段就發現，而是要跑到真正加密那一步才爆。獨立成一個
    函式是為了讓測試可以直接換掉它，不需要真的把套件解除安裝。
    """
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:
        return False


def _validate_install_password(need_install_password, install_password_env_raw,
                               has_inline_password, has_plaintext_field):
    """驗證安裝密碼保護的設定，回傳
    (install_password_env_or_empty_string, error_or_None)。

    兩種填法（見 docs/adr/0004）：配置精靈可以直接輸入密碼，也可以填環境
    變數名稱；設定檔（CLI）只支援後者。密碼本身不會走到這個函式——
    `has_inline_password` 只是一個布林值，知道「這次有沒有用直接輸入」就
    足以做所有驗證，這個純函式因此維持「只處理設定值」的性質。

    `has_plaintext_field`：`data` 裡有沒有出現 `install_password` 這個 key。
    有的話明白報錯而不是默默忽略——這個專案已經修過好幾次「使用者以為設定
    生效了、其實被默默忽略」的缺陷，而這一項被忽略的後果特別嚴重：使用者
    以為自己的安裝檔有密碼保護，實際上完全沒有，還要等到把安裝檔發出去
    才可能發現。
    """
    if has_plaintext_field:
        return None, (
            "欄位驗證失敗：<br>設定檔不支援直接寫入安裝密碼（`install_password`）。"
            "<br>設定檔是一份會被存進專案、傳給別人的普通文字檔，密碼寫在裡面等於"
            "整個保護失效。請改用 `install_password_env` 填入存放密碼的環境變數名稱；"
            "想直接輸入密碼請改用配置精靈（GUI）。"
        )

    install_password_env = str(install_password_env_raw or "").strip()

    if has_inline_password and install_password_env:
        return None, (
            "欄位驗證失敗：<br>安裝密碼只能擇一指定：直接輸入密碼，或填入存放密碼的"
            "環境變數名稱，不能兩種同時給。"
        )

    if need_install_password and not has_inline_password and not install_password_env:
        return None, (
            "欄位驗證失敗：<br>已勾選「啟用安裝密碼保護」，請輸入密碼、或填入存放密碼的"
            "環境變數名稱，或取消勾選。"
        )

    if install_password_env and not os.environ.get(install_password_env):
        return None, f"欄位驗證失敗：<br>環境變數「{install_password_env}」目前沒有值，請先設定好安裝密碼再打包。"

    if (has_inline_password or install_password_env) and not _encryption_backend_available():
        return None, (
            "欄位驗證失敗：<br>安裝密碼保護需要 `cryptography` 套件，目前找不到它。"
            "<br>請先執行 <code>pip install cryptography</code> 再打包，或取消「啟用安裝密碼保護」。"
        )

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


def _validate_version_string(version):
    """驗證版本號格式，通過回傳 None，不通過回傳錯誤訊息字串。

    F10：這個檢查原本不在這裡——`validate_and_build_pack_data()` 對版本號
    只確認是非空字串，真正的格式檢查發生在 `builder.py` 中段呼叫
    `version_info.write_version_file()` 的時候，此時 `dist/`／`build/` 已於
    流程開頭被清空。這個純函式的設計目的正是在產生任何副作用之前攔截設定
    錯誤，版本號格式沒有走這條路徑。

    格式：`<主>.<次>.<修>[-<後綴>]`。數字段 1 至 4 段（Win32 VERSIONINFO 的
    filevers/prodvers 依規格固定是 4 個 16 位元整數，第 5 段無處可放），
    每段皆為非負整數；後綴為連字號之後的任意非空文字，不強制符合 semantic
    versioning 的完整規範——`version_compare.py` 既有的預發布判定以「有無
    連字號」為準，維持同一套慣例。決定與理由見
    docs/adr/0003-allow-prerelease-suffix-in-version-string.md。

    數字段用 `isascii() and isdigit()` 判斷，不只用 `isdigit()`：後者對全形
    數字與上標字元也回傳 True，但那些字元不見得能被 `int()` 接受，會讓一個
    「通過驗證」的值在後面才炸開。
    """
    numeric_part, hyphen, suffix = version.partition("-")
    if hyphen and not suffix.strip():
        return f"欄位驗證失敗：<br>版本號「{version}」的連字號後面是空的，預發布後綴不能留空（例如 1.0.0-rc1）。"

    segments = numeric_part.split(".")
    if len(segments) > 4:
        return (
            f"欄位驗證失敗：<br>版本號「{version}」的數字段超過 4 段。"
            "<br>Windows 的版本資源固定只有 4 個數字欄位，放不下第 5 段。"
        )
    for segment in segments:
        if not (segment.isascii() and segment.isdigit()):
            return (
                f"欄位驗證失敗：<br>版本號「{version}」格式不正確。"
                "<br>格式為 1 到 4 段非負整數，可選擇在後面加上連字號與預發布後綴，"
                "例如 1.0.0、1.2.3.4、1.0.0-rc1。"
            )
    return None


def _read_signing_cert_subject(signing, reader=None):
    """簽章憑證讀得到的話，回傳它的發行者字串，否則回傳 None。

    `reader` 是測試接縫（比照 `file_assoc.py` 的 registry 參數），預設是
    `cert_subject.read_from_pfx`。

    讀不到就當作「憑證不在本機」處理，不中止流程：那正是雲端代簽的正常
    情形，而使用者仍然可以自己填 `msix.certificate_subject`。憑證本身有
    問題（密碼不對之類）會在後面真的要簽章時失敗，那裡的訊息比這裡精確。
    """
    if not signing:
        return None
    reader = reader or cert_subject.read_from_pfx
    password = os.environ.get(signing.get("cert_password_env", ""), "")
    try:
        return reader(signing.get("cert_path", ""), password)
    except Exception:
        return None


def validate_and_build_pack_data(data, app_dir, png_path, ico_path, doc_icon_path_selected,
                                 has_inline_password=False, read_cert_subject=None):
    """驗證表單/JSON 資料，並組出要交給 builder.build_all() 的 pack_data。

    純函式：不碰執行緒、不呼叫 check_build_environment()/ensure_workspace_files()
    這類有外部副作用的檢查——那些留在呼叫端（GUI 的 start_pack()、CLI 的
    pack 子指令）裡，跟這裡回傳的結果合併。這樣驗證邏輯可以直接單元測試，
    不需要真的啟動背景執行緒或呼叫外部指令。GUI 跟 CLI 共用同一份驗證，
    不會有兩邊規則兜不起來的問題。

    has_inline_password：這次是不是用「配置精靈直接輸入密碼」那條路（見
    docs/adr/0004）。**密碼本身不會傳進來**——`data` 的欄位集合就是設定檔的
    格式，讓密碼變成一個一般欄位等於同時讓設定檔能寫明文密碼；而這裡要做的
    驗證只需要知道「有沒有」，不需要看到值。這個純函式因此維持「只處理
    設定值」的性質。

    回傳 (pack_data, None) 表示驗證通過；(None, error_message) 表示驗證失敗，
    error_message 就是原本要包進 {"status": "error", "message": ...} 的內容。
    """
    # 引擎的解讀放最前面：它決定後面哪些欄位算得上有效，而且值本身
    # 填錯時（打成 msi 之類）沒有必要先把其餘欄位驗完。
    try:
        engine = install_engine.normalize(data)
    except install_engine.UnknownEngine as e:
        return None, f"欄位驗證失敗：<br>{e}"

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
    # need_install_password 是 GUI 那顆「啟用安裝密碼保護」勾選框的狀態，
    # 跟 need_file_assoc/use_custom_doc_icon 是同一種欄位：勾選框決定要不要
    # 套用旁邊的欄位。CLI 沒有勾選框，由 builder_cli.py 依
    # install_password_env 有沒有內容推斷。
    need_install_password = bool(data.get("need_install_password", False))
    # 設定檔不支援直接寫密碼（見 docs/adr/0004），出現這個 key 要明白報錯
    # 而不是默默忽略——所以這裡看的是「有沒有這個 key」，不是它的值。
    has_plaintext_password_field = "install_password" in data
    windows_service_raw = data.get("windows_service", {}) or {}
    scheduled_task_raw = data.get("scheduled_task", {}) or {}
    dependencies_min_version_raw = data.get("dependencies_min_version", {}) or {}
    create_restore_point_before_install = bool(data.get("create_restore_point_before_install", False))

    if not app_name or not version or not publisher or not exe_name:
        return None, "欄位驗證失敗：<br>所有文字欄位（名稱、版本、發行者、安裝檔名）皆為必填項目，請檢查是否有欄位遺漏。"

    version_error = _validate_version_string(version)
    if version_error:
        return None, version_error

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

    # 檔案關聯圖示的格式依引擎而不同（見 docs/adr/0010）：傳統引擎寫的是
    # 登錄表的 DefaultIcon，吃的就是 ICO；MSIX 的 uap:Logo 不吃 ICO，而吃
    # ICO 的 desktop7:Logo 需要 Windows 10 build 19645，遠高於本工具的最低
    # 版本，本專案又沒有影像處理能力可轉檔。
    doc_icon_extension = ".png" if engine == install_engine.MSIX else ".ico"
    doc_icon_label = "PNG" if engine == install_engine.MSIX else "ICO"
    msix_icon_reason = (
        "（MSIX 模式的檔案關聯圖示只能是 PNG：套件清單能用的宣告不接受 ICO，"
        "而接受 ICO 的那個宣告需要的 Windows 版本遠高於本工具宣告的最低版本。"
        "請把同一張圖另存一份 PNG。）"
    ) if engine == install_engine.MSIX else ""

    doc_icon_path = ""
    if use_custom_doc_icon:
        if not doc_icon_path_selected or not doc_icon_path_selected.lower().endswith(doc_icon_extension):
            return None, (
                f"欄位驗證失敗：<br>已勾選自訂文件圖示，請選擇一顆 {doc_icon_label} 檔案，"
                f"或取消勾選改沿用應用程式圖示。{msix_icon_reason}"
            )
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
        if not icon_path.lower().endswith(doc_icon_extension):
            return None, (
                f"欄位驗證失敗：<br>副檔名「{ext}」指定的專屬圖示不是有效的 {doc_icon_label} 檔案，"
                f"請重新選擇。{msix_icon_reason}"
            )
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
    # F11：驗證讀的是 .strip() 過的值，但 pack_data 是 dict(data) 整包複製
    # 而來，這幾個欄位原本沒有像其他欄位那樣把正規化後的值寫回——start_type
    # 填成 "auto " 能通過驗證，實際傳給 sc.exe 的卻是那個帶空白的原始值，
    # 註冊必定失敗。驗證看的值跟實際使用的值必須是同一個。
    windows_service_normalized = {}
    scheduled_task_normalized = {}

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
        windows_service_normalized = {
            "service_name": service_name,
            "exe_relative_path": exe_rel,
            "display_name": str(windows_service_raw.get("display_name", "")).strip(),
            "start_type": start_type,
        }

    if scheduled_task_raw.get("task_name") or scheduled_task_raw.get("exe_relative_path"):
        task_name = str(scheduled_task_raw.get("task_name", "")).strip()
        exe_rel = str(scheduled_task_raw.get("exe_relative_path", "")).strip()
        if not task_name or not exe_rel:
            return None, "欄位驗證失敗：<br>scheduled_task 的 task_name 跟 exe_relative_path 必須同時填寫，或都留空不使用這個功能。"
        if not os.path.exists(os.path.join(app_dir, exe_rel)):
            return None, f"欄位驗證失敗：<br>scheduled_task 指定的執行檔「{exe_rel}」不存在於應用程式資料夾中，請重新選擇。"
        scheduled_task_normalized = {
            "task_name": task_name,
            "exe_relative_path": exe_rel,
            # 預設值跟 installer_core.py 讀取時的 .get("trigger", "onlogon")
            # 一致，明確寫進設定檔而不是留給讀取端補。
            "trigger": str(scheduled_task_raw.get("trigger", "onlogon")).strip() or "onlogon",
        }

    # F09：「免管理員權限安裝」開啟時 builder.py 不加入提權設定，整個安裝
    # 流程在一般權限下執行；但 sc.exe create 與系統還原點建立都需要管理員
    # 權限，這兩個組合在終端使用者機器上必定失敗。失敗只會變成安裝完成
    # 畫面上的警告，使用者要等到裝上去、發現服務不存在才知道這個設定從
    # 一開始就不可能成立——這種矛盾應該在打包階段就攔下來。
    #
    # 排程工作不在互斥清單裡：schtasks.exe 以目前使用者身分建立 onlogon
    # 觸發的工作不需要管理員權限。
    if no_admin_install and windows_service_normalized:
        return None, (
            "欄位驗證失敗：<br>「免管理員權限安裝」與「建立 Windows 服務」不能同時使用。"
            "<br>建立 Windows 服務（sc.exe）需要系統管理員權限，但免權限安裝的整個流程都在"
            "一般權限下執行，服務必定建立失敗。請擇一取消。"
        )
    if no_admin_install and create_restore_point_before_install:
        return None, (
            "欄位驗證失敗：<br>「免管理員權限安裝」與「安裝前建立系統還原點」不能同時使用。"
            "<br>建立系統還原點需要系統管理員權限，但免權限安裝的整個流程都在一般權限下執行，"
            "還原點必定建立失敗。請擇一取消。"
        )

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

    # 安裝密碼保護的驗證規則收在 _validate_install_password()（見上方）。
    # 密碼本身不會走到這裡：has_inline_password 只是一個布林值，見
    # docs/adr/0004。
    install_password_env, error = _validate_install_password(
        need_install_password, install_password_env_raw, has_inline_password,
        has_plaintext_password_field,
    )
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
    pack_data["windows_service"] = windows_service_normalized
    pack_data["scheduled_task"] = scheduled_task_normalized
    pack_data["install_engine"] = engine

    # 引擎相容性檢查放在最後，理由是它回報的是「這份設定與這個引擎不相容」，
    # 而使用者要判斷的是「切換引擎划不划算」——那個判斷需要一份先通過了
    # 一般欄位驗證的設定，否則列出來的清單裡會混著「值填錯了」這種跟
    # 引擎無關的項目。
    #
    # 相容性結果先於「引擎尚未實作」回報：下游專案要先知道自己的設定
    # 能不能用，才決定要不要等這個引擎（見 docs/adr/0009）。
    report = install_engine.check_settings(engine, data)
    if report.has_blocking:
        return None, report.error_message()
    if engine == install_engine.MSIX:
        # 相容性通過之後才檢查 msix 區塊的必填欄位。順序是刻意的：先回答
        # 「這個引擎適不適合你」，再要求「把必填欄位補齊」——對方可能看完
        # 相容性結果就決定不用這個引擎，此時要他補欄位是白費工。
        msix_normalized, msix_error = msix_settings.validate(
            data.get("msix"),
            cert_subject=_read_signing_cert_subject(signing, read_cert_subject),
        )
        problems = [msix_error] if msix_error else []
        package_version = ""
        try:
            package_version = msix_settings.to_quad_version(version)
        except msix_settings.InvalidVersion as e:
            problems.append(str(e))
        if problems:
            return None, "欄位驗證失敗：<br>" + "<br>".join(problems)
        # 「引擎尚未實作」這道攔截不在這裡：它擋的是 bootstrapper（內嵌
        # .msix 並交給系統部署的那顆 exe），而產出 .msix 本身已經做得到。
        # 兩者由不同的指令觸發，因此該攔截屬於 builder_cli 的 pack 指令，
        # 不屬於驗證。驗證的職責是「這份設定能不能用」，不是「工具做到哪
        # 一步了」。
        pack_data["msix"] = dict(msix_normalized, package_version=package_version)
    # report.notices（第四類，不擋建置、只需說明的項目）目前沒有接收端：
    # 它只在 MSIX 引擎下產生，而 MSIX 引擎在上一行就中止了。MSIX 引擎實作
    # 完成、上面那個中止拿掉之後，這裡把 notices 交給 build_all() 的
    # progress_callback 印進建置紀錄。先放一個永遠是空清單的欄位進 pack_data
    # 只會讓人以為它已經在運作。
    return pack_data, None
