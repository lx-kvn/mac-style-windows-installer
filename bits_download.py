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

# 真實抓到的 bug：這幾個值原本是憑印象猜的，跟 Microsoft 官方 BG_JOB_STATE
# enum（bits.h）對不上——TRANSFERRED/TRANSIENT_ERROR 被對調，導致下載成功
# 被誤判成錯誤（呼叫 job.Cancel() 把已下載的內容丟掉，重新用 urllib 下載
# 一次），下載途中的暫時性錯誤反而被誤判成成功（執行一個被截斷的 .exe）。
# 見 https://learn.microsoft.com/en-us/windows/win32/api/bits/ne-bits-bg_job_state
_BG_JOB_STATE_TRANSFERRING = 2
_BG_JOB_STATE_ERROR = 4
_BG_JOB_STATE_TRANSIENT_ERROR = 5
_BG_JOB_STATE_TRANSFERRED = 6


def _default_bcm_factory():
    import win32com.client
    return win32com.client.Dispatch("BackgroundCopyManager.1")


def download_via_bits(url, dest_path, on_progress=None, bcm_factory=None, poll_interval=0.5,
                       max_wait_seconds=300):
    """透過 BITS 下載 url 到 dest_path，回傳是否成功。pywin32 沒裝、或
    BITS 呼叫本身失敗（含逾時、被使用者取消等各種狀態），一律回傳 False，
    不拋例外——呼叫端要自己決定退回原本的下載方式。

    on_progress(percent: int)：下載中跟完成時都會呼叫，percent 是
    0-100 的整數。

    真實抓到的 bug：輪詢迴圈原本是 while True，沒有任何時間上限——一個卡在
    QUEUED/SUSPENDED 狀態的 job（例如網路完全斷線、BITS 服務被系統管理員
    停用）會讓這個函式永遠不回傳，整個安裝流程的 UI 卡死。它取代的 urllib
    下載邏輯原本有 timeout=30，不能因為換成 BITS 就倒退成沒有上限。
    max_wait_seconds 預設 300 秒（比 urllib 的單次 timeout=30 寬鬆，因為
    BITS 常見的低優先權背景傳輸模式本來就比較慢，不該太早放棄）。
    """
    try:
        bcm = (bcm_factory or _default_bcm_factory)()
        job = bcm.CreateJob(f"mswi_dep_download_{int(time.time())}", _BG_JOB_TYPE_DOWNLOAD)
        job.AddFile(url, dest_path)
        job.Resume()

        deadline = time.monotonic() + max_wait_seconds
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
            if time.monotonic() >= deadline:
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
