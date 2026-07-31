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
"""

import os
import subprocess
import json
import shutil

CONFIG_FILE_NAME = "installer_config.json"


def build_all(
    app_dir, exe_name, app_name, folder_name, version, publisher, png_path, ico_path,
    main_exe, eula_texts=None, eula_default_lang="", dependencies=None, file_associations=None, doc_icon_path="",
    add_to_path=False, path_target_exe="", restart_explorer_on_update=False, workspace_dir=".", progress_callback=None,
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
    folder_name = folder_name or app_name

    workspace_dir = os.path.abspath(workspace_dir)
    ui_dir = os.path.join(workspace_dir, "ui")
    ui_index = os.path.join(ui_dir, "index.html")

    if not os.path.exists(ui_dir) or not os.path.exists(ui_index):
        raise Exception(
            f"找不到 ui 資料夾或 ui/index.html 基礎資源（預期位置：{ui_dir}）。"
        )

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
        "file_associations": file_associations,
        "doc_icon": "doc_icon.ico" if doc_icon_path else "",
        "add_to_path": bool(add_to_path),
        "path_target_exe": path_target_exe,
        "restart_explorer_on_update": bool(restart_explorer_on_update),
    }
    config_path = os.path.join(workspace_dir, CONFIG_FILE_NAME)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_content, f, ensure_ascii=False, indent=4)

    temp_icon = os.path.join(ui_dir, "app_icon.png")
    shutil.copy(png_path, temp_icon)

    # 步驟 2：編譯反安裝程式
    # 解除安裝助手區段：這裡開始到編譯完成，動畫最高只會自己爬到 35%，
    # 剩下的空間留給後面編譯安裝檔那個實際耗時久很多的階段。
    report(15, "正在編譯解除安裝助手...", cap=35, time_constant=8)
    uninstall_cmd = [
        "pyinstaller", "--onefile", "--uac-admin", "--name=uninstall", "uninstall.py",
    ]
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
        "--uac-admin",
        f"--name={exe_name}",
        "--add-data=ui;ui",
        f"--add-data={app_dir};app_contents",
        f"--add-data={config_path};.",
        f"--add-data={built_uninstall};.",
        f"--icon={ico_path}",
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
    if doc_icon_path:
        # --add-data 不會重新命名檔案，會保留使用者選的原始檔名，
        # 所以跟現有的 PNG 圖示（temp_icon）一樣，先複製一份固定檔名
        # doc_icon.ico 到工作目錄，installer_config.json 的 "doc_icon" 欄位
        # 才能一律查這個固定名字，不用管使用者原本選的檔案叫什麼。
        temp_doc_icon = os.path.join(workspace_dir, "doc_icon.ico")
        shutil.copy(doc_icon_path, temp_doc_icon)
        cmd.append(f"--add-data={temp_doc_icon};.")
    cmd.append("installer_core.py")

    res_installer = subprocess.run(
        cmd, cwd=workspace_dir, creationflags=creationflags,
        capture_output=True, text=True,
    )

    # 步驟 4：清理暫存中間檔案
    report(97, "正在執行臨時快取與殘留檔案清理...", cap=99, time_constant=3)
    if os.path.exists(config_path):
        os.remove(config_path)
    if os.path.exists(temp_icon):
        os.remove(temp_icon)
    temp_doc_icon = os.path.join(workspace_dir, "doc_icon.ico")
    if os.path.exists(temp_doc_icon):
        os.remove(temp_doc_icon)

    if res_installer.returncode != 0:
        tail = ((res_installer.stdout or "") + "\n" + (res_installer.stderr or ""))[-1500:]
        raise Exception(f"主安裝檔編譯打包失敗。\n\n{tail}")

    report(100, f"編譯完成，輸出位置：{os.path.join(workspace_dir, 'dist', exe_name + '.exe')}", cap=100, time_constant=1)