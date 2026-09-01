"""
msix_manifest.py
-----------------
產生 MSIX 套件清單（`AppxManifest.xml`），以及多語系顯示名稱所需的 `.resw`
來源檔。

## 為什麼是字串樣板

沿用 `version_info.py` 的既有先例——該模組產生 PyInstaller 的版本資訊檔，
作法即為程式碼內的字串樣板，其說明並記載了理由。同一專案中兩個「產生
結構化文字檔給外部工具讀取」的模組採用兩種做法，是維護上的負擔。

不採用獨立的範本檔案：本專案已多次因「打包時要一起帶走的檔案清單」未同步
而發生缺陷（`tests/test_shared_module_packaging.py` 整支測試即為此而寫），
多一個檔案即多一個會被遺漏的登記項目。

不採用 `xml.etree` 建構（雖然那能從根本消除跳脫問題）：MSIX 清單使用多個
命名空間前綴（`uap`、`uap5`、`rescap`），而 `IgnorableNamespaces` 屬性以
前綴名稱指涉它們，標準函式庫預設會將前綴改寫為 `ns0`、`ns1`，須另行設定
壓制。以一個持續存在的麻煩，換掉一個十來個填值點加一個測試即可涵蓋的問題，
不划算。

**因此跳脫是硬性要求，不是提醒。** 每一個填入樣板的值都必須通過
`escape()`。這不是理論上的風險：發行者字串實測會包含雙引號——公司名含
逗號時 Windows 的形式是 `C=TW, CN="Foo, Inc."`（見規劃文件第十輪 spike
結果），直接填入 XML 屬性會產生格式錯誤的清單，而 `makeappx` 的錯誤訊息
不會指向這個原因。

## 幾個不直覺的地方

- **啟動資訊用 `EntryPoint="windows.fullTrustApplication"` 這種舊寫法**，
  不用 `uap10:RuntimeBehavior`／`uap10:TrustLevel`。後者需要 Windows 10
  2004（build 19041），而本工具的最低版本預設值是 1809——在低於該版本的
  套件中使用它們，啟動資訊會不完整而導致安裝失敗（見規劃文件第六輪查證
  結果第一項）。
- **應用程式識別碼是固定值**，不由 `app_name` 推導。變更它會使使用者釘選
  於工作列的捷徑失效（第五輪決議第四項）。
- **命令列別名綁定在應用程式項目上**，別名啟動的是該項目自身的執行檔，
  無法指向另一支。因此 `path_target_exe` 與 `main_exe` 不同時需要兩個
  應用程式項目，且第二個必須設 `AppListEntry="none"`，否則終端使用者的
  開始功能表會多出一個他未要求的項目（第六輪查證結果第二項）。
- **檔案關聯的圖示掛在關聯群組上，不是掛在個別副檔名上**，因此每個副檔名
  各自成為一個群組（見 `docs/adr/0010`）。
"""
import os

# 應用程式識別碼。固定值，不由任何使用者可改的欄位推導——變更它會使使用者
# 釘選於工作列的捷徑失效（第五輪決議第四項）。第二個供「命令列工具與主程式
# 不是同一支」時使用。
MAIN_APPLICATION_ID = "App"
COMMAND_LINE_APPLICATION_ID = "CommandLine"

# 多語系顯示名稱的資源識別字，清單以 `ms-resource:` 加上它來參照。
DISPLAY_NAME_RESOURCE = "AppDisplayName"

# 套件內的圖示檔名。清單裡宣告的名稱與實際複製進套件的檔名必須一致，那是
# 一個會安靜漂移的地方（清單指向一個不存在的檔案，makeappx 不一定會擋），
# 因此固定在這裡，由清單產生與套件組裝共用同一份。
TILE_LOGO = "tile.png"
TASKBAR_LOGO = "small.png"
STORE_LOGO = "store.png"
SHARED_ASSOCIATION_LOGO = "doc.png"


def association_logo_name(extension):
    """某個副檔名專屬的關聯圖示在套件內的檔名。

    比照 `builder.py` 對 `doc_icon_<副檔名>.ico` 的既有慣例：每個副檔名各自
    複製一份固定命名的圖示，避免不同副檔名指向同名不同內容的來源檔案時互相
    覆蓋。
    """
    return f"doc_{association_group_name(extension)}.png"


DEFAULT_MIN_WINDOWS_VERSION = "10.0.17763.0"
# 「已測試到的最高版本」影響限於相容性提示，依工具自身的建置環境填入即可，
# 不開放設定（第五輪決議第二項末）。
DEFAULT_MAX_VERSION_TESTED = "10.0.26100.0"

_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:uap5="http://schemas.microsoft.com/appx/manifest/uap/windows10/5"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap uap5 rescap">
  <Identity Name="{identity_name}"
            Publisher="{publisher_subject}"
            Version="{version}"
            ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>{display_name}</DisplayName>
    <PublisherDisplayName>{publisher}</PublisherDisplayName>
    <Logo>{store_logo}</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop"
                        MinVersion="{min_windows_version}"
                        MaxVersionTested="{max_version_tested}" />
  </Dependencies>
  <Resources>
{resources}
  </Resources>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
  <Applications>
{applications}
  </Applications>
</Package>
"""

_APPLICATION = """    <Application Id="{app_id}" Executable="{executable}"
                 EntryPoint="windows.fullTrustApplication">
      <uap:VisualElements DisplayName="{display_name}"
                          Description="{description}"
                          BackgroundColor="transparent"
                          Square150x150Logo="{tile_logo}"
                          Square44x44Logo="{taskbar_logo}"{app_list_entry} />{extensions}
    </Application>"""

_FILE_ASSOCIATION = """        <uap:Extension Category="windows.fileTypeAssociation">
          <uap:FileTypeAssociation Name="{group}">
            <uap:DisplayName>{display_name}</uap:DisplayName>{logo}
            <uap:SupportedFileTypes>
              <uap:FileType>{extension}</uap:FileType>
            </uap:SupportedFileTypes>
          </uap:FileTypeAssociation>
        </uap:Extension>"""

_EXECUTION_ALIAS = """        <uap5:Extension Category="windows.appExecutionAlias">
          <uap5:AppExecutionAlias>
            <uap5:ExecutionAlias Alias="{alias}" />
          </uap5:AppExecutionAlias>
        </uap5:Extension>"""

_RESW = """<?xml version="1.0" encoding="utf-8"?>
<root>
  <data name="{name}" xml:space="preserve">
    <value>{value}</value>
  </data>
</root>
"""


def escape(value):
    """把值轉成可以安全填進 XML 屬性或元素內容的形式。

    屬性與元素內容共用同一個函式：多轉義幾個字元不會有副作用，而分成兩個
    函式會讓「這個填值點該用哪一個」變成每次都要判斷的事，判斷錯了產出的
    清單會壞掉而錯誤訊息不指向原因。
    """
    return (str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def association_group_name(extension):
    """副檔名 -> 關聯群組名稱。

    格式限制為 1 到 64 字元、全小寫、無空白。這裡只做去點與轉小寫，字元集
    的檢查留在驗證階段——在這裡靜默地把不合法的字元換掉，會產生一個與使用者
    輸入對不起來的群組名稱。
    """
    return str(extension).lstrip(".").lower()


def _quad_version(version):
    """三段以下補零至四段。實際的格式驗證在 msix_settings.to_quad_version()，
    這裡只做轉換，避免產生清單時才因為版本格式失敗。"""
    parts = [p for p in str(version).split(".") if p != ""]
    numbers = [int(p) if p.isdigit() else 0 for p in parts][:4]
    numbers += [0] * (4 - len(numbers))
    return ".".join(str(n) for n in numbers)


def _resolve_association_logo(extension, doc_icon, doc_icons):
    """決定某個副檔名的關聯圖示：自己的設定優先，其次是共用的那張。

    沒有任何設定時回傳 None——此時不產生 <uap:Logo> 元素，而不是產生一個
    空的。空元素會讓系統去找一個不存在的檔案。
    """
    own = (doc_icons or {}).get(extension)
    return own or doc_icon or None


def _render_applications(app_id_display_name, description, main_exe, tile_logo,
                         taskbar_logo, file_associations, doc_icon, doc_icons,
                         add_to_path, path_target_exe):
    target = (path_target_exe or "").strip()
    alias_on_main = add_to_path and (not target or target == main_exe)
    needs_second = add_to_path and target and target != main_exe

    main_extensions = []
    for extension in file_associations:
        logo = _resolve_association_logo(extension, doc_icon, doc_icons)
        main_extensions.append(_FILE_ASSOCIATION.format(
            group=escape(association_group_name(extension)),
            display_name=escape(app_id_display_name),
            extension=escape(extension),
            logo=f"\n            <uap:Logo>{escape(logo)}</uap:Logo>" if logo else "",
        ))
    if alias_on_main:
        main_extensions.append(_EXECUTION_ALIAS.format(alias=escape(main_exe)))

    applications = [_APPLICATION.format(
        app_id=MAIN_APPLICATION_ID,
        executable=escape(main_exe),
        display_name=escape(app_id_display_name),
        description=escape(description),
        tile_logo=escape(tile_logo),
        taskbar_logo=escape(taskbar_logo),
        app_list_entry="",
        extensions=_wrap_extensions(main_extensions),
    )]

    if needs_second:
        # AppListEntry="none"：這個項目的存在理由只是承載命令列別名，讓它
        # 出現在開始功能表等於在終端使用者的清單裡多一個他未要求的東西。
        applications.append(_APPLICATION.format(
            app_id=COMMAND_LINE_APPLICATION_ID,
            executable=escape(target),
            display_name=escape(app_id_display_name),
            description=escape(description),
            tile_logo=escape(tile_logo),
            taskbar_logo=escape(taskbar_logo),
            app_list_entry='\n                          AppListEntry="none"',
            extensions=_wrap_extensions([_EXECUTION_ALIAS.format(alias=escape(target))]),
        ))
    return "\n".join(applications)


def _wrap_extensions(blocks):
    if not blocks:
        return ""
    return "\n      <Extensions>\n" + "\n".join(blocks) + "\n      </Extensions>"


def render(identity_name, certificate_subject, version, app_name, publisher,
           main_exe, description=None, min_windows_version=None,
           max_version_tested=None, display_names=None, default_language=None,
           file_associations=(), doc_icon="", doc_icons=None,
           add_to_path=False, path_target_exe="",
           tile_logo=TILE_LOGO, taskbar_logo=TASKBAR_LOGO, store_logo=STORE_LOGO):
    """組出 `AppxManifest.xml` 的內容。

    `display_names` 有值時，顯示名稱改以 `ms-resource:` 參照——清單沒有內嵌
    多語言字串的機制，多語系只能靠 `makepri` 編出的 `resources.pri`
    （第十二輪定案決議第二項）。此時 `.resw` 來源檔由
    `write_resource_sources()` 產生。
    """
    localized = bool(display_names)
    if localized:
        display_value = f"ms-resource:{DISPLAY_NAME_RESOURCE}"
        languages = _ordered_languages(display_names, default_language)
    else:
        display_value = app_name
        languages = ["en-us"]

    resources = "\n".join(
        f'    <Resource Language="{escape(lang)}" />' for lang in languages
    )
    return _MANIFEST.format(
        identity_name=escape(identity_name),
        publisher_subject=escape(certificate_subject),
        version=escape(_quad_version(version)),
        display_name=escape(display_value),
        publisher=escape(publisher),
        store_logo=escape(store_logo),
        min_windows_version=escape(min_windows_version or DEFAULT_MIN_WINDOWS_VERSION),
        max_version_tested=escape(max_version_tested or DEFAULT_MAX_VERSION_TESTED),
        resources=resources,
        applications=_render_applications(
            app_id_display_name=display_value,
            description=description or app_name,
            main_exe=main_exe,
            tile_logo=tile_logo,
            taskbar_logo=taskbar_logo,
            file_associations=list(file_associations or []),
            doc_icon=doc_icon,
            doc_icons=doc_icons,
            add_to_path=add_to_path,
            path_target_exe=path_target_exe,
        ),
    )


def _ordered_languages(display_names, default_language):
    """預設語言排在第一個——清單中 <Resource> 的第一筆即為預設語言。"""
    languages = list(display_names.keys())
    if default_language and default_language in languages:
        languages.remove(default_language)
        languages.insert(0, default_language)
    return languages


def write_resource_sources(target_dir, display_names):
    """依 `makepri` 期望的資料夾結構寫出各語言的 `.resw`。

    結構是 `<套件目錄>/strings/<語言代碼>/Resources.resw`。

    注意套件目錄裡必須另有至少一張圖片被資源索引收錄，否則顯示名稱會直接
    顯示成 `ms-resource:AppDisplayName` 這串原始文字而非翻譯後的名稱，而該
    錯誤在打包階段不會顯現。本工具的套件目錄根層本來就有三張圖示，此條件
    自然成立——但那是條件成立的原因，不是巧合，移動圖示位置時要一併考慮。
    """
    for language, value in (display_names or {}).items():
        folder = os.path.join(target_dir, "strings", language)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "Resources.resw"), "w", encoding="utf-8") as f:
            f.write(_RESW.format(name=DISPLAY_NAME_RESOURCE, value=escape(value)))
    return target_dir
