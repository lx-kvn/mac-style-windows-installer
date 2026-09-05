"""
builder_cli.py
--------------
打包工具（#1）的 CLI 進入點，讓開發者可以純靠指令把一個應用程式資料夾
打包成 macOS 風格拖曳安裝的 Setup exe，不需要開 GUI。核心邏輯完全共用
`packaging_core.py`（驗證、環境檢查、工作目錄準備）跟 `builder.py`
（實際編譯流程）——跟 `gui_config.py` 的 `ConfigAPI.start_pack()`/
`_run_pack_thread()` 做的是同一件事，只是「資料從哪裡來」（JSON/命令列
參數，不是表單）跟「進度怎麼呈現」（印到 stdout，不是
`window.evaluate_js()`）不一樣。

子指令：
  - `init`：產生一份帶預設值的範本 JSON 設定檔。
  - `pack`：讀 JSON（`--config`，選填）→ 用命令列參數覆蓋個別欄位
    （CLI 優先於 JSON）→ 驗證 → 編譯。
  - `pack-msix`：產出未簽章的 `.msix`（兩截式流程的第一步，見第九輪定案
    決議）。簽章由呼叫端自行處理，之後再以 `pack --signed-msix` 編出
    bootstrapper exe。
  - `fetch-sdk-tools`：取得簽章／MSIX 打包需要的 Windows SDK 工具。這是
    獨立的一次性環境準備動作，`pack` 不會自行執行它——打包流程不在使用者
    未明確要求的情況下，把一個剛從網路取得的執行檔跑在打包機器上
    （見 docs/adr/0008）。

完整欄位說明、範例見 CLI_USAGE.md。
"""

import argparse
import json
import os
import re
import sys

import builder
import install_engine
import messages
import lang_detect
import packaging_core
import packaging_settings
import cert_store
import sdk_tools

# init 產生的範本：每個欄位都是 validate_and_build_pack_data() /
# builder.build_all() 認得的鍵名，值是說明性的預留位置，不是真的能直接拿去
# 編譯的設定（JSON 沒有註解語法，只能靠這種方式提示使用者要填什麼）。
TEMPLATE = {
    # install_engine：安裝檔內部用哪一種方式落地檔案（見 CONTEXT.md
    # 「傳統引擎與 MSIX 引擎」）。沒填即為 traditional，既有的設定檔
    # 因此不受影響。
    "install_engine": "traditional",
    # msix：只有 install_engine 為 msix 時才會被檢查，傳統引擎完全不看。
    # identity_name 一經發布即不可變更（見 docs/adr/0007），這裡放的是提示性
    # 預留值，不是可以直接拿去用的預設值。certificate_subject 必須與簽章憑證
    # 上記載的名稱完全一致。min_windows_version 留空即採預設 10.0.17763.0。
    "msix": {
        "identity_name": "YourCompany.YourApp",
        "certificate_subject": "CN=Your Company, O=Your Company, C=TW",
        "min_windows_version": "",
        # icons：三個位置的圖示個別覆蓋，留空即沿用 png_icon 同一張
        # （第五輪決議第一項）。都必須是正方形的 PNG。
        "icons": {"tile": "", "taskbar": "", "store": ""},
    },
    "app_dir": "C:\\path\\to\\your\\app\\folder",
    "png_icon": "C:\\path\\to\\drag_icon.png",
    "ico_icon": "C:\\path\\to\\cover_icon.ico",
    "doc_icon": "",
    "doc_icons": {},
    "app_name": "MyCustomApp",
    "folder_name": "",
    "version": "1.0.0",
    "publisher": "CustomPublisher",
    "exe_name": "Setup_MyCustomApp",
    "main_exe": "MyApp.exe",
    "eula_texts": {},
    "eula_default_lang": "",
    "dependencies": [],
    "file_associations": "",
    "add_to_path": False,
    "path_target_exe": "",
    "local_appdata_files": [],
    "no_admin_install": False,
    "custom_install_dir": "",
    "pre_install_script": "",
    "post_install_script": "",
    "custom_dependencies": [],
    "bundle_dependencies": [],
    "signing": {},
    "dependencies_min_version": {},
    # windows_service/scheduled_task/create_restore_point_before_install：
    # 真實抓到的問題（A3：config schema 單一真實來源）——這幾個欄位原本
    # 沒有列在範本裡，跑 builder_cli.py init 拿到的範本看起來就像這個工具
    # 不支援這幾個功能一樣，跟 CLI_USAGE.md 沒補文件是同一種脫鉤問題。
    "windows_service": {"service_name": "", "exe_relative_path": "", "start_type": "auto"},
    "scheduled_task": {"task_name": "", "exe_relative_path": "", "trigger": "onlogon"},
    "create_restore_point_before_install": False,
    "install_password_env": "",
}

# 純量欄位：CLI flag 名稱 -> data 字典鍵名，CLI 有帶值就覆蓋 JSON 對應欄位。
_SCALAR_OVERRIDE_FIELDS = [
    ("install_engine", "install_engine"),
    ("app_name", "app_name"),
    ("folder_name", "folder_name"),
    ("version", "version"),
    ("publisher", "publisher"),
    ("exe_name", "exe_name"),
    ("main_exe", "main_exe"),
    ("eula_default_lang", "eula_default_lang"),
    ("file_associations", "file_associations"),
    ("path_target_exe", "path_target_exe"),
    ("pre_install_script", "pre_install_script"),
    ("post_install_script", "post_install_script"),
    ("custom_install_dir", "custom_install_dir"),
    ("install_password_env", "install_password_env"),
]


def resolve_language(flag):
    """這次的輸出要用哪一種語言（第十四輪決議第八項）。

    帶了 --lang 就用它；沒帶就偵測系統語言，與 GUI 首次啟動時的預設值
    一致。提供旗標是為了 CI——輸出語言跟著執行那台機器的區域設定跑，
    會讓同一份設定在兩台機器上產生不同語言的 log，比對就失效了。
    """
    if flag:
        return flag
    return lang_detect.detect_system_language(
        messages.LANGUAGES, messages.DEFAULT_LANGUAGE)


# 訊息表用到的標記只有 `<br>` 與 `<code>`，這個樣板刻意寫得比那兩個寬：
# 新增第四種標籤時不需要回來改這裡。訊息表裡沒有任何非標記用途的
# 角括號（例如 `<版本號>` 這類佔位字），因此不會誤傷。
_TAG = re.compile(r"<[^>]{1,40}>")


def _strip_html(message):
    """訊息表的標記是給配置精靈的 innerHTML 用的，終端機要先去掉。

    `<br>` 換成真正的換行；其餘標籤整個拿掉，保留它包住的文字——那段文字
    通常正是要照做的指令（例如 `<code>pip install -r requirements.txt</code>`）。

    真實看到的輸出（2026-09-03，在缺少 `winrt-*` 的環境跑 `pack`）：

        請先執行 <code>pip install -r requirements.txt</code> 再打包

    原本這裡只認得 `<br>`，因為當初只有帶 `<br>` 的訊息。後來新增帶其他標籤
    的訊息時，不會有任何地方報錯——症狀只出現在使用者的終端機上。改成通用的
    去標籤，新增第三種標籤時不需要再回來改這裡
    （`tests/test_builder_cli.py` 會比對所有訊息表）。
    """
    return _TAG.sub("", (message or "").replace("<br>", "\n"))


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="builder_cli.py",
        description="打包工具（InstallerBuilder）的 CLI 版本：純指令把應用程式資料夾打包成安裝檔。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="產生一份帶預設值的範本 JSON 設定檔")
    init_p.add_argument("--output", default="installer_pack_config.json", help="範本輸出路徑")

    list_files_p = sub.add_parser(
        "list-files", help="列出 app_dir 底下所有檔案的相對路徑，方便寫 --local-appdata-files 或 JSON 設定檔前先查一下"
    )
    list_files_p.add_argument("--app-dir", dest="app_dir", required=True, help="應用程式內容資料夾")

    pack_p = sub.add_parser("pack", help="驗證設定並編譯出安裝檔")
    pack_p.add_argument(
        "--lang", dest="lang", default=None,
        help=f"訊息語言（{'／'.join(messages.LANGUAGES)}），未指定時依系統語言",
    )
    pack_p.add_argument("--config", default=None, help="JSON 設定檔路徑（選填，沒給就完全靠底下的 flag）")
    pack_p.add_argument("--workspace-dir", default=None, help="編譯工作目錄，預設用 packaging_core.get_workspace_dir()")
    # 這兩個與 --workspace-dir 同一種性質：描述「這台機器上的東西在哪」，
    # 不描述要打包成什麼產品，因此是旗標／工具偏好，不是打包設定檔欄位。
    # 旗標的效力只及於這一次執行，不寫進持久設定。
    pack_p.add_argument(
        "--sdk-tools-dir", dest="sdk_tools_dir", default=None,
        help="手動指定 makeappx／signtool 所在目錄，覆蓋這次建置的自動檢索",
    )
    pack_p.add_argument(
        "--sdk-tools-cache-dir", dest="sdk_tools_cache_dir", default=None,
        help=f"覆蓋 {sdk_tools.FETCH_SUBCOMMAND} 的快取位置（供 CI 納入自己的快取機制）",
    )

    # 路徑類欄位：不是 data 字典的一部分，是 validate_and_build_pack_data()
    # 額外的位置參數（GUI 版是靠檔案選擇對話框取得），這裡直接讓使用者填路徑字串。
    pack_p.add_argument(
        "--signed-msix", dest="signed_msix", default=None,
        help="已簽章的 .msix 路徑，內嵌進 bootstrapper exe（兩截式流程的第二步）",
    )
    pack_p.add_argument("--app-dir", dest="app_dir", default=None, help="應用程式內容資料夾")
    pack_p.add_argument("--png-icon", dest="png_icon", default=None, help="拖拽介面用的 PNG 圖示")
    pack_p.add_argument("--ico-icon", dest="ico_icon", default=None, help="安裝檔封面用的 ICO 圖示")
    pack_p.add_argument("--doc-icon", dest="doc_icon", default=None, help="檔案關聯自訂圖示（選填）")

    for flag_key, dest in _SCALAR_OVERRIDE_FIELDS:
        pack_p.add_argument(f"--{flag_key.replace('_', '-')}", dest=dest, default=None)

    pack_p.add_argument(
        "--dependencies", dest="dependencies", default=None,
        help="逗號分隔，例如 vcredist_x64,dotnet_desktop",
    )
    pack_p.add_argument("--add-to-path", dest="add_to_path", action=argparse.BooleanOptionalAction, default=None)
    pack_p.add_argument(
        "--local-appdata-files", dest="local_appdata_files", default=None,
        help="逗號分隔，相對於 app_dir 的路徑，指定改裝到 %%LOCALAPPDATA%%\\Programs\\<folder_name>（不需要系統管理員權限）",
    )
    # 真實抓到的 bug：argparse.BooleanOptionalAction 的 __call__ 判斷「這次是不是
    # 停用」的方式是看實際打的那個 option string 開頭是不是 "--no-"
    # （CPython 原始碼：not option_string.startswith("--no-")），完全不管這個
    # option string 本來就是你唯一定義、想當「啟用」用的那個。而這個旗標
    # 本身的名字「--no-admin-install」開頭剛好就是 "--no-"——結果不管你是想
    # 啟用還是停用，argparse 都會把它判斷成「停用」，設成 False。也就是說
    # 打 --no-admin-install 這個旗標，過去實際上從來沒有真的生效過，一路
    # 靜默地被 argparse 解讀成「關閉」。改成單純的 store_true（default 仍然
    # 保留 None，跟其他旗標一樣「命令列沒帶就交給 JSON/預設值決定」的語意），
    # 放棄「--no-no-admin-install」這個用來明確停用的寫法——反正預設本來就是
    # False，沒有人需要一個額外的旗標特地把它設回預設值。
    pack_p.add_argument(
        "--no-admin-install", dest="no_admin_install",
        action="store_true", default=None,
        help="開啟後整個安裝檔完全不要求系統管理員權限，改裝到 %%LOCALAPPDATA%%（免 UAC）",
    )
    pack_p.add_argument(
        "--bundle-dependencies", dest="bundle_dependencies", default=None,
        help="逗號分隔，列在 dependencies 裡的相依元件 key，打包時內嵌進安裝檔（不用安裝時再連網下載）",
    )

    msix_p = sub.add_parser(
        "pack-msix", help="產出未簽章的 .msix（兩截式流程的第一步）",
    )
    msix_p.add_argument(
        "--lang", dest="lang", default=None,
        help=f"訊息語言（{'／'.join(messages.LANGUAGES)}），未指定時依系統語言",
    )
    msix_p.add_argument("--config", default=None, help="JSON 設定檔路徑")
    msix_p.add_argument("--output", default=None, help="輸出的 .msix 路徑，預設用套件身分名稱")
    msix_p.add_argument("--workspace-dir", default=None, help="編譯工作目錄")
    msix_p.add_argument("--app-dir", dest="app_dir", default=None)
    msix_p.add_argument("--png-icon", dest="png_icon", default=None)
    msix_p.add_argument("--ico-icon", dest="ico_icon", default=None)
    msix_p.add_argument("--doc-icon", dest="doc_icon", default=None)
    for flag_key, dest in _SCALAR_OVERRIDE_FIELDS:
        msix_p.add_argument(f"--{flag_key.replace('_', '-')}", dest=dest, default=None)
    msix_p.add_argument("--sdk-tools-dir", dest="sdk_tools_dir", default=None)
    msix_p.add_argument("--sdk-tools-cache-dir", dest="sdk_tools_cache_dir", default=None)
    for dest in ("dependencies", "local_appdata_files", "bundle_dependencies"):
        msix_p.add_argument(f"--{dest.replace('_', '-')}", dest=dest, default=None)
    msix_p.add_argument("--add-to-path", dest="add_to_path",
                        action=argparse.BooleanOptionalAction, default=None)
    msix_p.add_argument("--no-admin-install", dest="no_admin_install",
                        action="store_true", default=None)

    fetch_p = sub.add_parser(
        sdk_tools.FETCH_SUBCOMMAND,
        help=f"取得 {'、'.join(sdk_tools.REQUIRED_TOOLS)}（下載固定版本的 {sdk_tools.PACKAGE_ID} 並驗證雜湊）",
    )
    fetch_p.add_argument(
        "--cache-dir", dest="cache_dir", default=None,
        # %% 是必要的：argparse 會把 help 字串當格式字串做 % 展開，
        # 直接寫 %LOCALAPPDATA% 會讓 --help 本身拋 ValueError。
        help="覆蓋快取位置（供 CI 納入自己的快取機制），預設在 %%LOCALAPPDATA%% 底下",
    )
    fetch_p.add_argument(
        "--force", action="store_true",
        help="即使快取已存在也重新下載",
    )

    sub.add_parser(
        "list-certs",
        help="列出可以用來簽章的憑證與它們的指紋（填進 signing.cert_thumbprint 用）",
    )

    return parser


def cmd_pack_msix(args):
    """產出未簽章的 `.msix`（兩截式流程的第一步，見第九輪定案決議）。

    這一步刻意不做簽章：已簽章的 `.msix` 必須在編 bootstrapper exe 之前
    備妥，而簽章可能由呼叫端的雲端代簽處理、不一定即時完成（第二輪決議
    第三項）。把簽章綁進這個指令，等於讓雲端代簽的情境沒有容身之處。
    """
    data, app_dir, png_path, ico_path, doc_icon_path_selected = _load_pack_input(args)

    try:
        engine = install_engine.normalize(data)
    except install_engine.UnknownEngine as e:
        print(str(e), file=sys.stderr)
        return 1
    if engine != install_engine.MSIX:
        # `.msix` 是 MSIX 引擎的產物（第二輪決議第二項），傳統引擎沒有它。
        print(
            "pack-msix 只適用於 MSIX 引擎，這份設定的 install_engine 是 "
            f"{engine}。要產出 .msix 請把 install_engine 設成 "
            f"{install_engine.MSIX}。",
            file=sys.stderr,
        )
        return 1

    pack_data, error = packaging_core.validate_and_build_pack_data(
        data, app_dir, png_path, ico_path, doc_icon_path_selected,
        lang=resolve_language(getattr(args, "lang", None)),
    )
    if error:
        print(_strip_html(error), file=sys.stderr)
        return 1

    # 第四類的說明（不擋建置、只需要說明為什麼那個設定沒有作用）。這條指令
    # 走的是 build_msix，不經過 build_all，因此不能靠 build_all 的進度回報
    # ——真實抓到的缺口：說明原本只掛在那裡，而 pack-msix 正是 CI 走的那一條，
    # 那裡沒有人盯著畫面看有沒有欄位被灰掉。
    for notice in pack_data.get("engine_notices") or []:
        print(notice)

    workspace_dir = args.workspace_dir or packaging_core.get_workspace_dir()
    msix = pack_data["msix"]
    output = args.output or os.path.join(
        workspace_dir, "dist", f"{msix['identity_name']}.msix")
    # 預設檔名用套件身分名稱而不是 app_name：後者是自由文字、可以是中文，
    # 不保證能當檔名；前者的字元集本來就受限（見 docs/adr/0007）。
    sdk_settings = sdk_tools.settings_with_overrides(
        tools_dir=getattr(args, "sdk_tools_dir", None),
        cache_dir=getattr(args, "sdk_tools_cache_dir", None),
        settings=packaging_settings.load_settings(),
    )

    try:
        # signing 一律傳 None：這個指令的產物按定義是未簽章的，即使設定裡
        # 有本機憑證也一樣。使用者跑這個指令，要的就是一份還沒簽的套件，
        # 好拿去交給代簽服務；在這裡順手簽下去等於讓那個情境無處可去。
        builder.build_msix(
            app_dir=app_dir,
            pack_data=pack_data,
            png_path=png_path,
            output_path=output,
            workspace_dir=workspace_dir,
            doc_icon_path=doc_icon_path_selected,
            signing=None,
            sdk_tools_settings=sdk_settings,
            log=print,
        )
    except Exception as e:
        print(f"產出 .msix 失敗：{e}", file=sys.stderr)
        return 1

    print(f"完成，未簽章的套件：{output}")
    print("下一步：自行簽章，再以 pack --signed-msix 編出 bootstrapper exe。")
    return 0


def cmd_fetch_sdk_tools(args):
    """取得 SDK 工具（ADR-0008 決定一：使用者明確要求才下載）。

    這個指令是使用者對「在這台機器上執行一個從網路取得的執行檔」表達同意
    的地方。工具能做的是讓它成為一次明確的動作，無法確保使用者理解該動作
    的後果（見該 ADR 的已知限制）。
    """
    settings = sdk_tools.settings_with_overrides(
        cache_dir=args.cache_dir, settings=packaging_settings.load_settings(),
    )
    print(f"來源：{sdk_tools.PACKAGE_URL}")
    print(f"下載後會驗證 SHA-256 是否為 {sdk_tools.PACKAGE_SHA256}。")
    try:
        result = sdk_tools.fetch_tools(settings=settings, force=args.force, log=print)
    except Exception as e:
        print(f"取得 SDK 工具失敗：{e}", file=sys.stderr)
        return 1
    for tool, path in sorted(result.tools.items()):
        print(f"  {tool}: {path}")
    print(f"完成。版本 {result.version}，位置：{result.cache_dir}")
    return 0


def cmd_list_certs(args):
    """列出個人存放區裡可以用來簽章的憑證（ADR-0014 決定五）。

    這個指令服務的是 `signing.cert_thumbprint` 這個欄位製造出來的摩擦：那
    四十個十六進位字元要從某個地方來。列出來還不夠——使用者還要知道貼到哪裡
    去，因此最後印一行說明欄位名稱。

    只讀個人存放區，不讀也不寫任何信任存放區，因此與 docs/adr/0005 的兩項
    決定皆不衝突。兩者是不同的東西，見 CONTEXT.md「簽章憑證的兩種來源」。
    """
    found = cert_store.list_signing_certificates()
    if not found:
        # 什麼都不印的話，使用者分不出「沒有憑證」與「指令壞了」。
        print("這台電腦的個人憑證存放區裡沒有可以用來簽章的憑證。")
        print("把含私鑰的 .pfx 匯入之後再跑一次，例如：")
        print("    Import-PfxCertificate -FilePath cert.pfx "
              r"-CertStoreLocation Cert:\CurrentUser\My "
              "-Password (Read-Host -AsSecureString)")
        return 0

    print(f"找到 {len(found)} 張可以用來簽章的憑證：")
    for entry in found:
        where = "目前使用者" if entry.store == cert_store.CURRENT_USER else "本機電腦"
        print()
        print(f"  {entry.subject}")
        print(f"    指紋　：{entry.thumbprint}")
        print(f"    有效至：{entry.not_after or '（讀不到）'}")
        print(f"    存放區：{where}")
    print()
    print("把要用的那一張的指紋填進設定檔的 signing.cert_thumbprint，"
          "打包時就不會有密碼出現在命令列上。")
    return 0


def cmd_init(args):
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(TEMPLATE, f, ensure_ascii=False, indent=2)
    print(f"已產生範本設定檔：{args.output}")
    print("欄位說明與範例請見 CLI_USAGE.md。")
    return 0


def cmd_list_files(args):
    files = packaging_core.list_app_dir_files(args.app_dir)
    if not files:
        print(f"（找不到檔案，請確認 app_dir 路徑是否正確：{args.app_dir}）")
        return 0
    for f in files:
        print(f)
    return 0


def _load_pack_input(args):
    """組出 validate_and_build_pack_data() 需要的 data 字典 + 四個路徑參數。
    JSON（--config，選填）是底，命令列參數有帶值就覆蓋對應欄位——CLI 優先。
    """
    data = {}
    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            data = json.load(f)

    app_dir = args.app_dir or data.get("app_dir", "")
    png_path = args.png_icon or data.get("png_icon", "")
    ico_path = args.ico_icon or data.get("ico_icon", "")
    doc_icon_path_selected = args.doc_icon or data.get("doc_icon", "")

    for flag_key, dest in _SCALAR_OVERRIDE_FIELDS:
        value = getattr(args, dest)
        if value is not None:
            data[dest] = value

    if args.dependencies is not None:
        data["dependencies"] = [d.strip() for d in args.dependencies.split(",") if d.strip()]
    if args.add_to_path is not None:
        data["add_to_path"] = args.add_to_path
    if args.local_appdata_files is not None:
        data["local_appdata_files"] = [f.strip() for f in args.local_appdata_files.split(",") if f.strip()]
    if args.no_admin_install is not None:
        data["no_admin_install"] = args.no_admin_install
    if args.bundle_dependencies is not None:
        data["bundle_dependencies"] = [d.strip() for d in args.bundle_dependencies.split(",") if d.strip()]

    # need_file_assoc / use_custom_doc_icon 這兩個布林欄位在 GUI 版是「勾選框
    # 決定要不要套用旁邊欄位」，CLI 版直接依對應欄位是否有內容推斷，不需要
    # 使用者額外指定一個看起來多餘的旗標。
    data.setdefault("need_file_assoc", bool(data.get("file_associations")))
    data.setdefault("use_custom_doc_icon", bool(doc_icon_path_selected))
    data.setdefault("eula_texts", data.get("eula_texts", {}))
    data.setdefault("doc_icons", data.get("doc_icons", {}))
    data.setdefault("eula_default_lang", data.get("eula_default_lang", ""))
    data.setdefault("custom_dependencies", data.get("custom_dependencies", []))
    data.setdefault("signing", data.get("signing", {}))
    data.setdefault("windows_service", data.get("windows_service", {}))
    data.setdefault("scheduled_task", data.get("scheduled_task", {}))
    data.setdefault("dependencies_min_version", data.get("dependencies_min_version", {}))
    data.setdefault("create_restore_point_before_install", bool(data.get("create_restore_point_before_install", False)))
    data.setdefault("install_password_env", data.get("install_password_env", ""))
    # need_install_password 跟 need_file_assoc 同一種欄位：GUI 版是「啟用
    # 安裝密碼保護」那顆勾選框的狀態，CLI 版沒有勾選框，依 install_password_env
    # 有沒有內容推斷。設定檔不支援直接寫密碼（見 docs/adr/0004），所以 CLI
    # 這條路只有環境變數一種來源。
    data.setdefault("need_install_password", bool(data.get("install_password_env")))

    return data, app_dir, png_path, ico_path, doc_icon_path_selected


def cmd_pack(args):
    data, app_dir, png_path, ico_path, doc_icon_path_selected = _load_pack_input(args)

    env = packaging_core.check_build_environment()
    if not env["ready"]:
        missing = []
        if not env["pyinstaller_found"]:
            missing.append("pyinstaller")
        if not env["python_found"]:
            missing.append("python")
        if env["python_found"] and not env["webview_found"]:
            missing.append("pywebview")
        print(f"環境檢查失敗：缺少 {'、'.join(missing)}，請先安裝必要環境後再試一次。", file=sys.stderr)
        return 1

    lang = resolve_language(getattr(args, "lang", None))
    pack_data, error = packaging_core.validate_and_build_pack_data(
        data, app_dir, png_path, ico_path, doc_icon_path_selected,
        lang=lang,
    )
    if error:
        print(_strip_html(error), file=sys.stderr)
        return 1

    # 引擎需要的第三方套件在不在。這一項必須在驗證之後才問得出來：要先知道
    # 這份設定選的是哪一個引擎，才知道需不需要問。缺少時整個打包流程仍然會
    # 成功，只是產出的安裝檔在任何機器上都裝不起來（見
    # packaging_core.missing_engine_dependencies）。位置在工作目錄檢查之前，
    # 理由與那道檢查前移一致：先報真正該修的那一項。
    engine_dependency_problem = packaging_core.missing_engine_dependencies(
        pack_data["install_engine"], env, lang=lang)
    if engine_dependency_problem:
        print(_strip_html(engine_dependency_problem), file=sys.stderr)
        return 1

    workspace_dir = args.workspace_dir or packaging_core.get_workspace_dir()
    sdk_settings = sdk_tools.settings_with_overrides(
        tools_dir=args.sdk_tools_dir,
        cache_dir=args.sdk_tools_cache_dir,
        settings=packaging_settings.load_settings(),
    )

    prep_error = packaging_core.ensure_workspace_files(workspace_dir)
    if prep_error:
        print(prep_error, file=sys.stderr)
        return 1
    is_msix = pack_data.get("install_engine") == install_engine.MSIX
    # 在動手打包之前先問一次工作目錄齊不齊：一體式流程下，makeappx 打包與
    # signtool 簽章（含一次連到時間戳記伺服器的往返）都會發生在 build_all
    # 之前，而 build_all 開頭那個廉價的資源檢查若留到那時才跑，那些力氣就
    # 白花了。build_all 自己仍然會再檢查一次。
    missing = builder.missing_workspace_resources(workspace_dir, is_msix=is_msix)
    if missing:
        print(missing, file=sys.stderr)
        return 1

    signed_msix = args.signed_msix or ""
    if is_msix and not signed_msix:
        # MSIX 模式需要一份已簽章的 .msix：它是被塞進 exe 資源區塊的，塞進去
        # 之後要換成簽過章的版本等於整個重編一次，因此簽章一定要在這一步
        # 之前完成（見規劃文件「下游專案的 CI 建置順序」）。
        #
        # 憑證是本機檔案時，那三個步驟工具自己串得起來——第二輪決議第三項在
        # 兩截式骨架之上留的正是這條便捷路徑。判斷依據是設定裡有沒有
        # `signing`：它的 cert_path 一律是本機 .pfx（上面的驗證已確認檔案
        # 實際存在），因此「有 signing」與「憑證在本機」是同一件事，不需要
        # 使用者再多選一個模式。
        if not pack_data.get("signing"):
            print(
                "MSIX 引擎需要一份已簽章的 .msix，而這份設定沒有 signing 欄位\n"
                "（憑證不在本機，例如交給雲端代簽）。這種情況流程是兩截的：\n"
                "  1. pack-msix 產出未簽章的 .msix\n"
                "  2. 自行簽章\n"
                "  3. pack --signed-msix <已簽章的.msix> 編出安裝檔\n"
                "憑證就在本機時，把它填進 signing，pack 會自己把三步串完。",
                file=sys.stderr,
            )
            return 1
        identity_name = (pack_data.get("msix") or {}).get("identity_name", "package")
        try:
            signed_msix = builder.build_msix(
                app_dir=app_dir,
                pack_data=pack_data,
                png_path=png_path,
                # 放在工作目錄底下、不放進 dist/：後者會在編 bootstrapper exe
                # 之前被清空，中間產物擺在那裡會在被內嵌之前就消失。
                output_path=os.path.join(workspace_dir, f"{identity_name}.msix"),
                workspace_dir=workspace_dir,
                doc_icon_path=doc_icon_path_selected,
                signing=pack_data["signing"],
                sdk_tools_settings=sdk_settings,
                log=print,
            )
        except Exception as e:
            print(f"產出 .msix 失敗：{e}", file=sys.stderr)
            return 1

    def progress_handler(percent, message, cap=99, time_constant=15):
        print(f"[{percent:>3}%] {message}")

    exe_name = pack_data["exe_name"].strip()
    try:
        builder.build_all(
            app_dir=app_dir,
            exe_name=exe_name,
            app_name=pack_data["app_name"].strip(),
            folder_name=pack_data.get("folder_name") or pack_data["app_name"].strip(),
            version=pack_data["version"].strip(),
            publisher=pack_data["publisher"].strip(),
            png_path=png_path,
            ico_path=ico_path,
            main_exe=pack_data["main_exe"],
            eula_texts=pack_data.get("eula_texts", {}),
            eula_default_lang=pack_data.get("eula_default_lang", ""),
            dependencies=pack_data.get("dependencies", []),
            file_associations=pack_data.get("file_associations", []),
            doc_icon_path=pack_data.get("doc_icon_path", ""),
            doc_icons=pack_data.get("doc_icons", {}),
            add_to_path=pack_data.get("add_to_path", False),
            path_target_exe=pack_data.get("path_target_exe", ""),
            local_appdata_files=pack_data.get("local_appdata_files", []),
            restart_explorer_on_update=pack_data.get("restart_explorer_on_update", False),
            no_admin_install=pack_data.get("no_admin_install", False),
            custom_install_dir=pack_data.get("custom_install_dir", ""),
            pre_install_script=pack_data.get("pre_install_script", ""),
            post_install_script=pack_data.get("post_install_script", ""),
            custom_dependencies=pack_data.get("custom_dependencies", []),
            bundle_dependencies=pack_data.get("bundle_dependencies", []),
            signing=pack_data.get("signing"),
            windows_service=pack_data.get("windows_service", {}),
            scheduled_task=pack_data.get("scheduled_task", {}),
            dependencies_min_version=pack_data.get("dependencies_min_version", {}),
            create_restore_point_before_install=pack_data.get("create_restore_point_before_install", False),
            install_password_env=pack_data.get("install_password_env", ""),
            workspace_dir=workspace_dir,
            install_engine=pack_data.get("install_engine", "traditional"),
            signed_msix=signed_msix,
            msix_identity_name=(pack_data.get("msix") or {}).get("identity_name", ""),
            engine_notices=pack_data.get("engine_notices"),
            sdk_tools_settings=sdk_settings,
            progress_callback=progress_handler,
        )
    except Exception as e:
        print(f"編譯失敗：{e}", file=sys.stderr)
        return 1

    dist_path = os.path.join(workspace_dir, "dist", f"{exe_name}.exe")
    print(f"編譯完成！安裝檔已成功建立：{dist_path}")
    return 0


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "list-files":
        return cmd_list_files(args)
    if args.command == "pack":
        return cmd_pack(args)
    if args.command == "pack-msix":
        return cmd_pack_msix(args)
    if args.command == "list-certs":
        return cmd_list_certs(args)
    if args.command == sdk_tools.FETCH_SUBCOMMAND:
        return cmd_fetch_sdk_tools(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
