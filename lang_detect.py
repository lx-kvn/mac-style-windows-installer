"""
lang_detect.py
---------------
共用的系統語言偵測模組。

`gui_config.py`（打包工具，語言下拉選單的預設值用）跟 `installer_core.py`
（安裝檔，強制依系統語言自動偵測，不給使用者選）都呼叫這裡的
`detect_system_language()`，避免同一套「讀 Windows 系統語言」的邏輯在兩邊
各寫一份、以後修一次要改兩個地方。
"""

import ctypes


def _get_windows_locale_name():
    """取得目前使用者的 Windows UI 語言標籤（BCP-47 格式，例如 "zh-TW"、
    "en-US"）。取不到（非 Windows 平台、API 呼叫失敗等）回傳空字串。
    """
    try:
        buf = ctypes.create_unicode_buffer(85)  # LOCALE_NAME_MAX_LENGTH
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, len(buf)):
            return buf.value
    except Exception:
        pass
    return ""


def detect_system_language(supported, default):
    """依目前系統語言，從 `supported` 清單裡選出最合適的語言代碼。

    比對順序：完整標籤完全比對（例如系統回報 "zh-TW"，`supported` 裡也有
    "zh-TW"）→ 只比對 "-" 前的主語言代碼（例如系統回報 "en-US"，`supported`
    裡有 "en"，視為相符）→ 都對不到就回傳 `default`。
    """
    locale_name = _get_windows_locale_name()
    if not locale_name:
        return default

    if locale_name in supported:
        return locale_name

    primary = locale_name.split("-")[0].lower()
    for code in supported:
        if code.lower() == primary or code.split("-")[0].lower() == primary:
            return code

    return default
