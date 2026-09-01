"""msix_manifest.py 的測試：產生 AppxManifest.xml。

對應 docs/proposals/MSIX輸出規劃.md 第五輪決議（欄位來源）、第六輪查證
（最低版本與應用程式項目數量）、第九輪（欄位命名）、第十二輪定案決議
（宣告形式），以及 docs/adr/0007（套件身分名稱）與 docs/adr/0010
（關聯圖示與一副檔名一群組）。

**每個測試都把產出交給 XML 解析器**，不用字串比對判斷結構。理由是跳脫
錯誤的表現形式是「產出不是合法的 XML」，而字串比對抓不到那種錯——實測
已知發行者字串會包含雙引號（公司名含逗號時 Windows 的形式為
`C=TW, CN="Foo, Inc."`），那正是會壞掉的輸入。
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msix_manifest

NS = {
    "": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
    "uap5": "http://schemas.microsoft.com/appx/manifest/uap/windows10/5",
    "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
}


def render(**overrides):
    """一份最小但完整的輸入，測試各自覆蓋它關心的部分。"""
    kwargs = {
        "identity_name": "MyCompany.MyApp",
        "certificate_subject": "CN=My Company",
        "version": "1.2.3",
        "app_name": "My App",
        "publisher": "My Company",
        "main_exe": "app.exe",
    }
    kwargs.update(overrides)
    return msix_manifest.render(**kwargs)


def parse(xml_text):
    """解析產出。解析失敗即為測試失敗——那正是跳脫錯誤的表現形式。"""
    return ET.fromstring(xml_text)


def find(root, path):
    return root.find(path, NS)


def findall(root, path):
    return root.findall(path, NS)


class SkeletonTest(unittest.TestCase):
    def test_the_output_is_well_formed_xml(self):
        parse(render())

    def test_identity_carries_the_four_required_values(self):
        identity = find(parse(render()), "Identity")
        self.assertEqual(identity.get("Name"), "MyCompany.MyApp")
        self.assertEqual(identity.get("Publisher"), "CN=My Company")
        self.assertEqual(identity.get("ProcessorArchitecture"), "x64")

    def test_version_is_padded_to_four_parts(self):
        """第二輪決議第十項：三段自動補四段。"""
        identity = find(parse(render(version="1.2.3")), "Identity")
        self.assertEqual(identity.get("Version"), "1.2.3.0")

    def test_target_device_family_uses_the_verified_default(self):
        """第六輪查證結果第一項：10.0.17763.0（Windows 10 1809）。"""
        family = find(parse(render()), "Dependencies/TargetDeviceFamily")
        self.assertEqual(family.get("Name"), "Windows.Desktop")
        self.assertEqual(family.get("MinVersion"), "10.0.17763.0")

    def test_min_version_can_be_overridden(self):
        family = find(parse(render(min_windows_version="10.0.19041.0")),
                      "Dependencies/TargetDeviceFamily")
        self.assertEqual(family.get("MinVersion"), "10.0.19041.0")

    def test_full_trust_capability_is_declared(self):
        capability = find(parse(render()), "Capabilities/rescap:Capability")
        self.assertEqual(capability.get("Name"), "runFullTrust")

    def test_the_application_uses_the_pre_2004_activation_form(self):
        """第六輪查證結果第一項的連帶約束：uap10:RuntimeBehavior／TrustLevel
        需要 Windows 10 2004，最低版本為 1809 時必須改用 EntryPoint 的舊寫法，
        否則啟動資訊不完整而導致安裝失敗。"""
        app = find(parse(render()), "Applications/Application")
        self.assertEqual(app.get("Executable"), "app.exe")
        self.assertEqual(app.get("EntryPoint"), "windows.fullTrustApplication")

    def test_the_application_id_is_a_fixed_value_not_derived_from_app_name(self):
        """第五輪決議第四項：由工具填入固定值。變更此值會使使用者釘選於
        工作列的捷徑失效，因此不能隨顯示名稱變動。"""
        first = find(parse(render(app_name="One")), "Applications/Application").get("Id")
        second = find(parse(render(app_name="Another Name")), "Applications/Application").get("Id")
        self.assertEqual(first, second)


class EscapingTest(unittest.TestCase):
    """第十二輪定案決議第六項：每個填值點強制跳脫。"""

    def test_a_publisher_containing_quotes_and_commas_survives(self):
        """實測的真實形式：公司名含逗號時 Windows 用雙引號包住整個值。"""
        subject = 'C=TW, CN="Foo, Inc."'
        identity = find(parse(render(certificate_subject=subject)), "Identity")
        self.assertEqual(identity.get("Publisher"), subject)

    def test_xml_special_characters_in_the_app_name_survive(self):
        name = 'Tom & Jerry <the "best"> app'
        visual = find(parse(render(app_name=name)),
                      "Applications/Application/uap:VisualElements")
        self.assertEqual(visual.get("DisplayName"), name)

    def test_special_characters_in_the_publisher_display_name_survive(self):
        publisher = "A & B <Ltd>"
        value = find(parse(render(publisher=publisher)), "Properties/PublisherDisplayName")
        self.assertEqual(value.text, publisher)


class FileAssociationTest(unittest.TestCase):
    """ADR-0010：一個副檔名一個群組，圖示掛在群組上。"""

    def test_no_associations_means_no_extension_block(self):
        app = find(parse(render()), "Applications/Application")
        self.assertIsNone(find(app, "Extensions/uap:Extension"))

    def test_each_extension_gets_its_own_group(self):
        root = parse(render(file_associations=[".alpha", ".beta"]))
        groups = findall(root, "Applications/Application/Extensions/uap:Extension/uap:FileTypeAssociation")
        self.assertEqual(len(groups), 2)

    def test_group_names_are_derived_from_the_extension_in_lower_case(self):
        """群組名稱的格式限制是全小寫、無空白、1 到 64 字元。"""
        root = parse(render(file_associations=[".Locked"]))
        group = find(root, "Applications/Application/Extensions/uap:Extension/uap:FileTypeAssociation")
        self.assertEqual(group.get("Name"), "locked")

    def test_the_extension_itself_keeps_its_original_form(self):
        root = parse(render(file_associations=[".alpha"]))
        file_type = find(root, "Applications/Application/Extensions/uap:Extension/"
                               "uap:FileTypeAssociation/uap:SupportedFileTypes/uap:FileType")
        self.assertEqual(file_type.text, ".alpha")

    def test_per_extension_icons_land_on_their_own_group(self):
        root = parse(render(
            file_associations=[".alpha", ".beta"],
            doc_icons={".alpha": "doc_alpha.png", ".beta": "doc_beta.png"},
        ))
        logos = {
            group.get("Name"): find(group, "uap:Logo").text
            for group in findall(root, "Applications/Application/Extensions/uap:Extension/uap:FileTypeAssociation")
        }
        self.assertEqual(logos, {"alpha": "doc_alpha.png", "beta": "doc_beta.png"})

    def test_a_shared_icon_applies_to_every_group(self):
        root = parse(render(file_associations=[".alpha", ".beta"], doc_icon="doc.png"))
        logos = [find(g, "uap:Logo").text for g in findall(
            root, "Applications/Application/Extensions/uap:Extension/uap:FileTypeAssociation")]
        self.assertEqual(logos, ["doc.png", "doc.png"])

    def test_a_per_extension_icon_wins_over_the_shared_one(self):
        root = parse(render(
            file_associations=[".alpha", ".beta"], doc_icon="shared.png",
            doc_icons={".alpha": "own.png"},
        ))
        logos = {
            g.get("Name"): find(g, "uap:Logo").text
            for g in findall(root, "Applications/Application/Extensions/uap:Extension/uap:FileTypeAssociation")
        }
        self.assertEqual(logos, {"alpha": "own.png", "beta": "shared.png"})

    def test_no_icon_at_all_means_no_logo_element(self):
        root = parse(render(file_associations=[".alpha"]))
        group = find(root, "Applications/Application/Extensions/uap:Extension/uap:FileTypeAssociation")
        self.assertIsNone(find(group, "uap:Logo"))


class ExecutionAliasTest(unittest.TestCase):
    """第十二輪定案決議第五項與第六輪查證結果第二項。"""

    def test_no_alias_when_add_to_path_is_off(self):
        root = parse(render(add_to_path=False, path_target_exe="app.exe"))
        self.assertEqual(findall(root, ".//uap5:AppExecutionAlias"), [])

    def test_the_alias_sits_on_the_main_application_when_the_target_is_the_main_exe(self):
        root = parse(render(add_to_path=True, path_target_exe="app.exe"))
        apps = findall(root, "Applications/Application")
        self.assertEqual(len(apps), 1)
        alias = find(root, ".//uap5:AppExecutionAlias/uap5:ExecutionAlias")
        self.assertEqual(alias.get("Alias"), "app.exe")

    def test_a_different_target_gets_its_own_application_entry(self):
        """第六輪查證結果第二項：別名啟動的是其所屬應用程式項目自身的執行檔，
        無法指向另一支，因此兩者不同時需要兩個項目。"""
        root = parse(render(add_to_path=True, path_target_exe="cli.exe"))
        apps = findall(root, "Applications/Application")
        self.assertEqual(len(apps), 2)
        executables = [a.get("Executable") for a in apps]
        self.assertEqual(executables, ["app.exe", "cli.exe"])

    def test_the_second_entry_is_hidden_from_the_start_menu(self):
        """第六輪查證結果第二項未涵蓋的細節：第二個項目預設會出現在開始
        功能表，使終端使用者多出一個未要求的項目。"""
        root = parse(render(add_to_path=True, path_target_exe="cli.exe"))
        second = findall(root, "Applications/Application")[1]
        visual = find(second, "uap:VisualElements")
        self.assertEqual(visual.get("AppListEntry"), "none")

    def test_the_alias_is_on_the_second_entry_not_the_first(self):
        root = parse(render(add_to_path=True, path_target_exe="cli.exe"))
        apps = findall(root, "Applications/Application")
        self.assertIsNone(find(apps[0], "Extensions/uap5:Extension"))
        alias = find(apps[1], ".//uap5:ExecutionAlias")
        self.assertEqual(alias.get("Alias"), "cli.exe")

    def test_both_application_ids_are_fixed_and_distinct(self):
        root = parse(render(add_to_path=True, path_target_exe="cli.exe"))
        ids = [a.get("Id") for a in findall(root, "Applications/Application")]
        self.assertEqual(len(set(ids)), 2)


class LocalizedDisplayNameTest(unittest.TestCase):
    """第十二輪定案決議第二項。"""

    def test_without_display_names_the_literal_app_name_is_used(self):
        root = parse(render(app_name="My App"))
        self.assertEqual(find(root, "Properties/DisplayName").text, "My App")
        visual = find(root, "Applications/Application/uap:VisualElements")
        self.assertEqual(visual.get("DisplayName"), "My App")

    def test_without_display_names_a_single_language_is_declared(self):
        resources = findall(parse(render()), "Resources/Resource")
        self.assertEqual(len(resources), 1)

    def test_with_display_names_the_manifest_references_the_resource(self):
        root = parse(render(
            display_names={"zh-TW": "我的應用程式", "en-US": "My App"},
            default_language="zh-TW",
        ))
        self.assertTrue(find(root, "Properties/DisplayName").text.startswith("ms-resource:"))
        visual = find(root, "Applications/Application/uap:VisualElements")
        self.assertTrue(visual.get("DisplayName").startswith("ms-resource:"))

    def test_every_declared_language_appears_in_resources(self):
        root = parse(render(
            display_names={"zh-TW": "我的應用程式", "en-US": "My App"},
            default_language="zh-TW",
        ))
        languages = [r.get("Language") for r in findall(root, "Resources/Resource")]
        self.assertEqual(sorted(languages), sorted(["zh-TW", "en-US"]))

    def test_the_default_language_is_declared_first(self):
        """清單中 <Resource> 的第一筆即為預設語言。"""
        root = parse(render(
            display_names={"en-US": "My App", "zh-TW": "我的應用程式"},
            default_language="zh-TW",
        ))
        languages = [r.get("Language") for r in findall(root, "Resources/Resource")]
        self.assertEqual(languages[0], "zh-TW")


class ResourceSourcesTest(unittest.TestCase):
    """多語系名稱的 .resw 來源檔。"""

    def setUp(self):
        import tempfile
        import shutil
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_one_resw_per_language(self):
        msix_manifest.write_resource_sources(
            self.tmp, {"zh-TW": "我的應用程式", "en-US": "My App"})
        for lang in ("zh-TW", "en-US"):
            path = os.path.join(self.tmp, "strings", lang, "Resources.resw")
            self.assertTrue(os.path.isfile(path), f"缺少 {lang} 的資源檔")

    def test_the_resw_is_well_formed_and_carries_the_name(self):
        msix_manifest.write_resource_sources(self.tmp, {"en-US": 'A & B "X"'})
        path = os.path.join(self.tmp, "strings", "en-US", "Resources.resw")
        with open(path, encoding="utf-8") as f:
            root = ET.fromstring(f.read())
        value = root.find("./data/value")
        self.assertEqual(value.text, 'A & B "X"')


if __name__ == "__main__":
    unittest.main()
