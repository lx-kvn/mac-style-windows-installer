"""
packaging_settings.py
----------------------
持久化「打包工具」（GUI 的 ConfigAPI／CLI 都共用）少數幾個使用者偏好
設定，目前只有一個：`workspace_dir`（使用者自訂的編譯工作目錄，覆蓋
packaging_core.default_workspace_dir() 這個保證可寫入的預設值——見該
函式的說明，源頭問題是這支工具打包成 exe 後如果裝在 Program Files，
一般權限執行時寫不進自己所在的資料夾，導致編譯/打包失敗）。

用一個通用的 key/value JSON 檔案存，不是每個設定各自寫一支函式，這樣
未來要記住第二個偏好設定時不用重新設計持久化機制。讀寫都是 best-effort：
檔案不存在、內容損壞、或寫入失敗（例如磁碟空間不足），一律當作「沒有
自訂設定」處理，不影響工具本身的其他功能。
"""
import json
import os


def settings_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "mac-style-windows-installer", "gui_settings.json")


def load_settings():
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(settings):
    path = settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
