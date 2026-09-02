"""
messages.py
------------
共用的訊息翻譯機制：查表、語言退回、參數代入。

## 為什麼需要這支模組

`install_engine.py` 先前自己有一套 `translate()` 與語言常數（第十四輪決議
第七項）。錯誤彈窗裡的訊息實際上來自四個模組——`packaging_core`（欄位驗證）、
`msix_settings`（MSIX 區塊）、`png_size`（圖示尺寸與格式）、`cert_subject`
（讀憑證失敗），因此四個模組都要支援雙語。再複製三份 `translate()`，等於
同一段邏輯有四個版本，其中三份日後不會被修到。

## 為什麼不是一張集中的大表

這裡提供的是**機制**，表由各模組自己持有。訊息留在使用它的模組裡：
`png_size` 的訊息只有 `png_size` 知道什麼時候該說、說的是哪一件事。集中成
一張表之後，改一則訊息要跳到另一個檔案，而「這則訊息還有沒有人在用」也不再
看得出來。

跨模組的一致性（每個語言的鍵集合必須相同）由 `tests/test_message_tables.py`
統一檢查——那件事需要一個知道所有表的地方，但那個地方是測試，不是產品程式碼。

## 為什麼什麼都不拋

語言標籤來自系統設定或命令列旗標，鍵來自呼叫端，兩者都可能是任何值。為了
顯示層的問題中止建置沒有道理——使用者要的是安裝檔。查不到的鍵回傳鍵本身
（畫面上出現一串看得懂的識別字，比出現一片空白更容易查出漏了什麼），參數
對不上時保留原始的佔位符。
"""

LANGUAGES = ("zh-TW", "en")
DEFAULT_LANGUAGE = "zh-TW"


def translate(table, key, lang=DEFAULT_LANGUAGE, **params):
    """從 `table` 取出 `key` 對應的訊息。

    退回順序：指定的語言 → 預設語言 → 鍵本身。逐鍵退回而不是整張表退回：
    某個語言少了一則訊息時，只有那一則會變成中文，其餘仍是該語言。
    """
    localized = table.get(lang) or {}
    fallback = table.get(DEFAULT_LANGUAGE) or {}
    text = localized.get(key)
    if text is None:
        text = fallback.get(key)
    if text is None:
        return key
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        # 佔位符與參數對不上時保留原文。顯示 `{name}` 這種東西不好看，但
        # 比讓整個驗證流程因為一個字串而中斷好——而且那個佔位符本身就指出
        # 了是哪一則訊息出問題。
        return text


def missing_keys(table):
    """各語言之間缺了哪些鍵，回傳 `{語言: {缺少的鍵}}`；一致時回傳空字典。

    供測試使用。「多出來的鍵」同樣算漂移：那則訊息在另一個語言下不存在，
    使用該語言的人會看到中文（或鍵本身）。
    """
    every_key = set()
    for lang in table:
        every_key |= set(table[lang])
    result = {}
    for lang in table:
        gap = every_key - set(table[lang])
        if gap:
            result[lang] = gap
    return result
