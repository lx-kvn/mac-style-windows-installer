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

兩個子指令：
  - `init`：產生一份帶預設值的範本 JSON 設定檔。
  - `pack`：讀 JSON（`--config`，選填）→ 用命令列參數覆蓋個別欄位
    （CLI 優先於 JSON）→ 驗證 → 編譯。

完整欄位說明、範例見 CLI_USAGE.md。
"""

import argparse
import json
import os
import sys

import builder
import packaging_core

# init 產生的範本：每個欄位都是 validate_and_build_pack_data() /
# builder.build_all() 認得的鍵名，值是說明性的預留位置，不是真的能直接拿去
# 編譯的設定（JSON 沒有註解語法，只能靠這種方式提示使用者要填什麼）。
TEMPLATE = {
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
}

# 純量欄位：CLI flag 名稱 -> data 字典鍵名，CLI 有帶值就覆蓋 JSON 對應欄位。
_SCALAR_OVERRIDE_FIELDS = [
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
]


def _strip_html(message):
    """validate_and_build_pack_data() 回傳的錯誤訊息帶 <br> 是給 GUI
    innerHTML 用的，終端機印出來要換成真正的換行。"""
    return (message or "").replace("<br>", "\n")


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
    pack_p.add_argument("--config", default=None, help="JSON 設定檔路徑（選填，沒給就完全靠底下的 flag）")
    pack_p.add_argument("--workspace-dir", default=None, help="編譯工作目錄，預設用 packaging_core.get_workspace_dir()")

    # 路徑類欄位：不是 data 字典的一部分，是 validate_and_build_pack_data()
    # 額外的位置參數（GUI 版是靠檔案選擇對話框取得），這裡直接讓使用者填路徑字串。
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

    return parser


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

    pack_data, error = packaging_core.validate_and_build_pack_data(
        data, app_dir, png_path, ico_path, doc_icon_path_selected,
    )
    if error:
        print(_strip_html(error), file=sys.stderr)
        return 1

    workspace_dir = args.workspace_dir or packaging_core.get_workspace_dir()
    prep_error = packaging_core.ensure_workspace_files(workspace_dir)
    if prep_error:
        print(prep_error, file=sys.stderr)
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
            workspace_dir=workspace_dir,
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
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
