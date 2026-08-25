"""
progress_report.py
-------------------
把「算好百分比/訊息、推到 pywebview 視窗前端某個 window.<callback> 全域
函式」這個原語收斂成單一實作。原本 installer_core.py（主安裝流程/相依
元件自動安裝，各自對應前端不同的進度條）跟 uninstall.py（解除安裝流程）
各自有一份逐位元組幾乎相同的 `_report_progress()`，只差前端 callback
名稱不同——收斂到這裡，呼叫端只傳 callback 名稱進來。

不吞呼叫端的例外之外的任何東西：window 可能還沒建立好（None）或已經
關閉（evaluate_js 拋例外），這兩種情況都是安裝/解除安裝流程本身不該
被打斷的正常情境，直接吞掉、不往外拋。
"""
import json


def report_progress(window, js_callback_name, percent, message):
    if not window:
        return
    safe_msg = json.dumps(message, ensure_ascii=False)
    try:
        window.evaluate_js(f"window.{js_callback_name}({percent}, {safe_msg})")
    except Exception:
        pass
