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

import hashlib
import os
import subprocess
import json
import shutil
import urllib.request
from datetime import datetime

import dependency_defs
import version_info
import install_encryption
import sdk_tools

CONFIG_FILE_NAME = "installer_config.json"


def _file_sha256(path, chunk_size=1024 * 1024):
    """算檔案的 SHA-256 摘要（十六進位小寫字串）。

    安裝端 dependency_install.py 有一份同樣的實作，這裡不改成共用同一份：
    共用會讓打包工具的 import 圖多出一條指向安裝端執行期模組的邊，而那個
    模組是透過 --add-data 內嵌、PyInstaller 的靜態分析看不到的，改成
    import 反而增加 frozen exe 找不到模組的風險（這個專案已經因為模組清單
    沒同步踩過好幾次）。SHA-256 是規格固定的演算法，不像 URL 清單或命名
    慣例那樣有「兩邊悄悄不同步」的空間。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _download_file(url, dest_path, timeout=60, expected_sha256=None):
    """下載一個檔案到指定路徑，供 bundle_dependencies（打包時嵌入相依元件
    安裝檔）共用。install_dependency()（installer_core.py）安裝時的線上
    下載也是同一段邏輯的另一個副本——這裡不強行合併成同一份程式碼，因為
    一個在打包工具端（同步、失敗直接中止整個 pack）、一個在安裝端
    （需要推播進度給前端），兩邊的呼叫情境跟錯誤處理方式不同，硬併會讓
    兩邊都要遷就對方不需要的參數。

    F06：兩邊「不合併」成立，但兩邊的驗證強度不該因此產生落差。這裡原本
    既沒有 Content-Length 完整性比對、也沒有 sha256 驗證，而內嵌迴圈呼叫
    時連使用者填的 `sha256` 都沒有傳進來——使用者同時填 `sha256` 又勾選
    內嵌時，該檔案從打包到安裝沒有任何一個環節驗證過。打包當下網路中斷
    （read() 只會回傳空字串正常結束迴圈，不拋例外）會把一顆內容截斷的
    執行檔內嵌進 Setup.exe，之後每一位終端使用者都會執行它。

    驗證失敗時刪掉已寫入的部分檔案再往外拋：留著等於下一步的 --add-data
    仍然可能把一顆沒通過驗證的檔案內嵌進去。
    """
    downloaded = 0
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            total = resp.getheader("Content-Length")
            total = int(total) if total else None
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

        if total is not None and downloaded != total:
            raise Exception(f"下載不完整（預期 {total} bytes，實際收到 {downloaded} bytes）。")

        if expected_sha256:
            actual = _file_sha256(dest_path)
            if actual != str(expected_sha256).strip().lower():
                raise Exception(
                    f"完整性驗證失敗（sha256 不符）：預期 {expected_sha256}，實際 {actual}。"
                )
    except Exception:
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        raise


def _sign_file(target_path, signing, find_tool=None, run=None, log=None):
    """用 signtool 幫產出的檔案簽數位簽章（見 signing 設定欄位）。

    簽的對象有兩種：傳統引擎的 exe，以及 MSIX 引擎的 `.msix`。signtool 對
    兩者的用法完全相同，因此不分成兩個函式——名稱不用 `_sign_executable`
    是因為那個名字會讓人以為 `.msix` 需要另一條路徑，進而寫出第二份重複的
    實作。

    這裡不負責生出憑證——那要跟憑證機構購買或用公司行號申請，這個函式做的
    只是「把簽章步驟接進打包流程」：憑證路徑/密碼來源都齊全時自動簽，找不到
    signtool 或簽章失敗一律讓整個 pack 流程失敗（既然使用者特地設定了簽章，
    不該悄悄放行一份沒簽到的檔案）。

    密碼透過 cert_password_env 指定的環境變數名稱讀取，不放在設定檔明文裡；
    packaging_core.py 的 validate_and_build_pack_data() 已經確認過打包當下
    這個環境變數確實有值，這裡直接讀取即可。

    signtool 的檢索改走 sdk_tools.find_tool()，與 MSIX 模式的 makeappx
    共用同一套來源優先序（見 docs/adr/0008 決定四）。原本的實作只檢索
    PATH，而 Windows SDK 安裝後不會把這些工具加進 PATH——對已正確安裝
    SDK 的使用者，該實作會回報找不到，並要求他們去做一件不會成功的事。

    find_tool／run 是測試接縫（比照 file_assoc.py 的 registry 參數），
    預設分別是 sdk_tools.find_tool 與 subprocess.run。log 收到本次實際
    採用的工具來源與版本，三個來源並存時，這行是診斷「兩台機器打包結果
    不同」的唯一依據（docs/adr/0008 決定五末段）。
    """
    find_tool = find_tool or sdk_tools.find_tool
    run = run or subprocess.run
    located = find_tool("signtool.exe")
    if log:
        log(located.describe())
    password = os.environ.get(signing["cert_password_env"], "")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = run(
        [
            located.path, "sign",
            "/f", signing["cert_path"],
            "/p", password,
            "/fd", "sha256",
            "/tr", signing["timestamp_url"],
            "/td", "sha256",
            target_path,
        ],
        creationflags=creationflags, capture_output=True, text=True,
    )
    if result.returncode != 0:
        # 錯誤訊息不印密碼（signtool 本身的輸出也不會回顯密碼，這裡只是不
        # 額外把 password 變數帶進錯誤訊息，避免哪天有人手滑加進去）。
        tail = ((result.stdout or "") + "\n" + (result.stderr or ""))[-1000:]
        raise Exception(f"簽署 {os.path.basename(target_path)} 失敗：\n{tail}")


MSIX_STAGING_DIRNAME = "msix_staging"


def missing_workspace_resources(workspace_dir, is_msix=False):
    """工作目錄少了哪個基礎資源，回傳說明字串；齊全時回傳 None。

    這個檢查很廉價（幾次 os.path.exists），因此值得在花力氣之前先做。真實
    踩到的順序問題：MSIX 一體式流程下，makeappx 打包與 signtool 簽章（含一次
    連到時間戳記伺服器的往返）都已經跑完，才由 build_all 開頭的這段檢查中止。
    抽成函式讓呼叫端能在動手之前先問一次，而 build_all 自己仍然會再問——它
    不能假設呼叫端問過了。
    """
    workspace_dir = os.path.abspath(workspace_dir)
    ui_dir = os.path.join(workspace_dir, "ui")
    ui_index = os.path.join(ui_dir, "index.html")
    ui_uninstall_html = os.path.join(ui_dir, "uninstall.html")

    if not os.path.exists(ui_dir) or not os.path.exists(ui_index):
        return f"找不到 ui 資料夾或 ui/index.html 基礎資源（預期位置：{ui_dir}）。"
    # 解除安裝介面的兩個檔案只有傳統引擎會用到。MSIX 模式的解除安裝由系統
    # 接管、不編 uninstall.exe（ADR-0006），要求它們存在等於為一個這個模式
    # 用不到的東西擋下建置。
    if not is_msix:
        if not os.path.exists(ui_uninstall_html):
            return f"找不到 ui/uninstall.html 基礎資源（預期位置：{ui_uninstall_html}）。"
        if not os.path.exists(os.path.join(workspace_dir, "uninstall.py")):
            return f"找不到 uninstall.py（預期位置：{workspace_dir}）。"
    return None


def build_msix(app_dir, pack_data, png_path, output_path, workspace_dir,
               doc_icon_path="", signing=None, sdk_tools_settings=None,
               find_tool=None, run=None, log=None):
    """把應用程式資料夾做成 `.msix`，回傳輸出路徑。

    步驟固定為「組裝目錄 → makeappx 打包 →（憑證是本機檔案時）簽章」。

    ## 為什麼簽章是這個函式的一部分，而不是呼叫端各自處理

    第二輪決議第三項的流程存在一個不可消除的斷點：已簽章的 `.msix` 必須在
    編 bootstrapper exe 之前備妥，而簽章可能由呼叫端的雲端代簽處理、不一定
    即時完成。該決議因此以兩截式為骨架，並在其上留一條「一體式」便捷路徑
    ——憑證是本機檔案時由工具自己把三步串完。

    這裡用 `signing` 是否為 None 表達那個分歧，不另外設一個布林旗標：那種
    旗標的意義會依賴另一個值（有旗標但沒憑證時要做什麼？），而呼叫端本來
    就知道自己有沒有本機憑證。`signing` 為 None 時產物按定義是未簽章的
    ——`pack-msix` 走的正是這一條，在那裡簽下去會讓雲端代簽失去容身之處。

    ## 為什麼放在 builder.py 而不是 builder_cli.py

    這條路徑 CLI 與 GUI 都要走得到。留在 CLI 裡的話 GUI 得再寫一份，而兩份
    會分頭長歪成兩種行為。`gui_config.py` 與 `builder_cli.py` 都已經以
    `build_all()` 為共同入口，這個函式與它並列。

    find_tool／run 是測試接縫（比照 file_assoc.py 的 registry 參數）。
    """
    import msix_package

    if find_tool is None:
        def find_tool(name):
            return sdk_tools.find_tool(name, settings=sdk_tools_settings)

    msix = pack_data.get("msix") or {}
    staging_dir = os.path.join(workspace_dir, MSIX_STAGING_DIRNAME)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    msix_package.stage(
        app_dir=app_dir,
        staging_dir=staging_dir,
        png_icon=png_path,
        identity_name=msix["identity_name"],
        certificate_subject=msix["certificate_subject"],
        version=msix["package_version"],
        app_name=pack_data["app_name"],
        publisher=pack_data["publisher"],
        main_exe=pack_data["main_exe"],
        doc_icon=doc_icon_path,
        doc_icons=pack_data.get("doc_icons") or {},
        file_associations=pack_data.get("file_associations") or [],
        add_to_path=pack_data.get("add_to_path", False),
        path_target_exe=pack_data.get("path_target_exe", ""),
        min_windows_version=msix.get("min_windows_version"),
        icons=msix.get("icons") or {},
    )
    packed = msix_package.pack(
        staging_dir, output_path, find_tool=find_tool, run=run, log=log)

    if signing:
        # 簽不成不回傳一份未簽章的套件：呼叫端會把它內嵌進安裝檔，而那份
        # 安裝檔要到終端使用者手上才會失敗（未簽章的套件無法部署）。
        if log:
            log("正在簽署套件...")
        _sign_file(packed, signing, find_tool=find_tool, run=run, log=log)
    return packed


def build_all(
    app_dir, exe_name, app_name, folder_name, version, publisher, png_path, ico_path,
    main_exe, eula_texts=None, eula_default_lang="", dependencies=None, file_associations=None, doc_icon_path="",
    doc_icons=None, add_to_path=False, path_target_exe="", local_appdata_files=None,
    restart_explorer_on_update=False, no_admin_install=False, pre_install_script="", post_install_script="",
    custom_dependencies=None, bundle_dependencies=None, signing=None, custom_install_dir="",
    windows_service=None, scheduled_task=None, dependencies_min_version=None,
    create_restore_point_before_install=False, install_password_env="", install_password="",
    workspace_dir=".", sdk_tools_settings=None, install_engine="traditional",
    signed_msix="", engine_notices=None, progress_callback=None,
):
    """流水線：產生配置 -> 編譯反安裝檔 -> 編譯主安裝檔

    workspace_dir：ui/、installer_core.py、uninstall.py 必須實際存在的工作目錄，
    也是 dist/、build/、installer_config.json 等中間產物的落地位置。

    sdk_tools_settings：這一次建置要用的 SDK 工具設定（signtool 從哪裡來，
    見 sdk_tools.py）。None 代表讀取這台機器的持久設定；呼叫端傳入的則是
    「持久設定疊上這次的命令列覆蓋」的結果，效力只及於這一次執行。

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

    # 引擎相容性的第四類說明（不擋建置、只需要說明為什麼那個設定沒有作用）。
    # 在最開頭就送出去：使用者要在等編譯之前就看到，不是編完才知道自己有
    # 一個設定從頭到尾沒有生效。句子由 packaging_core 依這次的語言組好，
    # 這裡只負責送。
    for notice in (engine_notices or []):
        report(0, notice, cap=1, time_constant=1)

    dependencies = dependencies or []
    file_associations = file_associations or []
    doc_icons = doc_icons or {}
    local_appdata_files = local_appdata_files or []
    custom_dependencies = custom_dependencies or []
    bundle_dependencies = bundle_dependencies or []

    # 安裝密碼有兩個可能的來源（見 docs/adr/0004）：配置精靈直接輸入的
    # install_password 參數，或 install_password_env 指定的環境變數。在這裡
    # 解析成單一的 install_password_value，下面整段流程只認這一個值——來源
    # 不同不該讓打包結果分岔。呼叫端（packaging_core 的驗證）已經確認過
    # 兩者不會同時給、且環境變數當下確實有值。
    install_password_value = install_password or os.environ.get(install_password_env, "")
    password_protected = bool(install_password or install_password_env)
    windows_service = windows_service or {}
    scheduled_task = scheduled_task or {}
    dependencies_min_version = dependencies_min_version or {}
    folder_name = folder_name or app_name

    is_msix = install_engine == "msix"
    if is_msix:
        # 已簽章的 .msix 必須在這一步之前就備妥：它是被 --add-data 塞進
        # exe 資源區塊的，塞進去之後要換成簽過章的版本等於整個重編一次
        # （見規劃文件「下游專案的 CI 建置順序」）。
        if not signed_msix:
            raise Exception(
                "MSIX 引擎需要一份已簽章的 .msix：請先用 pack-msix 產出未簽章的套件、"
                "自行簽章之後，再以 --signed-msix 指定它。"
            )
        if not os.path.isfile(signed_msix):
            raise Exception(f"找不到指定的已簽章套件：{signed_msix}")

    workspace_dir = os.path.abspath(workspace_dir)
    ui_dir = os.path.join(workspace_dir, "ui")
    ui_index = os.path.join(ui_dir, "index.html")

    missing = missing_workspace_resources(workspace_dir, is_msix=is_msix)
    if missing:
        raise Exception(missing)

    if not os.path.exists(os.path.join(workspace_dir, "installer_core.py")):
        raise Exception(f"找不到 installer_core.py（預期位置：{workspace_dir}）。")

    # 每次重新編譯前，先清掉舊的 dist/build 產物，避免用到上一輪殘留檔案
    # 真實抓到的缺陷：pack-msix 把產出的 .msix 放在工作目錄的 dist/，而
    # 下面這一步會清空 dist/——照文件走的三步流程（產出、簽章、編安裝檔）
    # 因此會在第三步先刪掉自己要內嵌的那份套件，PyInstaller 接著回報
    # 「找不到檔案」，而該訊息完全指不到真正的原因。
    #
    # 不改成「叫使用者把檔案放到別的地方」：dist/ 正是上一步告訴他產物
    # 在那裡的位置。改成先複製到工作目錄根層，不論來源在哪都安全。
    embedded_msix = ""
    if is_msix:
        embedded_msix = os.path.join(workspace_dir, os.path.basename(signed_msix))
        if os.path.abspath(embedded_msix) != os.path.abspath(signed_msix):
            shutil.copy(signed_msix, embedded_msix)

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
        # 只留「這份安裝檔有沒有密碼保護」這個布林值。密碼本身絕對不能寫進
        # 這份設定檔——它會被打包進安裝檔、跟著送到每一位終端使用者手上。
        "password_protected": password_protected,
        # 安裝端要靠這兩個欄位知道自己是哪一種引擎、內嵌的套件叫什麼
        # （見 installer_core._trigger_installation_impl_inner 的分流）。
        "install_engine": install_engine,
        "msix_package": os.path.basename(signed_msix) if signed_msix else "",
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
    # CREATE_NO_WINDOW：呼叫端（gui_config.py）是 --noconsole 的 GUI 程式，
    # 沒有這個旗標的話 Windows 會在每次編譯時短暫跳出命令提示字元視窗。
    # 在這裡取一次，兩個編譯步驟共用——原本它宣告在編譯解除安裝助手那段
    # 裡面，而那一段在 MSIX 模式不執行。
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # ADR-0006：MSIX 模式的解除安裝由系統接管，沒有自訂介面，因此這顆
    # uninstall.exe 不編、也不內嵌。編了它只會出現在使用者的安裝目錄裡，
    # 讓人以為可以用，而它在這個模式下什麼都做不到。
    built_uninstall = ""
    if not is_msix:
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

    # 解除安裝助手區段結束，交棒給安裝檔區段。MSIX 模式沒有這個區段，訊息
    # 要跟著改——照原文印「解除安裝助手編譯完成」是對使用者陳述一件沒有發生
    # 的事，而建置訊息正是他判斷流程走到哪裡的依據。
    report(
        35,
        "準備編譯安裝檔..." if is_msix else "解除安裝助手編譯完成，準備編譯安裝檔...",
        cap=40, time_constant=2,
    )

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
    if built_uninstall:
        cmd.append(f"--add-data={built_uninstall};.")
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
    # 安裝密碼保護（見 CONTEXT.md「安裝密碼保護」一節）：有設定密碼時，
    # app_dir 整包加密成一份檔案再內嵌，不能像沒設定密碼保護時那樣直接把
    # 明文 app_dir 塞進 --add-data，不然密碼保護形同虛設。這份暫存加密檔
    # 跟其他暫存產物一樣，在下面的 finally 區塊統一清掉。
    temp_encrypted_payload = None
    try:
        if is_msix:
            # 應用程式檔案由系統從套件裡落地，安裝檔不需要另外帶一份
            # app_contents——帶了等於同一批檔案在 exe 裡放兩次。
            cmd.append(f"--add-data={embedded_msix};.")
        elif password_protected:
            temp_encrypted_payload = os.path.join(workspace_dir, "app_contents.enc")
            install_encryption.encrypt_directory(app_dir, temp_encrypted_payload, install_password_value)
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
        # F06：使用者在 custom_dependencies 填的 sha256 原本沒有被傳給
        # _download_file()，內嵌模式下這個欄位形同裝飾。內建相依元件目前
        # 沒有這個欄位（Microsoft 的永久連結指向的檔案本來就會隨版本更新，
        # 固定不了摘要），所以只從 custom_dependencies 收集。
        dependency_sha256_map = {
            entry["key"]: entry["sha256"]
            for entry in custom_dependencies if entry.get("sha256")
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
                _download_file(url, temp_path, expected_sha256=dependency_sha256_map.get(key))
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
        if (embedded_msix and os.path.exists(embedded_msix)
                and os.path.abspath(embedded_msix) != os.path.abspath(signed_msix)):
            os.remove(embedded_msix)

    if res_installer.returncode != 0:
        tail = ((res_installer.stdout or "") + "\n" + (res_installer.stderr or ""))[-1500:]
        raise Exception(f"主安裝檔編譯打包失敗。\n\n{tail}")

    dist_installer = os.path.join(workspace_dir, "dist", f"{exe_name}.exe")

    if signing:
        # 使用者特地設定了簽章，簽不成不該悄悄放行產出未簽章的檔案——
        # 直接讓整個 pack 流程失敗，而不是打包「成功」但實際上沒簽章。
        report(99, "正在簽署數位簽章...", cap=99, time_constant=2)
        def locate_signtool(name):
            return sdk_tools.find_tool(name, settings=sdk_tools_settings)

        # log 只掛在第一次呼叫：兩次簽的是同一支 signtool，來源那行印兩次
        # 只是重複。
        _sign_file(
            dist_installer, signing, find_tool=locate_signtool,
            log=lambda message: report(99, message, cap=99, time_constant=2),
        )
        if built_uninstall:
            _sign_file(built_uninstall, signing, find_tool=locate_signtool)

    report(100, f"編譯完成，輸出位置：{dist_installer}", cap=100, time_constant=1)