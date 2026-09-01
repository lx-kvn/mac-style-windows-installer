"""MSIX 部署探針：把規劃文件裡幾個「尚未驗證的前提」在真實系統上問出答案。

搭配 `.github/workflows/spike-msix-deployment.yml` 使用。這支腳本不是本工具
的一部分，也不會被打包進任何產物——它是 CI 上的一次性探針，回答
`docs/proposals/MSIX輸出規劃.md`「待辦事項」第 1 項所列的問題：

1. 進度 callback 在真實部署過程中是否實際被呼叫（第三輪 spike 只確認介面
   存在，因為那次部署在簽章驗證階段就被拒絕，未進入實際部署）。
2. 伺服器版 Windows（GitHub Actions runner）能否部署 MSIX。
3. 降權執行時能否部署（驗證「不需系統管理員權限」這項特性）。
4. 尺寸與宣告不符的圖示是否會被系統拒絕部署（第五輪決議第一項的成立以此
   為條件）。

為什麼只能在 CI 上做：部署一份自簽套件需要該憑證先被信任，而
[ADR-0005](../../docs/adr/0005-installer-never-installs-certificates-into-trust-stores.md)
決定三指出，用完即丟的 runner 正是信任測試憑證的適當環境——它的信任存放區
不具持續性。在開發者自己的機器上做同一件事，等於留下一張長期被信任的自簽
憑證。

**非同步操作一律用 `get()`，不用 `get_results()`。** 後者不等待操作完成，
在操作仍進行中呼叫會回傳一個 `extended_error_code = 0`、`error_text = ""`、
`is_registered = False` 的結果，與「成功」難以區分（第三輪 spike 第四項）。
"""
import argparse
import ctypes
import ctypes.wintypes as wintypes
import os
import struct
import sys
import zlib


def write_png(path, width, height, rgba=(0x4A, 0x90, 0xD9, 0xFF)):
    """寫一張純色 PNG。不引進 Pillow——這裡只需要「一張指定尺寸的合法 PNG」，
    為此增加一個相依（且要讓 CI 也裝）不划算。"""
    raw = b"".join(
        b"\x00" + bytes(rgba) * width for _ in range(height)
    )

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def make_package_dir(target, identity, publisher, icon_size=150,
                     file_assoc=None, assoc_logo=False, localized=False):
    """造出一個可以交給 makeappx 的目錄。

    清單由本專案的 `msix_manifest.render()` 產生，不在這裡另寫一份——探針
    自己手寫一份清單的話，它會與產品程式碼漂移，而且 CI 驗到的會是探針
    自己的清單對不對，不是產生器對不對。改成共用之後，每一次 CI 執行都
    順帶驗證了產生器的產出能不能通過 makeappx 與實際部署。

    `icon_size` 決定三張圖示實際的像素尺寸；宣告的位置固定，因此傳入 150
    以外的值即為「尺寸與宣告不符」的情形。
    """
    import shutil
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    import msix_manifest

    os.makedirs(target, exist_ok=True)
    # 借用系統的 notepad.exe 當替身，比照 test-packaging-options.yml 的既有
    # 做法：這裡不需要這支 exe 真的能做什麼，只需要它是一個合法的 PE 檔案。
    shutil.copy(os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                             "System32", "notepad.exe"),
                os.path.join(target, "app.exe"))
    for name in ("tile.png", "small.png", "store.png"):
        write_png(os.path.join(target, name), icon_size, icon_size)

    doc_icon = ""
    if file_assoc and assoc_logo:
        write_png(os.path.join(target, "doc.png"), 150, 150, rgba=(0xD9, 0x53, 0x4F, 0xFF))
        doc_icon = "doc.png"

    display_names = LOCALIZED_VALUES if localized else None
    xml = msix_manifest.render(
        identity_name=identity,
        certificate_subject=publisher,
        version="1.0.0.0",
        app_name="MSWI Probe",
        publisher="MSWI Probe",
        main_exe="app.exe",
        file_associations=[file_assoc] if file_assoc else [],
        doc_icon=doc_icon,
        display_names=display_names,
        default_language=LOCALIZED_LANGUAGES[0] if localized else None,
    )
    with open(os.path.join(target, "AppxManifest.xml"), "w", encoding="utf-8") as f:
        f.write(xml)
    if localized:
        msix_manifest.write_resource_sources(target, LOCALIZED_VALUES)
    return target


# 第一個是預設語言（清單中 <Resource> 的第一筆即為預設）。
LOCALIZED_LANGUAGES = ("en-us", "zh-tw")
LOCALIZED_VALUES = {"en-us": "Probe English Name", "zh-tw": "探針中文名稱"}


# --- 檔案關聯的圖示：系統實際會用哪一個 -----------------------------------
#
# 規劃文件裡有一個沒有答案的問題：MSIX 的檔案關聯宣告中 <uap:Logo> 是選填，
# 沒填時檔案總管顯示什麼？官方文件只說「通用的預設圖示，或關聯程式的圖示，
# 視 Windows 版本與設定而定」，這個差別決定本專案要不要在 MSIX 模式要求
# 使用者另外提供 PNG 版的關聯圖示。
#
# 不靠肉眼看檔案總管——SHGetFileInfoW 搭配 SHGFI_ICONLOCATION 會直接回報
# 「這個副檔名的檔案，殼層會去哪個檔案的第幾號資源取圖示」，headless 環境
# 一樣問得到，而且答案是明確的路徑而非一張圖。
SHGFI_ICONLOCATION = 0x000001000
SHGFI_USEFILEATTRIBUTES = 0x000000010
FILE_ATTRIBUTE_NORMAL = 0x00000080


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HANDLE),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


def shell_icon_for_extension(extension):
    """回傳 (圖示來源路徑, 索引)——殼層對這個副檔名實際使用的圖示位置。"""
    info = _SHFILEINFOW()
    shell32 = ctypes.WinDLL("shell32.dll")
    result = shell32.SHGetFileInfoW(
        f"probe{extension}", FILE_ATTRIBUTE_NORMAL, ctypes.byref(info),
        ctypes.sizeof(info), SHGFI_ICONLOCATION | SHGFI_USEFILEATTRIBUTES,
    )
    if not result:
        return ("", -1)
    return (info.szDisplayName, info.iIcon)


def deploy(package_path):
    """把套件交給系統部署，回傳 (成功與否, 說明, 進度回報次數)。"""
    from winrt.windows.foundation import Uri
    from winrt.windows.management.deployment import PackageManager, DeploymentOptions

    manager = PackageManager()
    uri = Uri(f"file:///{os.path.abspath(package_path).replace(os.sep, '/')}")

    progress_calls = []
    operation = manager.add_package_async(uri, [], DeploymentOptions.NONE)

    def on_progress(_sender, progress):
        progress_calls.append(getattr(progress, "percentage", None))

    operation.progress = on_progress

    # get() 會等待操作實際完成；get_results() 不會，會回傳一個看起來像成功
    # 的空結果（見模組說明）。
    result = operation.get()

    status = getattr(operation, "status", None)
    error_code = getattr(result, "extended_error_code", None)
    error_text = getattr(result, "error_text", "") or ""
    is_registered = getattr(result, "is_registered", False)

    ok = bool(is_registered) and not error_text
    detail = (
        f"status={status} is_registered={is_registered} "
        f"error_code={error_code!r} error_text={error_text!r}"
    )
    return ok, detail, progress_calls


def remove(package_full_name):
    """移除套件。

    與列舉一樣，綁定把 WinRT 的多載拆成不同名稱：`remove_package_async`
    只收一個參數，帶 `RemovalOptions` 的版本另有其名。實測（CI run
    33422386563）以兩個參數呼叫 `remove_package_async` 會得到
    `TypeError: Invalid parameter count`，因此這裡逐一嘗試並印出綁定實際
    提供了哪些——那本身也是這次探針要記錄的事實。
    """
    from winrt.windows.management.deployment import PackageManager, RemovalOptions

    manager = PackageManager()
    available = [n for n in dir(manager) if n.startswith("remove_package")]
    print(f"    綁定實際提供的移除方法：{available}")

    attempts = [
        ("remove_package_with_options_async", (package_full_name, RemovalOptions.NONE)),
        ("remove_package_async", (package_full_name,)),
        ("remove_package_async", (package_full_name, RemovalOptions.NONE)),
    ]
    for name, args in attempts:
        method = getattr(manager, name, None)
        if method is None:
            continue
        try:
            operation = method(*args)
        except TypeError as e:
            print(f"    {name}{tuple(type(a).__name__ for a in args)} 不可用：{e}")
            continue
        print(f"    使用 {name}() 成功，參數數量 {len(args)}")
        result = operation.get()
        return getattr(result, "error_text", "") or ""
    raise RuntimeError(f"找不到可用的移除方法，綁定提供的是：{available}")


def list_current_user_packages(manager):
    """列出當前使用者的套件。

    綁定套件把 WinRT 的多載方法拆成不同名稱，而規劃文件記載第三輪 spike
    用的是 `find_packages_by_user_security_id`，官方 API 名稱則是
    `FindPackagesForUser`。與其賭一個名字（猜錯會白跑一次 CI），這裡逐一
    嘗試並印出實際可用的那一個——那本身就是這次探針要記錄的事實之一。
    """
    candidates = [
        "find_packages_for_user",
        "find_packages_by_user_security_id",
        "find_packages",
    ]
    available = [n for n in dir(manager) if n.startswith("find_packages")]
    print(f"    綁定實際提供的列舉方法：{available}")
    for name in candidates:
        method = getattr(manager, name, None)
        if method is None:
            continue
        try:
            packages = list(method(""))
            print(f"    使用 {name}() 成功")
            return packages
        except Exception as e:
            print(f"    {name}() 不可用：{type(e).__name__}: {e}")
    raise RuntimeError(f"找不到可用的套件列舉方法，綁定提供的是：{available}")


def find_package(identity_name):
    from winrt.windows.management.deployment import PackageManager

    manager = PackageManager()
    for package in list_current_user_packages(manager):
        if package.id.name == identity_name:
            return package.id.full_name
    return None


def main():
    parser = argparse.ArgumentParser(description="MSIX 部署探針")
    sub = parser.add_subparsers(dest="command", required=True)

    icon = sub.add_parser("make-icon", help="產生一張指定尺寸的 PNG")
    icon.add_argument("--path", required=True)
    icon.add_argument("--size", type=int, required=True)

    pkg = sub.add_parser("make-package-dir", help="造一個可交給 makeappx 的目錄")
    pkg.add_argument("--target", required=True)
    pkg.add_argument("--identity", required=True)
    pkg.add_argument("--publisher", required=True)
    pkg.add_argument("--icon-size", type=int, default=150)
    pkg.add_argument("--file-assoc", default=None, help="宣告這個副檔名的檔案關聯，例如 .mswiprobe")
    pkg.add_argument("--assoc-logo", action="store_true", help="檔案關聯一併宣告 <uap:Logo>")
    pkg.add_argument("--localized", action="store_true", help="顯示名稱改用 ms-resource: 參照")

    icons = sub.add_parser("shell-icon", help="問殼層：這個副檔名的檔案用哪個圖示")
    icons.add_argument("--ext", required=True)
    icons.add_argument("--label", default="")

    name = sub.add_parser("package-display-name", help="讀出已部署套件的顯示名稱")
    name.add_argument("--identity-name", required=True)

    dep = sub.add_parser("deploy", help="部署一份套件")
    dep.add_argument("--package", required=True)
    dep.add_argument("--expect", choices=("success", "rejected"), default="success")
    dep.add_argument("--label", default="")

    rm = sub.add_parser("remove", help="移除已部署的套件")
    rm.add_argument("--identity-name", required=True)

    args = parser.parse_args()

    if args.command == "make-icon":
        write_png(args.path, args.size, args.size)
        print(f"已產生 {args.size}x{args.size} 的 PNG：{args.path}")
        return 0

    if args.command == "make-package-dir":
        make_package_dir(
            args.target, args.identity, args.publisher, args.icon_size,
            file_assoc=args.file_assoc, assoc_logo=args.assoc_logo,
            localized=args.localized,
        )
        print(
            f"已造出套件目錄 {args.target}（圖示 {args.icon_size}x{args.icon_size}"
            f"，檔案關聯 {args.file_assoc or '無'}"
            f"，關聯圖示 {'有' if args.assoc_logo else '無'}"
            f"，多語系名稱 {'是' if args.localized else '否'}）"
        )
        return 0

    if args.command == "shell-icon":
        path, index = shell_icon_for_extension(args.ext)
        label = args.label or args.ext
        print(f"=== 殼層對 {args.ext} 使用的圖示（{label}）===")
        if not path:
            print("    查不到——SHGetFileInfoW 沒有回報圖示位置")
        else:
            print(f"    來源：{path}")
            print(f"    索引：{index}")
        return 0

    if args.command == "package-display-name":
        from winrt.windows.management.deployment import PackageManager

        manager = PackageManager()
        for package in list_current_user_packages(manager):
            if package.id.name == args.identity_name:
                display = package.display_name
                print(f"=== 已部署套件 {args.identity_name} 的顯示名稱 ===")
                print(f"    display_name = {display!r}")
                if str(display).startswith("ms-resource:"):
                    print("    結果：顯示名稱沒有被解析，直接留著 ms-resource: 原始字串——"
                          "官方所述的陷阱確實存在，資源檔的產生方式需要調整。")
                else:
                    print("    結果：顯示名稱已正確解析為翻譯後的字串。")
                return 0
        print(f"找不到已部署的套件 {args.identity_name}")
        return 1

    if args.command == "remove":
        full_name = find_package(args.identity_name)
        if not full_name:
            print(f"找不到已部署的套件 {args.identity_name}，不需要移除")
            return 0
        error = remove(full_name)
        print(f"移除 {full_name}：{error or '成功'}")
        return 0

    label = args.label or os.path.basename(args.package)
    print(f"=== 部署 {label} ===")
    print(f"    以系統管理員身分執行：{is_elevated()}")
    try:
        ok, detail, progress_calls = deploy(args.package)
    except Exception as e:
        print(f"    部署呼叫本身拋出例外：{type(e).__name__}: {e}")
        return 0 if args.expect == "rejected" else 1

    print(f"    {detail}")
    print(f"    進度回報次數：{len(progress_calls)}")
    if progress_calls:
        print(f"    進度回報內容（前 10 筆）：{progress_calls[:10]}")

    if args.expect == "success":
        if ok:
            print("    結果：成功（符合預期）")
            return 0
        print("    結果：失敗（預期應該成功）")
        return 1

    # `--expect rejected` 用於圖示尺寸那一題，該題的兩種結果都是有效答案，
    # exit code 只是用來把「非預設的那個結果」標示出來，不代表對錯。措辭
    # 要對應到規劃文件的前提本身，不要對應到這裡設定的預期——第五輪決議
    # 第一項的前提是「尺寸與宣告不符不會被系統拒絕」，因此部署成功代表
    # 前提**成立**。首次撰寫時這兩句話寫反了（CI run 33422609860 的紀錄
    # 裡留有錯誤的措辭）。
    if ok:
        print("    結果：部署成功。系統不因圖示尺寸與宣告不符而拒絕部署，"
              "第五輪決議第一項的前提成立。")
        return 1
    print("    結果：被系統拒絕。第五輪決議第一項的前提不成立，"
          "沿用同一張 PNG 的作法需改為自動縮放或要求使用者提供三張。")
    return 0


def is_elevated():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return "無法判斷"


if __name__ == "__main__":
    sys.exit(main())
