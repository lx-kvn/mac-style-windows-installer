"""bits_download.py
------------------
用 BITS（Background Intelligent Transfer Service）下載相依元件安裝檔，
取代 `installer_core.py` 原本用 `urllib` 的線上下載邏輯，換取斷點續傳、
背景低優先權下載這些 BITS 特有的好處。

跟 `explorer_lock_release.py` 用 `Shell.Application` COM 物件是同一種
風格：選用依賴（pywin32），沒裝或呼叫失敗就 best-effort 回傳 False，讓
呼叫端自己決定要不要退回原本的下載方式（`installer_core.py` 目前是退回
`urllib`，維持行為對等）。`bcm_factory` 只給測試注入假的
`BackgroundCopyManager` 物件用。
"""
import time

_BG_JOB_TYPE_DOWNLOAD = 0
_BG_JOB_STATE_TRANSFERRING = 3
_BG_JOB_STATE_ERROR = 4
_BG_JOB_STATE_TRANSFERRED = 5
_BG_JOB_STATE_TRANSIENT_ERROR = 6


def _default_bcm_factory():
    import win32com.client
    return win32com.client.Dispatch("BackgroundCopyManager.1")


def download_via_bits(url, dest_path, on_progress=None, bcm_factory=None, poll_interval=0.5):
    """透過 BITS 下載 url 到 dest_path，回傳是否成功。pywin32 沒裝、或
    BITS 呼叫本身失敗（含逾時、被使用者取消等各種狀態），一律回傳 False，
    不拋例外——呼叫端要自己決定退回原本的下載方式。

    on_progress(percent: int)：下載中跟完成時都會呼叫，percent 是
    0-100 的整數。
    """
    try:
        bcm = (bcm_factory or _default_bcm_factory)()
        job = bcm.CreateJob(f"mswi_dep_download_{int(time.time())}", _BG_JOB_TYPE_DOWNLOAD)
        job.AddFile(url, dest_path)
        job.Resume()

        while True:
            state = job.GetState()
            if state == _BG_JOB_STATE_TRANSFERRED:
                job.Complete()
                if on_progress:
                    on_progress(100)
                return True
            if state in (_BG_JOB_STATE_ERROR, _BG_JOB_STATE_TRANSIENT_ERROR):
                try:
                    job.Cancel()
                except Exception:
                    pass
                return False
            if on_progress:
                progress = job.GetProgress()
                total = getattr(progress, "BytesTotal", 0) or 0
                transferred = getattr(progress, "BytesTransferred", 0) or 0
                if total:
                    on_progress(int(transferred / total * 100))
            time.sleep(poll_interval)
    except Exception:
        return False
