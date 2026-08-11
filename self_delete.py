"""self_delete.py — `uninstall.exe` 解除安裝完成後，讓自己（正在執行中的
這個 exe）被刪掉這件事。

從 `uninstall.py` 拆出來的理由：這段 `.bat` 產生 + 重試邏輯已經踩過三輪
真實 bug（見 `_build_bat_script()`/`schedule_if_needed()` 的說明），
`tests/test_self_delete.py` 也已經直接測這裡產生的 `.bat` 內容跟 Popen
參數——測試早就把這裡當一個獨立單元在測，只是原本沒有自己的檔案。

原本 `uninstall.py` 的兩個呼叫端（`run_silent_uninstall()`／
`UninstallerAPI.finish_and_exit()`）都要自己先呼叫
`_should_schedule_self_delete(_is_upgrade_call(argv))` 判斷要不要排程，
這個前置條件不是函式介面的一部分，容易忘記檢查順序。`schedule_if_needed()`
把這個判斷收進來，呼叫端只要給 `argv`，不用自己記得先檢查。
"""

import os
import subprocess
import tempfile


def is_upgrade_call(argv):
    """是否由 `installer_core.py` 的 `run_upgrade_uninstall()`（更新覆蓋
    安裝流程）呼叫，而不是一般的（互動式或企業批次靜默）解除安裝。見
    `schedule_if_needed()` 的說明，這個旗標決定要不要完全跳過背景自我
    刪除指令。
    """
    return "--upgrade" in argv


def _should_schedule(is_upgrade):
    """決定要不要排出延遲執行的背景自我刪除指令（`ping` 製造延遲 + `del`
    刪除自己的 exe + 視情況 `rmdir /s /q` 整個安裝目錄）。

    真實抓到的 bug：這段指令是 fire-and-forget（`subprocess.Popen()`
    呼叫完立刻回傳，不等待），`installer_core.py` 的
    `run_upgrade_uninstall()` 是用 `subprocess.run()` **同步**呼叫這支
    舊版 uninstall.exe，行程一結束就繼續往下跑，這時候背景那個延遲約
    1 秒後才會真正執行的 `rmdir /s /q` 根本還沒發生。新版本安裝流程
    緊接著就會開始把檔案複製進同一個目錄——如果複製時間跨過那個延遲
    視窗，背景指令觸發時會把「整個資料夾」（含已經複製好的新檔案）
    砍掉，導致安裝程式回報成功、但實際檔案沒有複製完整，且只發生在
    更新覆蓋安裝（唯一會同時存在「舊版本背景自刪」跟「新版本複製檔案」
    競爭同一個資料夾的情境）。

    修法：`is_upgrade`（呼叫端帶了 `--upgrade` 旗標）為真時，完全不排
    這段背景指令——這支舊版 uninstall.exe 的行程本身已經確定執行完畢
    即將結束，它自己的 exe 檔案已經沒有任何行程占用，不需要靠延遲的
    背景指令才能刪除；`installer_core.py` 稍後本來就會把新版本自己的
    `uninstall.exe` 複製到同一個路徑蓋過去，資料夾本身也會被新版本
    重用，不需要也不該被刪除或重建。一般手動雙擊解除安裝、或不是被
    更新流程觸發的純靜默解除安裝，`is_upgrade` 是 False，行為不變。
    """
    return not is_upgrade


def _build_bat_script(current_dir, exe_path, safe_to_remove_whole_dir):
    """組出實際要寫到磁碟、拿去執行的 `.bat` 內容。

    真實抓到的 bug（第一輪）：原本只固定延遲約 1 秒（`ping -n 2`）就砍一次、
    不管成不成功——這個假設在純 console 程式上大致成立，但這支 exe 內嵌了
    WebView2 runtime，`window.destroy()` 之後行程真正結束（含 WebView2
    自己的輔助行程收尾）可能不只 1 秒，`del`/`rmdir` 失敗時又被 `&` 串接
    靜默吞掉、不會重試，導致解除安裝完成後常常沒有真的把自己刪掉。現在
    改成反覆重試 `del` 直到成功（最多 20 次、每次間隔約 1 秒，足夠涵蓋
    WebView2 關閉的合理延遲，又不會無限期占用背景行程），確認檔案真的
    刪除之後才視情況接著 `rmdir` 整個資料夾。

    真實抓到的 bug（第三輪，用「持有檔案控制代碼 5 秒後放開」實際重現才
    抓到）：第一輪的重試迴圈是用 `for /l %i in (1,1,20) do (del ... &
    if not exist (...) & ping ...)` 這種「整個迴圈主體包在一組括號裡」的
    寫法——cmd.exe 對這種括號內的複合指令是**一次性解析、整段當成同一個
    靜態區塊重複執行**（用 `echo %time%` 實測可以看到每一輪印出的時間
    完全相同），而 `if not exist` 這個條件雖然不是 `%` 變數、理論上應該
    每輪重新求值，但實測發現只要檔案曾經在某一輪判定為「還被鎖住」，
    之後就算鎖真的釋放了，同一個 `for /l` 迴圈後續每一輪依然持續回報
    失敗，直到 20 次全部跑完、迴圈結束，檔案跟資料夾整個沒被刪掉。改成
    寫一個暫存 `.bat` 檔案，用傳統的 `:retry` / `goto retry` 標籤式重試
    （不是包在同一組括號裡的 `for` 迴圈主體，而是每次 `goto` 跳回
    `:retry` 都是重新從那一行開始解析執行），同樣的情境下，實測一放開
    鎖就立刻在下一輪重試成功——這才是真正可靠的寫法。`.bat` 檔案本身在
    最後一行呼叫 `del /f /q "%~f0"` 自我刪除（cmd.exe 逐行讀取批次檔，
    執行到刪除自己那一行時，前面的內容早就讀進記憶體了，這是刪除批次檔
    的標準手法，只是拿來清掉這個暫時產生的 `.bat`，跟安裝目錄本身的
    刪除邏輯無關）。
    """
    if safe_to_remove_whole_dir:
        cleanup_line = f'cd .. & rmdir /s /q "{current_dir}"'
    else:
        cleanup_line = ""
    return (
        "@echo off\r\n"
        f'cd /d "{current_dir}"\r\n'
        "set retries=0\r\n"
        ":retry\r\n"
        f'del /f /q "{exe_path}" >nul 2>&1\r\n'
        f'if not exist "{exe_path}" goto success\r\n'
        "set /a retries=%retries%+1\r\n"
        "if %retries% geq 20 goto giveup\r\n"
        "ping 127.0.0.1 -n 2 >nul\r\n"
        "goto retry\r\n"
        ":success\r\n"
        f"{cleanup_line}\r\n"
        ":giveup\r\n"
        'del /f /q "%~f0"\r\n'
    )


def _write_bat_file(bat_path, content):
    """用系統 ANSI 編碼（mbcs）寫出 .bat 檔案——cmd.exe 依系統目前的
    OEM/ANSI 編碼逐位元組解析批次檔，這裡的編碼要跟它一致，安裝路徑裡的
    中文/日文等字元才能被正確寫入、正確解析。"""
    with open(bat_path, "w", encoding="mbcs") as f:
        f.write(content)


def _get_short_path(path):
    """回傳 path 的 8.3 短路徑名稱（純 ASCII，任何系統編碼都能正確表示）。
    路徑不存在、磁碟區沒有啟用短檔名產生等情況會取得失敗，回傳 None。"""
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    length = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    return buf.value if length else None


def schedule_if_needed(argv, current_dir, exe_path, safe_to_remove_whole_dir, log=None):
    """判斷要不要排程、真的排程，兩件事一起做——呼叫端只要給 `argv`，
    不用自己先呼叫 `is_upgrade_call()`/內部的排程判斷，前置條件收在
    這裡，不會有呼叫端忘記檢查的問題。

    真實抓到的 bug（`--noconsole` 編譯之後這支 exe 沒有主控台，
    stdin/stdout/stderr 是無效控制代碼）：`subprocess.Popen(...,
    shell=True)` 如果沒有明確指定 stdin/stdout/stderr，預設會嘗試繼承
    父行程的這幾個控制代碼——在有主控台的舊版 console 程式上這樣沒問題，
    但在 `--noconsole` 的行程裡繼承無效控制代碼會讓 `CreateProcess`
    直接失敗，`Popen()` 拋出例外（`OSError: [WinError 6] The handle is
    invalid` 之類），整個自我刪除背景指令根本沒有真的被排上去。這裡
    明確指定 `stdin=stdout=stderr=subprocess.DEVNULL`。

    真實抓到的 bug（F17）：`.bat` 內容固定用 `encoding="mbcs"`（系統目前
    的 ANSI 編碼）寫入，如果安裝路徑含有系統目前 locale 編碼無法表示的
    字元（例如簡體中文 Windows 上裝了一個路徑含繁體中文特殊字、或日文/
    韓文路徑），`open(..., encoding="mbcs").write()` 會丟
    `UnicodeEncodeError`，原本整段被最外層 `try/except Exception: pass`
    吞掉，`uninstall.exe` 就這樣永遠不會被排程自我刪除，而且完全沒有任何
    記錄可以事後追查。修法：先試著取得 current_dir/exe_path 的 8.3 短
    路徑名稱（純 ASCII，任何系統編碼都寫得進去），改用短路徑重新組一份
    `.bat` 再試一次；短路徑也拿不到的話才真的放棄，而且不管走哪個分支，
    都透過 `log`（呼叫端已經有的 log_lines 收集函式）留下一筆記錄，不再
    是完全無聲的失敗。
    """
    if not _should_schedule(is_upgrade_call(argv)):
        return
    if log is None:
        log = lambda msg: None

    bat_content = _build_bat_script(current_dir, exe_path, safe_to_remove_whole_dir)
    bat_path = os.path.join(tempfile.gettempdir(), f"_mswi_uninstall_cleanup_{os.getpid()}.bat")
    try:
        _write_bat_file(bat_path, bat_content)
    except UnicodeEncodeError:
        short_dir = _get_short_path(current_dir)
        short_exe = _get_short_path(exe_path)
        if not (short_dir and short_exe):
            log(f"[警告] 安裝路徑含有系統目前編碼無法表示的字元，且無法取得短路徑名稱，跳過自我刪除排程：{current_dir}")
            return
        try:
            bat_content = _build_bat_script(short_dir, short_exe, safe_to_remove_whole_dir)
            _write_bat_file(bat_path, bat_content)
            log(f"[警告] 安裝路徑含有系統目前編碼無法表示的字元，自我刪除改用短路徑名稱：{short_dir}")
        except Exception as e:
            log(f"[警告] 安裝路徑含有系統目前編碼無法表示的字元，改用短路徑名稱後仍無法排程自我刪除：{e}")
            return
    except Exception as e:
        log(f"[警告] 無法排程自我刪除：{e}")
        return

    try:
        subprocess.Popen(
            f'"{bat_path}"',
            shell=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log(f"[警告] 無法排程自我刪除：{e}")
