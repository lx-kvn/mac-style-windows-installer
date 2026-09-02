"""
install_engine.py
------------------
打包時二選一的兩種「安裝檔內部運作方式」——傳統引擎與 MSIX 引擎——以及
「這份設定跟選定的引擎相不相容」的判斷。詞彙見 `CONTEXT.md`「傳統引擎與
MSIX 引擎」與「安裝路徑與使用者範圍」兩節。

本模組只回答「哪些設定在這個引擎下能用」，不負責產生 MSIX 套件本身——
套件的組裝與打包在 `msix_package.py`，部署在 `msix_deploy.py`。

## 四類分類

現有功能在 MSIX 下分為四類，出處為
`docs/proposals/MSIX輸出規劃.md` 第二輪決議第七項，第五輪補上第四類，
第七輪逐項重新檢查後修正歸屬：

- **第一類（無阻礙）**——MSIX 原生支援，通過。不列在下面的表裡。
- **第二類（可對應，需另行設計）**——`UNSUPPORTED`。第一版報錯，語氣是
  「目前尚未支援」，意思是等本工具補上。
- **第三類（格式本身不允許）**——`IMPOSSIBLE`。報錯，語氣是「MSIX 無法
  ⋯⋯，此為格式本身的限制」，意思是別等了、重新設計。
- **第四類（動機消失，設定無害失效）**——`MOOT`。不擋建置，只在建置訊息
  說明為何沒有作用。判準有兩個條件須同時成立：指令在 MSIX 下無法執行，
  且使用者設定它的目的已經以其他方式達成或不再存在。僅前者成立而後者
  不成立者屬第二或第三類——差別在於有無實質損失。

第二類與第三類的語氣必須可區分（第二輪決議第八項）：該區別決定下游專案
應等待本工具補上功能，或應立即重新設計其安裝流程。

## 一次列出全部違規項

不沿用 `packaging_core.validate_and_build_pack_data()` 既有的「第一個錯誤
即回傳」慣例（見 `docs/adr/0009` 決定四）。既有的欄位驗證回報的是「這個值
填錯了」，逐項修正即可；這裡回報的是「這份設定與這個引擎不相容」，而下游
專案需要判斷的是「切換引擎對本專案是否划算」——該判斷需要完整清單。逐條
回報會使對方每修正一項就重跑一次建置，且在看到最後一項之前無從得知總代價。

累積在本模組內部完成，`error_message()` 組出一則多行訊息後交給呼叫端，
`validate_and_build_pack_data()` 的回傳形狀因此不需變更——該函式為 GUI 與
CLI 共用。
"""
from collections import namedtuple

import messages

TRADITIONAL = "traditional"
MSIX = "msix"
ENGINES = (TRADITIONAL, MSIX)

SETTING_FIELD = "install_engine"

UNSUPPORTED = "unsupported"
IMPOSSIBLE = "impossible"
MOOT = "moot"


class UnknownEngine(Exception):
    """`install_engine` 填了不認得的值。

    攜帶訊息表的鍵與參數而非現成句子，理由同其他模組的例外。這一則在
    第十四輪 key 化時被漏掉了——它是唯一不在 check_settings() 路徑上的
    使用者可見訊息，因此沒被那次的清點涵蓋。
    """

    def __init__(self, key, **params):
        self.key = key
        self.params = params
        super().__init__(_t(key, **params))

    def localized(self, lang=messages.DEFAULT_LANGUAGE):
        return _t(self.key, lang, **self.params)


class Report(namedtuple("Report", "blocking notices")):
    """一次檢查的結果。

    `blocking` 是會擋下建置的違規項（第二、三類），`notices` 是不擋建置、
    只需要在建置訊息裡說明的項目（第四類）。兩者分開，因為呼叫端對它們的
    處置不同：前者組成錯誤訊息中止流程，後者印進建置紀錄後繼續。
    """

    __slots__ = ()

    @property
    def has_blocking(self):
        return bool(self.blocking)

    def error_message(self, lang=messages.DEFAULT_LANGUAGE):
        """把全部違規項組成一則多行訊息；沒有違規項時回傳空字串。

        分成兩段列出而不是混在一起：第二類與第三類要求下游專案採取的行動
        不同（等待 vs 重新設計），混列會讓對方必須逐條判讀每一句的語氣才
        分得出來。

        `lang` 不帶時是繁體中文，與訊息 key 化之前的行為一致。
        """
        if not self.blocking:
            return ""
        lines = [_t("error.intro", lang)]
        for category in (UNSUPPORTED, IMPOSSIBLE):
            items = [f for f in self.blocking if f.category == category]
            if not items:
                continue
            lines.append("")
            lines.append(_t(f"heading.{category}", lang))
            lines.extend(f"  - {_t(f.key, lang)}" for f in items)
        return "\n".join(lines)

    def notice_messages(self, lang=messages.DEFAULT_LANGUAGE):
        """第四類的說明句子。不擋建置，交給建置紀錄印出來。"""
        separator = _t("list.separator", lang)
        return [
            _t(f.key, lang, fields=separator.join(f.field.split(",")))
            for f in self.notices
        ]


# `key` 是訊息表的鍵，不是現成的句子——留著現成句子的話，呼叫端會直接印它，
# 翻譯就永遠只做了一半。第四類的 notice 借用同一個結構，其 field 是以逗號
# 相連的欄位清單（那一則訊息本來就同時談兩個欄位）。
Finding = namedtuple("Finding", "field category key")


def normalize(data):
    """讀出這次要用哪一種引擎。

    沒有這個欄位、或值為空，一律是傳統引擎——既有的設定檔沒有這個欄位，
    它們的行為必須完全不變。
    """
    value = (data.get(SETTING_FIELD) or "").strip().lower()
    if not value:
        return TRADITIONAL
    if value not in ENGINES:
        raise UnknownEngine("engine.unknown", field=SETTING_FIELD,
                            choices=" / ".join(ENGINES), value=value)
    return value


def _has_value(value):
    """設定有沒有被填。空字串、空清單、空字典都算沒填。"""
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


# 欄位與類別的對應。三份清單合而為一：GUI 需要一份「與這次填了什麼無關」
# 的靜態分類——就地標記的用途是事前告知，使用者還沒填就該看得到這個欄位在
# 這個模式下不能用。check_settings() 回答的是另一個問題（這份設定裡有哪些
# 違規），只列出實際被填了的欄位。
#
# 訊息本身以 key 取出，見下方 MESSAGES。訊息描述的是「使用者失去了什麼」，
# 不是「哪個欄位被拒絕」——後者從欄位名就看得出來，前者才是他要判斷的事。
_FIELD_CATEGORIES = {
    # 第二類：可對應，需另行設計。
    "dependencies": UNSUPPORTED,
    "custom_dependencies": UNSUPPORTED,
    "dependencies_min_version": UNSUPPORTED,
    "windows_service": UNSUPPORTED,
    "scheduled_task": UNSUPPORTED,
    # 使用者範圍。這一項與其他欄位相反，是「沒有填」才構成違規——預設的
    # Program Files 即 no_admin_install 為假，而第一版只提供當前使用者範圍
    # （見 docs/adr/0009）。判斷邏輯在 _user_scope_finding()。
    "no_admin_install": UNSUPPORTED,
    # 第三類：格式本身不允許，無替代方案。
    "custom_install_dir": IMPOSSIBLE,
    "pre_install_script": IMPOSSIBLE,
    "post_install_script": IMPOSSIBLE,
    "bundle_dependencies": IMPOSSIBLE,
    # 第四類：動機消失，設定無害失效。兩者的失效是同一件事的兩個位置——
    # local_appdata_files 的目的地路徑正是由 folder_name 組成（第七輪第二項）。
    "folder_name": MOOT,
    "local_appdata_files": MOOT,
}

_MOOT_PATH_FIELDS = ("folder_name", "local_appdata_files")

# 訊息表放在 Python 端而不是 config.html 的 i18n 表裡：CLI 沒有前端可以問，
# 訊息若只存在於前端，CLI 就沒有來源（第十四輪決議第七項）。
#
# 鍵的形式：
#   field.<欄位>      該欄位的完整說明，用於編譯時的違規清單
#   hint.<類別>       就地提示，只說類別不說細節（細節留給完整清單）
#   heading.<類別>    違規清單裡該類別的小標
#   error.intro       違規清單的開頭
#   notice.moot_paths 第四類的建置訊息，帶欄位清單參數
MESSAGES = {
    "zh-TW": {
        "error.intro": "這份設定與 MSIX 引擎不相容，以下項目需要處理：",
        "heading.unsupported": "MSIX 模式目前尚未支援（本工具日後可能補上）：",
        "heading.impossible": "MSIX 無法做到，此為格式本身的限制（需要重新設計安裝流程）：",
        "hint.unsupported": "MSIX 模式目前尚未支援這個設定，編譯時會被擋下。",
        "hint.impossible": "MSIX 無法做到，此為格式本身的限制，編譯時會被擋下。",
        "hint.moot": "這個設定在 MSIX 模式下不會有作用，也不需要。",
        "field.dependencies": "dependencies：安裝相依元件。MSIX 的做法是把執行階段檔案直接包進套件，與現行的下載後安裝不同，需另行設計。",
        "field.custom_dependencies": "custom_dependencies：自訂相依元件，與 dependencies 同。",
        "field.dependencies_min_version": "dependencies_min_version：相依元件的最低版本判定，隨相依元件一併未支援。",
        "field.windows_service": "windows_service：安裝為 Windows 服務。MSIX 有對應機制但限制較多，需另行設計。",
        "field.scheduled_task": "scheduled_task：排程工作。MSIX 的對應機制只涵蓋登入時觸發，其他觸發時機無對應。",
        "field.no_admin_install": "安裝給這台電腦上的所有使用者：MSIX 引擎目前只安裝給執行安裝的那一位使用者，其他使用者登入後不會有這個應用程式。若現在就需要所有使用者共用，請改用傳統引擎（install_engine 設為 traditional）。",
        "field.custom_install_dir": "custom_install_dir：指定安裝路徑。MSIX 套件的位置由系統決定，無法指定。",
        "field.pre_install_script": "pre_install_script：安裝前執行腳本。MSIX 的容器模型不允許在部署過程中執行任意外部程式。",
        "field.post_install_script": "post_install_script：安裝後執行腳本，與 pre_install_script 同。",
        "field.bundle_dependencies": "bundle_dependencies：內嵌第三方安裝程式並靜默執行。MSIX 不允許在部署過程中執行外部安裝程式。",
        "field.folder_name": "folder_name：安裝資料夾名稱。MSIX 套件的位置由系統決定，不存在使用者會操作的安裝資料夾。",
        "field.local_appdata_files": "local_appdata_files：個別檔案改裝到使用者目錄。MSIX 套件本來就安裝於使用者層級，這個設定原本的目的已經成立。",
        "notice.moot_paths": "{fields} 在 MSIX 引擎下不會有作用，也不需要：套件的位置由系統決定，不存在使用者會操作的安裝資料夾。設定 local_appdata_files 的目的（讓命令列工具不需提權即可執行）在 MSIX 下本來就成立，因為套件本來就安裝於使用者層級。若 path_target_exe 指向其中一個檔案，加入 PATH 的會是套件內的執行檔。",
        "engine.unknown": "{field} 只能是 {choices}，收到的是「{value}」。",
        "list.separator": "、",
    },
    "en": {
        "error.intro": "This configuration is not compatible with the MSIX engine. The following need attention:",
        "heading.unsupported": "Not supported by MSIX mode yet (this tool may add it later):",
        "heading.impossible": "MSIX cannot do this — it is a limitation of the format itself (the install flow needs redesigning):",
        "hint.unsupported": "MSIX mode does not support this setting yet; the build will be blocked.",
        "hint.impossible": "MSIX cannot do this — a limitation of the format itself; the build will be blocked.",
        "hint.moot": "This setting has no effect under MSIX mode, and is not needed.",
        "field.dependencies": "dependencies: installing runtime prerequisites. MSIX packs runtime files into the package itself rather than downloading and installing them, so this needs a different design.",
        "field.custom_dependencies": "custom_dependencies: custom prerequisites, same as dependencies.",
        "field.dependencies_min_version": "dependencies_min_version: minimum version checks for prerequisites, unsupported along with the prerequisites themselves.",
        "field.windows_service": "windows_service: installing as a Windows service. MSIX has an equivalent mechanism but with tighter limits, so this needs a different design.",
        "field.scheduled_task": "scheduled_task: scheduled tasks. The MSIX equivalent only covers logon triggers; other trigger types have no counterpart.",
        "field.no_admin_install": "Installing for every user on this machine: the MSIX engine currently installs only for the user running the installer, so other users will not have the application after signing in. If you need it shared across users today, use the traditional engine (set install_engine to traditional).",
        "field.custom_install_dir": "custom_install_dir: choosing the install path. The location of an MSIX package is decided by the system and cannot be specified.",
        "field.pre_install_script": "pre_install_script: running a script before installation. The MSIX container model does not allow arbitrary external programs to run during deployment.",
        "field.post_install_script": "post_install_script: running a script after installation, same as pre_install_script.",
        "field.bundle_dependencies": "bundle_dependencies: embedding third-party installers and running them silently. MSIX does not allow external installers to run during deployment.",
        "field.folder_name": "folder_name: the install folder name. The location of an MSIX package is decided by the system; there is no install folder for the user to work with.",
        "field.local_appdata_files": "local_appdata_files: redirecting individual files to the user directory. An MSIX package already installs at user level, so what this setting was for already holds.",
        "notice.moot_paths": "{fields}: no effect under the MSIX engine, and not needed. The package location is decided by the system, and there is no install folder for the user to work with. What local_appdata_files was for — letting a command-line tool run without elevation — already holds under MSIX, because the package installs at user level to begin with. If path_target_exe points at one of those files, what goes onto PATH is the executable inside the package.",
        "engine.unknown": "{field} must be one of {choices}; received \"{value}\".",
        "list.separator": ", ",
    },
}


def _t(key, lang=messages.DEFAULT_LANGUAGE, /, **params):
    """本模組訊息表的查表捷徑。機制在 messages.py，那裡也說明了為什麼
    訊息表留在各模組而不是集中一張。
    """
    return messages.translate(MESSAGES, key, lang, **params)


def field_categories():
    """回傳 `{欄位: 類別}`，與這次填了什麼無關。

    供 GUI 在使用者選定引擎的當下就地標出不相容的欄位（第十四輪決議第四
    項）。前端因此不需要自己維護一份欄位清單——那份清單一旦與這裡分岔，
    症狀是某個欄位悄悄不再被標記。
    """
    return dict(_FIELD_CATEGORIES)


def field_message(field, lang=messages.DEFAULT_LANGUAGE):
    """該欄位的完整說明；不是不相容欄位時回傳 None。"""
    if field not in _FIELD_CATEGORIES:
        return None
    return _t(f"field.{field}", lang)


def category_hint(category, lang=messages.DEFAULT_LANGUAGE):
    """就地提示：只說類別，不說細節。

    細節（失去什麼、有無替代方案）留在按下編譯時的完整清單裡——那本來就是
    它的職責，而欄位旁邊塞進一整段會把表單擠爆。
    """
    return _t(f"hint.{category}", lang)


def check_settings(engine, settings):
    """檢查一份設定與選定的引擎相不相容，回傳 Report。

    傳統引擎不受任何 MSIX 限制影響，直接回傳空結果。

    `settings` 吃的是使用者填的原始值，不是 packaging_core 補完預設值之後
    的結果：`folder_name` 沒填時會被補成 `app_name`，拿補完的值判斷會讓
    每一次 MSIX 建置都噴出一則使用者從未設定過的說明。
    """
    if engine != MSIX:
        return Report([], [])

    blocking = []
    # 使用者範圍與其他欄位相反：是「沒有填」才構成違規。預設的 Program Files
    # 即 no_admin_install 為假，而第一版只提供當前使用者範圍（docs/adr/0009）。
    if not settings.get("no_admin_install"):
        blocking.append(Finding("no_admin_install", UNSUPPORTED,
                                "field.no_admin_install"))
    for field, category in _FIELD_CATEGORIES.items():
        if field in ("no_admin_install",) or category == MOOT:
            continue
        if _has_value(settings.get(field)):
            blocking.append(Finding(field, category, f"field.{field}"))

    notices = []
    moot = [f for f in _MOOT_PATH_FIELDS if _has_value(settings.get(f))]
    if moot:
        notices.append(Finding(",".join(moot), MOOT, "notice.moot_paths"))
    return Report(blocking, notices)
