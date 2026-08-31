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


MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">
  <Identity Name="{identity}" Publisher="{publisher}" Version="1.0.0.0"
            ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>MSWI Deployment Probe</DisplayName>
    <PublisherDisplayName>MSWI Probe</PublisherDisplayName>
    <Logo>store.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0"
                        MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Resources><Resource Language="en-us" /></Resources>
  <Capabilities><rescap:Capability Name="runFullTrust" /></Capabilities>
  <Applications>
    <Application Id="App" Executable="app.exe" EntryPoint="windows.fullTrustApplication">
      <uap:VisualElements DisplayName="MSWI Probe" Description="MSWI deployment probe"
                          BackgroundColor="transparent"
                          Square150x150Logo="tile.png" Square44x44Logo="small.png" />
    </Application>
  </Applications>
</Package>
"""


def make_package_dir(target, identity, publisher, icon_size):
    """造出一個可以交給 makeappx 的目錄。

    `icon_size` 決定三張圖示實際的像素尺寸。宣告的位置固定是
    Square150x150Logo／Square44x44Logo／Logo，因此傳入 150 以外的值即為
    「尺寸與宣告不符」的情形——第五輪決議第一項的成立以「這種情形不會被
    系統拒絕」為前提，本探針即為驗證該前提。
    """
    os.makedirs(target, exist_ok=True)
    # 借用系統的 notepad.exe 當替身，比照 test-packaging-options.yml 的既有
    # 做法：這裡不需要這支 exe 真的能做什麼，只需要它是一個合法的 PE 檔案。
    import shutil
    shutil.copy(os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                             "System32", "notepad.exe"),
                os.path.join(target, "app.exe"))
    for name in ("tile.png", "small.png", "store.png"):
        write_png(os.path.join(target, name), icon_size, icon_size)
    with open(os.path.join(target, "AppxManifest.xml"), "w", encoding="utf-8") as f:
        f.write(MANIFEST.format(identity=identity, publisher=xml_escape(publisher)))
    return target


def xml_escape(value):
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


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
    from winrt.windows.management.deployment import PackageManager, RemovalOptions

    manager = PackageManager()
    operation = manager.remove_package_async(package_full_name, RemovalOptions.NONE)
    result = operation.get()
    return getattr(result, "error_text", "") or ""


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
        make_package_dir(args.target, args.identity, args.publisher, args.icon_size)
        print(f"已造出套件目錄 {args.target}（圖示實際尺寸 {args.icon_size}x{args.icon_size}）")
        return 0

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

    if ok:
        print("    結果：部署成功——但這個案例預期會被拒絕，前提不成立")
        return 1
    print("    結果：被拒絕（符合預期）")
    return 0


def is_elevated():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return "無法判斷"


if __name__ == "__main__":
    sys.exit(main())
