"""
file_extension.py
------------------
副檔名這個概念：怎麼正規化、什麼樣算合法，以及各處要用的那幾個名字。

## 為什麼需要這個模組

稽核 D2（見 `docs/investigations/MSIX稽核與缺陷修正.md`）：修正之前，副檔名
的規則散在四個地方各自實作——`packaging_core` 解析使用者輸入的清單、
`file_assoc.prog_id()` 推 ProgID、`builder.py` 推傳統引擎的內嵌圖示檔名、
`msix_manifest` 推套件清單的關聯群組名與套件內的圖示檔名。四處都沒有檢查
字元集，而 `msix_manifest.association_group_name()` 的註釋寫著「字元集的檢查
留在驗證階段」——專案裡不存在那個階段。

後果有兩種。其一，帶空白、非 ASCII、引號或超長的副檔名一路通到 `makeappx`，
在流程尾端失敗且錯誤訊息不指向副檔名欄位。其二，這個字串會被當成檔名使用
（`doc_<群組名>.png`、`doc_icon_<副檔名>.ico`），帶 `..\\` 的輸入會讓圖示被
複製到組裝目錄之外。

因此規則集中於此，推導函式一律先驗證再產出——推導是最後一道防線，驗證漏掉
時不該安靜地產出一個會被當成路徑使用的字串。

## 規則的來源

- **長度上限 64、全小寫、不含空白**——來自 Microsoft 對 `uap:FileTypeAssociation`
  的 `Name` 屬性的規定（「A string between 1 and 64 characters in length」、
  「must be all lower case characters with no spaces」）。本工具以副檔名去掉
  開頭的點作為該屬性的值（見 `msix_group()`），因此該規定直接落在副檔名上。
- **字元集限於英文字母、數字、句點、連字號、底線**——這一條是本工具自訂的，
  不是格式的規定。理由有二：官方文件未載明 `Name` 與 `uap:FileType` 的字元集，
  依推測放寬等於作出無人驗證的承諾；而這個字串同時會成為套件內與工作目錄裡的
  檔名，放行路徑分隔符與 Windows 檔名不允許的字元，等於讓設定值決定檔案寫到
  哪裡。實際會用到的副檔名（`.txt`、`.tar.gz`、`.7z`、`.my-type`）都在這個
  集合內。

## 這裡不做的事

不檢查「這個副檔名是不是已經被別的程式佔用」——那是安裝時的事，且答案會隨
使用者的機器而不同。也不檢查副檔名是否為系統保留（`.exe`、`.dll`）：Windows
允許為它們建立關聯，擋下等於替使用者作決定。
"""
import re

import messages

# 關聯群組名的長度上限。來源見模組說明。副檔名開頭的點不計入——它不是群組名
# 的一部分。
MAX_LENGTH = 64

# 允許的字元集。理由見模組說明；這是本工具自訂的限制，不是格式的規定。
_ALLOWED = re.compile(r"^[a-z0-9._-]+$")

# 全形逗號：中文輸入法下打出全形逗號是常態，把它當成分隔符不是猜測使用者的
# 意圖——沒有任何合法的副檔名含有逗號。
_SEPARATORS = (",", "，")


# 訊息表。機制在 messages.py，那裡也說明了為什麼表留在各模組而不是集中一張。
MESSAGES = {
    "zh-TW": {
        "empty": "副檔名不能是空的。",
        "lone_dot": "「{ext}」不是有效的副檔名：去掉開頭的點之後沒有剩下任何內容。",
        "too_long": "副檔名「{ext}」太長了：去掉開頭的點之後不能超過 {maximum} 個字元，目前是 {actual} 個。這是 MSIX 套件清單對關聯群組名的長度上限。",
        "charset": "副檔名「{ext}」含有不能使用的字元。只能用英文字母、數字、句點、連字號與底線——不能有空白、中文或其他符號。這個字串會成為套件清單裡的關聯群組名，也會成為圖示檔的檔名，因此限制比一般欄位嚴格。",
    },
    "en": {
        "empty": "A file extension cannot be empty.",
        "lone_dot": "\"{ext}\" is not a valid file extension: nothing remains once the leading period is removed.",
        "too_long": "The file extension \"{ext}\" is too long: without its leading period it may not exceed {maximum} characters, and it is currently {actual}. This is the length limit an MSIX manifest places on a file type association name.",
        "charset": "The file extension \"{ext}\" contains characters that cannot be used. Only letters, digits, periods, hyphens and underscores are allowed — no spaces, no non-ASCII characters, no other symbols. This string becomes the file type association name in the package manifest and the filename of the icon, so the rule is stricter than for an ordinary field.",
    },
}


def _t(key, lang=messages.DEFAULT_LANGUAGE, /, **params):
    return messages.translate(MESSAGES, key, lang, **params)


class InvalidExtension(Exception):
    """推導函式收到一個未通過驗證的副檔名。

    攜帶訊息表的鍵與參數而非現成句子，理由同 `png_size.NotAPng` 與
    `msix_settings.InvalidVersion`：留著現成句子的話，呼叫端會直接印它，
    翻譯就永遠只做了一半。

    這個例外代表的是程式流程的問題，不是使用者輸入的問題——正常路徑上，
    使用者的輸入在 `packaging_core` 就已經通過 `parse_list()`。它存在的
    理由是讓「驗證被繞過」這件事出聲，而不是安靜地產出一個會被當成路徑
    使用的字串。
    """

    def __init__(self, key, **params):
        self.key = key
        self.params = params
        super().__init__(_t(key, **params))

    def localized(self, lang=messages.DEFAULT_LANGUAGE):
        return _t(self.key, lang, **self.params)


def normalize(raw):
    """把使用者填的一項轉成標準形狀：去空白、轉小寫、補上開頭的點。

    只做形狀，不做判斷——判斷是 `validate()` 的事。兩者分開的理由是正規化
    的結果要能被顯示在錯誤訊息裡（使用者才認得出自己填的是哪一個），因此
    它必須對不合法的輸入也給得出結果。

    空字串維持空字串，不補成 `.`：那會產生一個看起來合法、實際無意義的
    副檔名。
    """
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    return value if value.startswith(".") else "." + value


def validate(ext, lang=messages.DEFAULT_LANGUAGE):
    """檢查一個已正規化的副檔名，通過回傳 None，否則回傳錯誤訊息。

    訊息一律指名是哪一個副檔名：一次填好幾個時，不指名等於要使用者自己
    逐一比對。
    """
    value = normalize(ext)
    if not value:
        return _t("empty", lang)
    body = value.lstrip(".")
    if not body:
        return _t("lone_dot", lang, ext=value)
    if len(body) > MAX_LENGTH:
        return _t("too_long", lang, ext=value, maximum=MAX_LENGTH, actual=len(body))
    if not _ALLOWED.match(body):
        return _t("charset", lang, ext=value)
    return None


def parse_list(raw, lang=messages.DEFAULT_LANGUAGE):
    """把使用者填的一整串副檔名解析成清單，回傳 `(清單, 錯誤訊息)`。

    一項不合法即整串失敗，不略過該項繼續：略過會產出一份與使用者填的內容
    不一致的關聯清單，而他不會知道少了哪一個。

    重複的項目收斂為一項並保留第一次出現的位置。同一個副檔名出現兩次會在
    套件清單裡產生兩個同名的關聯群組，使清單無效——而使用者重複填寫時的
    意圖顯然不是「要兩個」。
    """
    text = str(raw or "")
    for separator in _SEPARATORS[1:]:
        text = text.replace(separator, _SEPARATORS[0])

    seen = []
    for part in text.split(_SEPARATORS[0]):
        value = normalize(part)
        if not value:
            # 空項目（連續的逗號、結尾的逗號）是打字的產物，不是使用者要求
            # 的一個副檔名，直接略過。
            continue
        error = validate(value, lang)
        if error:
            return None, error
        if value not in seen:
            seen.append(value)
    return seen, None


def _checked(ext):
    """推導函式共用的前置檢查，回傳正規化後的副檔名。"""
    value = normalize(ext)
    if not value:
        raise InvalidExtension("empty")
    body = value.lstrip(".")
    if not body:
        raise InvalidExtension("lone_dot", ext=value)
    if len(body) > MAX_LENGTH:
        raise InvalidExtension("too_long", ext=value, maximum=MAX_LENGTH,
                               actual=len(body))
    if not _ALLOWED.match(body):
        raise InvalidExtension("charset", ext=value)
    return value


def prog_id(ext):
    """副檔名 -> 登錄表的 ProgID（傳統引擎）。

    格式 `AppFile<副檔名去掉所有句點>` 是對外契約：既有安裝寫進登錄表的就是
    這個字串，改了會使解除安裝清不掉自己寫過的項目。`file_assoc.py` 的
    `register()`／`unregister()` 都以它對齊（見 CONTEXT.md「檔案關聯」）。
    """
    return "AppFile" + _checked(ext).replace(".", "")


def msix_group(ext):
    """副檔名 -> MSIX 套件清單的關聯群組名。

    只去掉開頭的點，句點以外的內容原樣保留——`.tar.gz` 的群組名是 `tar.gz`。
    正規化已經處理了大小寫，這裡不再重複。
    """
    return _checked(ext).lstrip(".")


def msix_logo_name(ext):
    """副檔名 -> 該副檔名專屬的關聯圖示在 MSIX 套件內的檔名。

    比照 `traditional_icon_name()` 的既有慣例：每個副檔名各自複製一份固定
    命名的圖示，避免不同副檔名指向同名不同內容的來源檔案時互相覆蓋。
    """
    return f"doc_{msix_group(ext)}.png"


def traditional_icon_name(ext):
    """副檔名 -> 該副檔名專屬的文件圖示在安裝檔內嵌資源裡的檔名（傳統引擎）。

    這個檔名是打包端與安裝端之間的契約：`builder.py` 以這個名字內嵌，
    `installer_core.py` 以同一個名字取用。
    """
    return f"doc_icon_{_checked(ext).lstrip('.')}.ico"
