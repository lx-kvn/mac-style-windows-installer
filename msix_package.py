"""
msix_package.py
----------------
把應用程式內容組裝成 MSIX 套件目錄，並呼叫 `makeappx` 打包成 `.msix`。

拆成兩個階段，理由是它們的性質不同、失敗方式也不同：

- **`stage()`** 只碰檔案系統：複製 app 內容、放圖示、寫清單、產生多語系
  資源來源檔。不呼叫任何外部工具，因此可以完整地單元測試。
- **`pack()`** 呼叫 `makepri`／`makeappx`。工具的檢索與子行程的執行都是
  可注入的參數（`find_tool`／`run`），比照 `file_assoc.py` 的 registry
  seam 與 `builder._sign_file()` 的作法，測試不需要真的有 SDK 工具。

這個拆法也對應第二輪決議第三項的兩截式流程：`stage()` 與 `pack()` 一起
構成「產出未簽章的 `.msix`」這個步驟，簽章由呼叫端處理，之後才是編
bootstrapper exe。

清單裡宣告的圖示檔名與實際複製進套件的檔名必須一致——那是一個會安靜漂移
的地方（清單指向不存在的檔案時，`makeappx` 不一定會擋，錯誤要到裝好之後
看到空白圖示才顯現）。因此檔名常數與命名規則都放在 `msix_manifest.py`，
兩邊共用同一份。
"""
import os
import shutil
import subprocess
from collections import namedtuple

import msix_manifest

# pack() 的 find_tool 預設回傳 sdk_tools.ToolLocation，測試注入的替身只需要
# 有 path 這個屬性。這個具名 tuple 供替身使用，也讓「這裡只用到 path」這件
# 事在型別上說清楚。
ToolPath = namedtuple("ToolPath", "path")

# 多語系資源的來源資料夾名稱。makepri 依這個結構收集各語言的字串。
STRINGS_DIR = "strings"
# makepri 的中間產物。它留在套件目錄裡會被 makeappx 一起打包進去，因此在
# 打包之前刪除。
PRICONFIG_NAME = "priconfig.xml"
RESOURCES_PRI_NAME = "resources.pri"


def stage(app_dir, staging_dir, png_icon, identity_name, certificate_subject,
          version, app_name, publisher, main_exe, icons=None, doc_icon="",
          doc_icons=None, file_associations=(), add_to_path=False,
          path_target_exe="", display_names=None, default_language=None,
          min_windows_version=None, description=None):
    """組出一個可以交給 `makeappx` 的目錄，回傳該目錄的路徑。

    `staging_dir` 若已存在會先整個清空。上一次建置的殘留檔案會被打包進這
    一次的套件，而那種錯誤在產物上看不出來。

    `icons`：`{"tile": 路徑, "taskbar": 路徑, "store": 路徑}`，個別覆蓋。
    沒有覆蓋的位置一律使用 `png_icon` 同一份——第五輪決議第一項不做縮放，
    由 Windows 顯示時自行縮小。
    """
    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.copytree(app_dir, staging_dir)

    icons = icons or {}
    for key, name in (("tile", msix_manifest.TILE_LOGO),
                      ("taskbar", msix_manifest.TASKBAR_LOGO),
                      ("store", msix_manifest.STORE_LOGO)):
        shutil.copy(icons.get(key) or png_icon, os.path.join(staging_dir, name))

    file_associations = list(file_associations or [])
    doc_icons = doc_icons or {}
    # 圖示的複製與清單裡的宣告在同一個迴圈裡決定，兩者因此不可能對不起來。
    manifest_doc_icons = {}
    for extension in file_associations:
        source = doc_icons.get(extension)
        if not source:
            continue
        name = msix_manifest.association_logo_name(extension)
        shutil.copy(source, os.path.join(staging_dir, name))
        manifest_doc_icons[extension] = name

    manifest_doc_icon = ""
    if doc_icon and any(extension not in manifest_doc_icons for extension in file_associations):
        # 共用圖示只在真的有副檔名會用到它的時候才複製進去。
        manifest_doc_icon = msix_manifest.SHARED_ASSOCIATION_LOGO
        shutil.copy(doc_icon, os.path.join(staging_dir, manifest_doc_icon))

    xml = msix_manifest.render(
        identity_name=identity_name,
        certificate_subject=certificate_subject,
        version=version,
        app_name=app_name,
        publisher=publisher,
        main_exe=main_exe,
        description=description,
        min_windows_version=min_windows_version,
        display_names=display_names,
        default_language=default_language,
        file_associations=file_associations,
        doc_icon=manifest_doc_icon,
        doc_icons=manifest_doc_icons,
        add_to_path=add_to_path,
        path_target_exe=path_target_exe,
    )
    with open(os.path.join(staging_dir, "AppxManifest.xml"), "w", encoding="utf-8") as f:
        f.write(xml)

    if display_names:
        msix_manifest.write_resource_sources(staging_dir, display_names)
    return staging_dir


def _run_tool(run, tool_path, args, log, what):
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # errors="replace"：解碼失敗時 stdout/stderr 會變成 None，下方的 tail 變成
    # 空字串，使用者只看得到「XX 失敗」而沒有任何原因。詳見 docs/investigations/子行程輸出的解碼修正.md。
    result = run([tool_path] + list(args), creationflags=creationflags,
                 capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        tail = ((result.stdout or "") + "\n" + (result.stderr or ""))[-1500:]
        raise Exception(f"{what}失敗：\n{tail}")
    return result


def pack(staging_dir, output_path, find_tool=None, run=None, log=None,
         languages=None):
    """把組裝好的目錄打包成未簽章的 `.msix`，回傳輸出路徑。

    套件目錄底下有 `strings/` 時，先以 `makepri` 把多語系字串編成
    `resources.pri`——清單以 `ms-resource:` 參照該檔案，沒有它的話顯示名稱
    會直接顯示成 `ms-resource:AppDisplayName` 這串原始文字，而那個錯誤要到
    終端使用者裝好之後才看得到。因此 `makepri` 失敗一律中止，不繼續打包。
    """
    if find_tool is None:
        import sdk_tools
        find_tool = sdk_tools.find_tool
    run = run or subprocess.run

    def report(message):
        if log:
            log(message)

    strings_dir = os.path.join(staging_dir, STRINGS_DIR)
    if os.path.isdir(strings_dir):
        makepri = find_tool("makepri.exe")
        report(getattr(makepri, "describe", lambda: f"makepri：{makepri.path}")())
        priconfig = os.path.join(staging_dir, PRICONFIG_NAME)
        try:
            _run_tool(run, makepri.path, [
                "createconfig", "/cf", priconfig,
                "/dq", "_".join(languages or _languages_in(strings_dir)), "/o",
            ], log, "產生資源設定檔（makepri createconfig）")
            _run_tool(run, makepri.path, [
                "new", "/pr", staging_dir, "/cf", priconfig,
                "/of", os.path.join(staging_dir, RESOURCES_PRI_NAME), "/o",
            ], log, "編譯多語系資源（makepri new）")
        finally:
            # 不論成功失敗都要清掉：它是 makepri 的中間產物，留在目錄裡會被
            # makeappx 一起打包進最終的套件。
            if os.path.exists(priconfig):
                os.remove(priconfig)

    makeappx = find_tool("makeappx.exe")
    report(getattr(makeappx, "describe", lambda: f"makeappx：{makeappx.path}")())
    _run_tool(run, makeappx.path, [
        "pack", "/d", staging_dir, "/p", output_path, "/o",
    ], log, "打包 MSIX（makeappx pack）")
    return output_path


def _languages_in(strings_dir):
    """從 `strings/` 底下的資料夾名稱推出語言清單。

    以目錄結構而非另外傳一份清單為準，兩者若不一致，`makepri` 會編出一份
    與實際內容對不上的資源檔。
    """
    try:
        return sorted(
            name for name in os.listdir(strings_dir)
            if os.path.isdir(os.path.join(strings_dir, name))
        )
    except OSError:
        return []
