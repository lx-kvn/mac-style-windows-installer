"""restore_point.py
------------------
安裝前建立系統還原點，包裝 `srclient.dll` 的 `SRSetRestorePointW`。

best-effort：系統還原功能被停用、DLL 載入失敗、或呼叫本身失敗，一律回傳
False，不拋例外——這是加分項，不是安裝的關鍵路徑，不該擋住安裝繼續進行。

ctypes 呼叫明確宣告 struct 欄位跟 restype/argtypes（沿用
`explorer_lock_release.py` 處理 64-bit handle 時的既有慣例），避免同樣的
「沒宣告型別、ctypes 靜默失敗」的坑。`srclient_dll` 參數只給測試注入假的
DLL 物件用，跟 `restart_manager.py` 的 `rm_dll` 參數同一種 seam 風格。
"""
import ctypes
from ctypes import wintypes

_APPLICATION_INSTALL = 0
_BEGIN_SYSTEM_CHANGE = 100
_END_SYSTEM_CHANGE = 101
_MAX_DESC_W = 256


class _RESTOREPOINTINFOW(ctypes.Structure):
    _fields_ = [
        ("dwEventType", wintypes.DWORD),
        ("dwRestorePtType", wintypes.DWORD),
        ("llSequenceNumber", ctypes.c_int64),
        ("szDescription", wintypes.WCHAR * _MAX_DESC_W),
    ]


class _STATEMGRSTATUS(ctypes.Structure):
    _fields_ = [
        ("nStatus", wintypes.DWORD),
        ("llSequenceNumber", ctypes.c_int64),
    ]


def _srclient():
    dll = ctypes.WinDLL("srclient")
    dll.SRSetRestorePointW.restype = wintypes.BOOL
    dll.SRSetRestorePointW.argtypes = [
        ctypes.POINTER(_RESTOREPOINTINFOW), ctypes.POINTER(_STATEMGRSTATUS),
    ]
    return dll


def create_restore_point(description, srclient_dll=None):
    """建立一個系統還原點，回傳是否成功。兩階段呼叫：先用
    BEGIN_SYSTEM_CHANGE 開始，用回傳的 llSequenceNumber 接第二次
    END_SYSTEM_CHANGE 呼叫結束。任何一步失敗（含 DLL 載入失敗、呼叫例外）
    都回傳 False，不拋例外。
    """
    try:
        dll = srclient_dll if srclient_dll is not None else _srclient()

        begin_info = _RESTOREPOINTINFOW()
        begin_info.dwEventType = _BEGIN_SYSTEM_CHANGE
        begin_info.dwRestorePtType = _APPLICATION_INSTALL
        begin_info.llSequenceNumber = 0
        begin_info.szDescription = description[:_MAX_DESC_W - 1]

        begin_status = _STATEMGRSTATUS()
        if not dll.SRSetRestorePointW(ctypes.pointer(begin_info), ctypes.pointer(begin_status)):
            return False

        end_info = _RESTOREPOINTINFOW()
        end_info.dwEventType = _END_SYSTEM_CHANGE
        end_info.dwRestorePtType = _APPLICATION_INSTALL
        end_info.llSequenceNumber = begin_status.llSequenceNumber
        end_info.szDescription = description[:_MAX_DESC_W - 1]

        end_status = _STATEMGRSTATUS()
        return bool(dll.SRSetRestorePointW(ctypes.pointer(end_info), ctypes.pointer(end_status)))
    except Exception:
        return False
