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

TRADITIONAL = "traditional"
MSIX = "msix"
ENGINES = (TRADITIONAL, MSIX)

SETTING_FIELD = "install_engine"

UNSUPPORTED = "unsupported"
IMPOSSIBLE = "impossible"
MOOT = "moot"


class UnknownEngine(Exception):
    """`install_engine` 填了不認得的值。"""


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

    def error_message(self):
        """把全部違規項組成一則多行訊息；沒有違規項時回傳空字串。

        分成兩段列出而不是混在一起：第二類與第三類要求下游專案採取的行動
        不同（等待 vs 重新設計），混列會讓對方必須逐條判讀每一句的語氣才
        分得出來。
        """
        if not self.blocking:
            return ""
        lines = ["這份設定與 MSIX 引擎不相容，以下項目需要處理："]
        for category, heading in (
            (UNSUPPORTED, "MSIX 模式目前尚未支援（本工具日後可能補上）："),
            (IMPOSSIBLE, "MSIX 無法做到，此為格式本身的限制（需要重新設計安裝流程）："),
        ):
            items = [f for f in self.blocking if f.category == category]
            if not items:
                continue
            lines.append("")
            lines.append(heading)
            lines.extend(f"  - {f.message}" for f in items)
        return "\n".join(lines)


Finding = namedtuple("Finding", "field category message")


def normalize(data):
    """讀出這次要用哪一種引擎。

    沒有這個欄位、或值為空，一律是傳統引擎——既有的設定檔沒有這個欄位，
    它們的行為必須完全不變。
    """
    value = (data.get(SETTING_FIELD) or "").strip().lower()
    if not value:
        return TRADITIONAL
    if value not in ENGINES:
        raise UnknownEngine(
            f"{SETTING_FIELD} 只能是 {' 或 '.join(ENGINES)}，收到的是「{value}」。"
        )
    return value


def _has_value(value):
    """設定有沒有被填。空字串、空清單、空字典都算沒填。"""
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


# 第二類：可對應，需另行設計。訊息描述的是「使用者失去了什麼」，不是
# 「哪個欄位被拒絕」——後者從欄位名就看得出來，前者才是他要判斷的事。
_UNSUPPORTED_FIELDS = [
    ("dependencies", "dependencies：安裝相依元件。MSIX 的做法是把執行階段檔案直接包進套件，與現行的下載後安裝不同，需另行設計。"),
    ("custom_dependencies", "custom_dependencies：自訂相依元件，與 dependencies 同。"),
    ("dependencies_min_version", "dependencies_min_version：相依元件的最低版本判定，隨相依元件一併未支援。"),
    ("windows_service", "windows_service：安裝為 Windows 服務。MSIX 有對應機制但限制較多，需另行設計。"),
    ("scheduled_task", "scheduled_task：排程工作。MSIX 的對應機制只涵蓋登入時觸發，其他觸發時機無對應。"),
]

# 第三類：格式本身不允許，無替代方案。
_IMPOSSIBLE_FIELDS = [
    ("custom_install_dir", "custom_install_dir：指定安裝路徑。MSIX 套件的位置由系統決定，無法指定。"),
    ("pre_install_script", "pre_install_script：安裝前執行腳本。MSIX 的容器模型不允許在部署過程中執行任意外部程式。"),
    ("post_install_script", "post_install_script：安裝後執行腳本，與 pre_install_script 同。"),
    ("bundle_dependencies", "bundle_dependencies：內嵌第三方安裝程式並靜默執行。MSIX 不允許在部署過程中執行外部安裝程式。"),
]

# 第四類的兩個欄位共用一則說明。兩者的失效是同一件事的兩個位置——
# local_appdata_files 的目的地路徑正是由 folder_name 組成——分開講兩次
# 會讓使用者以為是兩個不相關的問題（第七輪第二項）。
_MOOT_PATH_FIELDS = ("folder_name", "local_appdata_files")


def _user_scope_finding(settings):
    """使用者範圍：MSIX 引擎第一版只提供「當前使用者」（見 docs/adr/0009）。

    訊息描述的損失是「這台電腦上其他使用者不會有這個應用程式」，不是
    「裝不到 Program Files」——後者對使用者不具意義，而且 MSIX 下本來就
    沒有使用者會操作的安裝資料夾（第八輪定案決議第一項）。

    附一句指向傳統引擎：這是暫時不做而非做不到，對方在等待期間有路可走。
    """
    if settings.get("no_admin_install"):
        return None
    return Finding(
        "no_admin_install", UNSUPPORTED,
        "安裝給這台電腦上的所有使用者：MSIX 引擎目前只安裝給執行安裝的那一位使用者，"
        "其他使用者登入後不會有這個應用程式。"
        "若現在就需要所有使用者共用，請改用傳統引擎（install_engine 設為 traditional）。",
    )


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
    scope = _user_scope_finding(settings)
    if scope:
        blocking.append(scope)
    for field, message in _UNSUPPORTED_FIELDS:
        if _has_value(settings.get(field)):
            blocking.append(Finding(field, UNSUPPORTED, message))
    for field, message in _IMPOSSIBLE_FIELDS:
        if _has_value(settings.get(field)):
            blocking.append(Finding(field, IMPOSSIBLE, message))

    notices = []
    moot = [f for f in _MOOT_PATH_FIELDS if _has_value(settings.get(f))]
    if moot:
        notices.append(
            f"{'、'.join(moot)} 在 MSIX 引擎下不會有作用，也不需要："
            "套件的位置由系統決定，不存在使用者會操作的安裝資料夾。"
            "設定 local_appdata_files 的目的（讓命令列工具不需提權即可執行）"
            "在 MSIX 下本來就成立，因為套件本來就安裝於使用者層級。"
            "若 path_target_exe 指向其中一個檔案，加入 PATH 的會是套件內的執行檔。"
        )
    return Report(blocking, notices)
