"""
builder.py
----------
安裝檔生成流水線的核心模組。

流程：
  1. 產生 installer_config.json（app 資訊 + EULA + 主執行檔 + 相依元件 + 檔案關聯 + PATH 設定）
  2. 複製使用者選擇的 PNG 圖示到 ui 資料夾
  3. 編譯 uninstall.exe
  4. 編譯主安裝檔（把 ui、app_contents、installer_config.json、uninstall.exe 全部包進同一個 exe）
  5. 清理暫存檔案

修正/新增紀錄：
  - 原本 builder.py 寫的是 metadata.json，但 installer_core.py 讀的是 installer_config.json，
    兩個檔名對不起來，等於安裝端那段讀取邏輯從沒真正生效過。這次統一成
    installer_config.json，兩邊一致。
  - 新增欄位：eula_texts（多語言 EULA，語言代碼對應文字的字典）、
    eula_default_lang、main_exe、dependencies、file_associations、add_to_path，
    對應「近期目標」清單裡要在製作工具端設定的項目。
  - 沿用上一輪的修正：build 前清空 dist/build、不使用 shell=True。
  - 新增 workspace_dir 參數：build_all() 需要 ui/index.html、installer_core.py、
    uninstall.py 實際存在於磁碟上（因為要另外呼叫 pyinstaller 子行程編譯它們），
    原本這些路徑都是寫死的相對路徑（相對於「執行時的工作目錄」），gui_config.py
    被打包成 exe 之後，工作目錄底下不一定有這些檔案，會直接找不到資源而失敗。
    現在改成所有路徑都錨定在呼叫端傳入的 workspace_dir，由呼叫端負責確保
    這個目錄底下有齊全的必要檔案（見 gui_config.py 的 _ensure_workspace_files()）。
    workspace_dir 預設為 "."，維持 .py 直接執行時的原有行為不變。
  - 新增欄位：local_appdata_files（相對於 app_dir 的檔案清單，指定這些檔案
    改裝到 %LOCALAPPDATA%\\Programs\\<folder_name> 而不是主安裝目錄，典型
    用途是讓 CLI 工具不需要系統管理員權限就能執行；實際的落地/回滾/解除
    安裝邏輯都在 installer_core.py/uninstall.py，這裡只是把清單原封不動
    寫進 installer_config.json）。
  - 新增欄位：doc_icons（字典 {副檔名: 圖示來源路徑}，讓不同副檔名的檔案
    關聯可以各自套用不同 ICO，例如 .a 跟 .b 用不同圖示，不是全部共用同一張
    doc_icon）。每個副檔名各自複製一份固定命名的圖示（doc_icon_<副檔名>.ico）
    內嵌進安裝檔，寫進 installer_config.json 的 "doc_icons" 是
    {副檔名: 內嵌檔名} 這個轉換後的對照表；installer_core.py 的
    _resolve_doc_icon_refs() 負責決定每個副檔名實際要用哪張圖示（優先順序：
    這個副檔名自己的 doc_icons 設定 -> 共用的 doc_icon -> 主程式圖示）。
"""

import os
import subprocess
import json
import shutil
import urllib.request
from datetime import datetime

import dependency_defs
import version_info
import install_encryption

CONFIG_FILE_NAME = "installer_config.json"


def _download_file(url, dest_path, timeout=60):
    """下載一個檔案到指定路徑，供 bundle_dependencies（打包時嵌入相依元件
    安裝檔）共用。install_dependency()（installer_core.py）安裝時的線上
    下載也是同一段邏輯的另一個副本——這裡刻意不強行合併成同一份程式碼：
    一個在打包工具端（同步、失敗直接中止整個 pack）、一個在安裝端
    （需要推播進度給前端），兩邊的呼叫情境跟錯誤處理方式不同，硬併會讓
    兩邊都要遷就對方不需要的參數。
    """
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)


def _sign_executable(exe_path, signing):
    """用 signtool 幫編譯出來的 exe 簽數位簽章（見 signing 設定欄位）。

    這裡不負責生出憑證——那要跟憑證機構購買或用公司行號申請，這個函式做的
    只是「把簽章步驟接進打包流程」：憑證路徑/密碼來源都齊全時自動簽，找不到
    signtool 或簽章失敗一律讓整個 pack 流程失敗（既然使用者特地設定了簽章，
    不該悄悄放行一份沒簽到的檔案）。

    密碼透過 cert_password_env 指定的環境變數名稱讀取，不放在設定檔明文裡；
    packaging_core.py 的 validate_and_build_pack_data() 已經確認過打包當下
    這個環境變數確實有值，這裡直接讀取即可。
    """
    signtool = shutil.which("signtool")
    if not signtool:
        raise Exception(
            "找不到 signtool（需要安裝 Windows SDK 或 Visual Studio，並確認它在 PATH 裡），無法簽署數位簽章。"
        )
    password = os.environ.get(signing["cert_password_env"], "")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            signtool, "sign",
            "/f", signing["cert_path"],
            "/p", password,
            "/fd", "sha256",
            "/tr", signing["timestamp_url"],
            "/td", "sha256",
            exe_path,
        ],
        creationflags=creationflags, capture_output=True, text=True,
    )
    if result.returncode != 0:
        # 錯誤訊息不印密碼（signtool 本身的輸出也不會回顯密碼，這裡只是不
        # 額外把 password 變數帶進錯誤訊息，避免哪天有人手滑加進去）。
        tail = ((result.stdout or "") + "\n" + (result.stderr or ""))[-1000:]
        raise Exception(f"簽署 {os.path.basename(exe_path)} 失敗：\n{tail}")


def build_all(
    app_dir, exe_name, app_name, folder_name, version, publisher, png_path, ico_path,
    main_exe, eula_texts=None, eula_default_lang="", dependencies=None, file_associations=None, doc_icon_path="",
    doc_icons=None, add_to_path=False, path_target_exe="", local_appdata_files=None,
    restart_explorer_on_update=False, no_admin_install=False, pre_install_script="", post_install_script="",
    custom_dependencies=None, bundle_dependencies=None, signing=None, custom_install_dir="",
    windows_service=None, scheduled_task=None, dependencies_min_version=None,
    create_restore_point_before_install=False, install_password_env="",
    workspace_dir=".", progress_callback=None,
):
    """流水線：產生配置 -> 編譯反安裝檔 -> 編譯主安裝檔

    workspace_dir：ui/、installer_core.py、uninstall.py 必須實際存在的工作目錄，
    也是 dist/、build/、installer_config.json 等中間產物的落地位置。

    exe_name / app_name / folder_name 是三個不同的東西：
      - exe_name：安裝檔本身的檔名（例如 Setup_MyApp），只在編譯這個步驟用到，
        不會寫進 installer_config.json，安裝端、解除安裝端都不需要知道這個名字。
      - app_name：顯示給使用者看的應用程式名稱，可以是中文，會寫進
        installer_config.json、登錄表 DisplayName、安裝介面上的文字。
      - folder_name：只用來組安裝路徑（C:\\Program Files\\<folder_name>），
        建議英數字，避免路徑含中文在少數環境下出問題。留空則沿用 app_name。

    修正紀錄：原本這裡完全沒有 app_name 這個參數，installer_config.json 裡的
    "app_name" 欄位其實是拿 exe_name 冒充，使用者在製作工具填的「應用程式名稱」
    欄位從未真正流到安裝端。這裡補上正確的三欄分工。
    """

    def report(percent, message, cap=99, time_constant=15):
        """回報進度。

        cap：這個階段動畫最高可以自己爬到多少（前端在還沒收到下一個真實 checkpoint
            前，會用這個上限 + time_constant 算一條漸進趨近曲線，不會超過 cap）。
        time_constant：趨近 cap 的時間常數（秒），數字越大爬得越慢、越保守。
            抓的原則是「這個階段實際預期要花多久」，不是憑感覺亂調。
        """
        if progress_callback:
            progress_callback(percent, message, cap, time_constant)

    dependencies = dependencies or []
    file_associations = file_associations or []
    doc_icons = doc_icons or {}
    local_appdata_files = local_appdata_files or []
    custom_dependencies = custom_dependencies or []
    bundle_dependencies = bundle_dependencies or []
    windows_service = windows_service or {}
    scheduled_task = scheduled_task or {}
    dependencies_min_version = dependencies_min_version or {}
    folder_name = folder_name or app_name

    workspace_dir = os.path.abspath(workspace_dir)
    ui_dir = os.path.join(workspace_dir, "ui")
    ui_index = os.path.join(ui_dir, "index.html")
    ui_uninstall_html = os.path.join(ui_dir, "uninstall.html")

    if not os.path.exists(ui_dir) or not os.path.exists(ui_index):
        raise Exception(
            f"找不到 ui 資料夾或 ui/index.html 基礎資源（預期位置：{ui_dir}）。"
        )
    if not os.path.exists(ui_uninstall_html):
        raise Exception(f"找不到 ui/uninstall.html 基礎資源（預期位置：{ui_uninstall_html}）。")

    if not os.path.exists(os.path.join(workspace_dir, "installer_core.py")):
        raise Exception(f"找不到 installer_core.py（預期位置：{workspace_dir}）。")
    if not os.path.exists(os.path.join(workspace_dir, "uninstall.py")):
        raise Exception(f"找不到 uninstall.py（預期位置：{workspace_dir}）。")

    # 每次重新編譯前，先清掉舊的 dist/build 產物，避免用到上一輪殘留檔案
    report(5, "正在清理舊的編譯產物...", cap=15, time_constant=2)
    for stale_dir in ("dist", "build"):
        stale_path = os.path.join(workspace_dir, stale_dir)
        if os.path.exists(stale_path):
            shutil.rmtree(stale_path, ignore_errors=True)

    # 步驟 1：動態生成設定配置（安裝端與解除安裝端都會讀這份檔案）
    report(10, "正在配置封裝參數與複製介面圖示...", cap=15, time_constant=2)
    # 每個副檔名各自的專屬圖示（見 doc_icons 參數）用固定命名規則
    # doc_icon_<副檔名去掉點>.ico 內嵌，跟共用的 doc_icon.ico 是分開的檔案，
    # 彼此不會互相覆蓋，installer_core.py 也是靠這個固定檔名去複製/引用。
    doc_icons_embedded = {ext: f"doc_icon_{ext.lstrip('.')}.ico" for ext in doc_icons}
    # pre/post-install 腳本比照 doc_icon 的做法：固定命名內嵌，安裝端只認這個
    # 固定檔名，不需要知道開發者原本選的檔案叫什麼。保留原始副檔名（.bat/.exe/
    # .ps1 等），因為執行方式（是不是要透過 cmd /c 或 powershell）由副檔名
    # 本身的關聯決定，改副檔名會讓它變得跑不起來。
    pre_install_embedded = f"pre_install_script{os.path.splitext(pre_install_script)[1]}" if pre_install_script else ""
    post_install_embedded = f"post_install_script{os.path.splitext(post_install_script)[1]}" if post_install_script else ""
    config_content = {
        "app_name": app_name,
        "display_name": app_name,
        "folder_name": folder_name,
        "version": version,
        "publisher": publisher,
        "main_exe": main_exe,
        "eula_texts": eula_texts or {},
        "eula_default_lang": eula_default_lang,
        "dependencies": dependencies,
        "custom_dependencies": custom_dependencies,
        "bundle_dependencies": bundle_dependencies,
        "file_associations": file_associations,
        "doc_icon": "doc_icon.ico" if doc_icon_path else "",
        "doc_icons": doc_icons_embedded,
        "add_to_path": bool(add_to_path),
        "path_target_exe": path_target_exe,
        "local_appdata_files": local_appdata_files,
        "restart_explorer_on_update": bool(restart_explorer_on_update),
        "no_admin_install": bool(no_admin_install),
        "custom_install_dir": custom_install_dir,
        "windows_service": windows_service,
        "scheduled_task": scheduled_task,
        "dependencies_min_version": dependencies_min_version,
        "create_restore_point_before_install": bool(create_restore_point_before_install),
        "pre_install_script": pre_install_embedded,
        "post_install_script": post_install_embedded,
        "password_protected": bool(install_password_env),
    }
    config_path = os.path.join(workspace_dir, CONFIG_FILE_NAME)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_content, f, ensure_ascii=False, indent=4)

    temp_icon = os.path.join(ui_dir, "app_icon.png")
    shutil.copy(png_path, temp_icon)

    # 讓兩顆輸出的 exe（安裝檔本體、uninstall.exe）都帶上 Win32 VERSIONINFO
    # 資源（見 version_info.py）。ProductName 沿用 app_name（沒有另外的
    # 「產品名稱」欄位），LegalCopyright 用建置當下年份自動組成——版本字串
    # 格式不合法時 write_version_file() 會在這裡直接拋例外，中止整個流程，
    # 不要編到一半才發現版本號寫錯。
    legal_copyright = f"Copyright © {datetime.now().year} {publisher}"
    uninstall_version_file = os.path.join(workspace_dir, "uninstall_version_info.txt")
    version_info.write_version_file(
        uninstall_version_file,
        product_name=app_name, file_version=version,
        file_description=f"Uninstall {app_name}",
        company_name=publisher, legal_copyright=legal_copyright,
    )
    main_version_file = os.path.join(workspace_dir, "main_version_info.txt")
    version_info.write_version_file(
        main_version_file,
        product_name=app_name, file_version=version, file_description=app_name,
        company_name=publisher, legal_copyright=legal_copyright,
    )

    # 步驟 2：編譯反安裝程式
    # 解除安裝助手區段：這裡開始到編譯完成，動畫最高只會自己爬到 35%，
    # 剩下的空間留給後面編譯安裝檔那個實際耗時久很多的階段。
    report(15, "正在編譯解除安裝助手...", cap=35, time_constant=8)
    uninstall_cmd = [
        "pyinstaller",
        "--onefile",
        # --noconsole：uninstall.py 現在也是 pywebview 視窗化程式（見
        # ui/uninstall.html），套用跟主安裝檔同一套 .nice-modal-* 視覺語言，
        # 不再是雙擊會跳出黑底命令提示字元視窗的純 console 程式。
        "--noconsole",
        "--add-data=ui;ui",
        # installer_core.py 排除這幾個模組的理由同樣適用於 uninstall.py：
        # 兩支 exe 現在都 import webview，PyInstaller 保守打包會把用不到的
        # pywebview 替代 GUI 後端一起塞進去，體積差很多。
        "--exclude-module=PyQt5",
        "--exclude-module=PyQt6",
        "--exclude-module=PySide2",
        "--exclude-module=PySide6",
        "--exclude-module=gi",
    ]
    if not no_admin_install:
        # no_admin_install 開啟時，整個 app（含解除安裝）都不要求提權；
        # 兩支 exe 的提權設定要一致，不然單獨對 uninstall.exe 提權會很突兀
        # （使用者剛裝完全程不用管理員權限，解除安裝卻突然跳 UAC）。
        uninstall_cmd.append("--uac-admin")
    uninstall_cmd += [f"--version-file={uninstall_version_file}", "--name=uninstall", "uninstall.py"]
    # CREATE_NO_WINDOW：呼叫端（gui_config.py）是 --noconsole 的 GUI 程式，
    # 沒有這個旗標的話，Windows 會在編譯的當下短暫跳出一個命令提示字元視窗。
    # 同時改成 capture_output，把 PyInstaller 實際輸出的內容留著，
    # 視窗被隱藏之後，失敗時的診斷資訊只能靠這個，不能再讓使用者盯著閃過的視窗自己看。
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res_un = subprocess.run(
        uninstall_cmd, cwd=workspace_dir, creationflags=creationflags,
        capture_output=True, text=True,
    )
    if res_un.returncode != 0:
        tail = ((res_un.stdout or "") + "\n" + (res_un.stderr or ""))[-1500:]
        raise Exception(f"反安裝程式編譯失敗。\n\n{tail}")

    built_uninstall = os.path.join(workspace_dir, "dist", "uninstall.exe")
    if not os.path.exists(built_uninstall):
        raise Exception("找不到產出的反安裝程式檔案。")

    # 解除安裝助手區段結束，交棒給安裝檔區段
    report(35, "解除安裝助手編譯完成，準備編譯安裝檔...", cap=40, time_constant=2)

    # 步驟 3：構建主要安裝檔打包指令
    # 安裝檔區段：這是實際最耗時的階段（常常要數十秒），動畫上限拉到 99%，
    # 時間常數也調得比較大，讓它爬得比較保守、不會太快撞到上限卡住。
    report(40, "正在編譯最終主安裝執行檔，此步驟需要數十秒...", cap=99, time_constant=25)
    cmd = [
        "pyinstaller",
        "--onefile",
        "--noconsole",
        f"--name={exe_name}",
        "--add-data=ui;ui",
        f"--add-data={config_path};.",
        f"--add-data={built_uninstall};.",
        f"--icon={ico_path}",
        f"--version-file={main_version_file}",
        # installer_core.py import webview，同樣會被 PyInstaller 靜態分析保守地
        # 整包塞進 pywebview 支援但 Windows 用不到的替代 GUI 後端，排除掉可以
        # 省下相當可觀的體積——而且這個影響的是每一個實際下載安裝檔的終端使用者，
        # 比 InstallerBuilder.exe 自己的體積更值得優先處理。
        "--exclude-module=PyQt5",
        "--exclude-module=PyQt6",
        "--exclude-module=PySide2",
        "--exclude-module=PySide6",
        "--exclude-module=gi",
    ]
    if not no_admin_install:
        cmd.append("--uac-admin")
    # 真實抓到的問題（F19）：這裡建立的暫存產物（doc_icon.ico、內嵌的
    # 前後置腳本、下載下來要內嵌的相依元件安裝檔）原本只有順利跑到最後
    # 的「清理暫存中間檔案」那段才會被刪除——中途任何一步拋例外（doc
    # icon 檔案不存在、相依元件下載失敗）都會讓已經建立的暫存檔留在
    # workspace_dir 裡。改成用 try/finally：不管這個區塊是正常結束還是
    # 中途拋例外，已經記錄在下面幾個清單裡的暫存檔都會被清掉。
    temp_doc_icon = os.path.join(workspace_dir, "doc_icon.ico") if doc_icon_path else None
    temp_doc_icons = []
    temp_scripts = []
    temp_dependency_files = []
    # 安裝密碼保護（見 CONTEXT.md「安裝密碼保護」一節）：有設定
    # install_password_env 時，app_dir 整包加密成一份檔案再內嵌，
    # 不能像沒設定密碼保護時那樣直接把明文 app_dir 塞進 --add-data，
    # 不然密碼保護形同虛設。這份暫存加密檔跟其他暫存產物一樣，在
    # 下面的 finally 區塊統一清掉。
    temp_encrypted_payload = None
    try:
        if install_password_env:
            password = os.environ.get(install_password_env, "")
            temp_encrypted_payload = os.path.join(workspace_dir, "app_contents.enc")
            install_encryption.encrypt_directory(app_dir, temp_encrypted_payload, password)
            cmd.append(f"--add-data={temp_encrypted_payload};.")
        else:
            cmd.append(f"--add-data={app_dir};app_contents")

        if doc_icon_path:
            # --add-data 不會重新命名檔案，會保留使用者選的原始檔名，
            # 所以跟現有的 PNG 圖示（temp_icon）一樣，先複製一份固定檔名
            # doc_icon.ico 到工作目錄，installer_config.json 的 "doc_icon"
            # 欄位才能一律查這個固定名字，不用管使用者原本選的檔案叫什麼。
            shutil.copy(doc_icon_path, temp_doc_icon)
            cmd.append(f"--add-data={temp_doc_icon};.")
        for ext, src_path in doc_icons.items():
            # 同上，每個副檔名各自複製一份固定命名的圖示，避免不同副檔名
            # 剛好選了同名但內容不同的原始檔案時互相覆蓋。
            temp_path = os.path.join(workspace_dir, doc_icons_embedded[ext])
            shutil.copy(src_path, temp_path)
            temp_doc_icons.append(temp_path)
            cmd.append(f"--add-data={temp_path};.")

        for script_src, embedded_name in (
            (pre_install_script, pre_install_embedded),
            (post_install_script, post_install_embedded),
        ):
            if not script_src:
                continue
            temp_path = os.path.join(workspace_dir, embedded_name)
            shutil.copy(os.path.join(app_dir, script_src), temp_path)
            temp_scripts.append(temp_path)
            cmd.append(f"--add-data={temp_path};.")

        # bundle_dependencies：打包當下把指定的相依元件安裝檔下載下來，
        # 內嵌進 Setup.exe 的 dependencies/ 子目錄（掛載路徑要跟
        # installer_core.py 的 install_dependency() 查找路徑
        # dependencies/<key>.exe 一致）。下載失敗直接中止整個 pack 流程
        # 並回報，不要悄悄產出一份「號稱有內嵌、其實沒裝進去」的安裝檔。
        dependency_url_map = {
            key: meta["download_url"] for key, meta in dependency_defs.BUILT_IN_DEPENDENCIES.items()
        }
        for entry in custom_dependencies:
            dependency_url_map[entry["key"]] = entry["download_url"]

        for key in bundle_dependencies:
            url = dependency_url_map.get(key)
            if not url:
                raise Exception(f"無法內嵌相依元件「{key}」：找不到對應的下載連結。")
            report(38, f"正在下載相依元件 {key} 供內嵌打包...", cap=39, time_constant=5)
            # 檔名必須剛好是「{key}.exe」：--add-data 不會重新命名檔案，
            # 只會把來源檔案原封不動放進目的地資料夾，installer_core.py 的
            # install_dependency() 查找的固定路徑是 dependencies/<key>.exe。
            temp_path = os.path.join(workspace_dir, f"{key}.exe")
            try:
                _download_file(url, temp_path)
            except Exception as e:
                raise Exception(f"下載相依元件「{key}」失敗，無法內嵌：{e}")
            temp_dependency_files.append(temp_path)
            cmd.append(f"--add-data={temp_path};dependencies")

        cmd.append("installer_core.py")

        res_installer = subprocess.run(
            cmd, cwd=workspace_dir, creationflags=creationflags,
            capture_output=True, text=True,
        )
    finally:
        # 步驟 4：清理暫存中間檔案（不管上面是正常結束還是中途拋例外）
        report(97, "正在執行臨時快取與殘留檔案清理...", cap=99, time_constant=3)
        if os.path.exists(config_path):
            os.remove(config_path)
        if os.path.exists(temp_icon):
            os.remove(temp_icon)
        if temp_doc_icon and os.path.exists(temp_doc_icon):
            os.remove(temp_doc_icon)
        for temp_path in temp_doc_icons:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        for temp_path in temp_scripts:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        for temp_path in temp_dependency_files:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        for temp_path in (uninstall_version_file, main_version_file):
            if os.path.exists(temp_path):
                os.remove(temp_path)
        if temp_encrypted_payload and os.path.exists(temp_encrypted_payload):
            os.remove(temp_encrypted_payload)

    if res_installer.returncode != 0:
        tail = ((res_installer.stdout or "") + "\n" + (res_installer.stderr or ""))[-1500:]
        raise Exception(f"主安裝檔編譯打包失敗。\n\n{tail}")

    dist_installer = os.path.join(workspace_dir, "dist", f"{exe_name}.exe")

    if signing:
        # 使用者特地設定了簽章，簽不成不該悄悄放行產出未簽章的檔案——
        # 直接讓整個 pack 流程失敗，而不是打包「成功」但實際上沒簽章。
        report(99, "正在簽署數位簽章...", cap=99, time_constant=2)
        _sign_executable(dist_installer, signing)
        _sign_executable(built_uninstall, signing)

    report(100, f"編譯完成，輸出位置：{dist_installer}", cap=100, time_constant=1)