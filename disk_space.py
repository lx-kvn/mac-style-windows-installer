"""
disk_space.py
--------------
安裝前的磁碟空間檢查。純函式：只吃路徑/位元組數，不碰 InstallerAPI 的任何
狀態，測試不需要先建構一整個 InstallerAPI()（連帶觸發 load_config() 的
設定檔讀取）。
"""
import os
import shutil


def required_install_size(src_dir):
    """加總 src_dir 底下所有檔案的大小（遞迴），算出這次安裝實際需要的空間。"""
    total = 0
    for root, dirs, files in os.walk(src_dir):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


def check_drive_space(requirements, fallback_path=""):
    """檢查一組「落地位置 → 需要多少空間」在各自所在磁碟上是否都放得下。

    requirements：`[(落地目錄, 需要的位元組數), ...]`。同一顆磁碟上的多筆
    需求會先加總再檢查，需求量為 0 的項目直接略過（例如這次沒有任何
    local_appdata 檔案、或不是覆蓋安裝所以沒有備份需求）——那顆磁碟根本
    不會被寫入，不該因為它查不到空間就擋下整個安裝。

    F08：原本的介面是「一個路徑、一個需求量」，只檢查安裝目錄所在磁碟。
    但 `local_appdata_files` 指定的檔案實際落在
    `%LOCALAPPDATA%\\Programs\\<folder_name>`、覆蓋安裝的備份落在 `%TEMP%`，
    兩者都可能位於另一顆磁碟——那些磁碟從未被檢查，而安裝目錄所在磁碟的
    需求量同時被高估。改成依落地磁碟分組，逐一檢查。

    落地目錄通常還沒建立，所以用 os.path.splitdrive 只取磁碟代號，不需要
    路徑本身存在；解析不出磁碟代號時（例如空字串）才 fallback 用
    fallback_path，兩者都取不到再退回 "C:"。

    保留 10% 緩衝空間（維持原本的行為）。回傳
    `(是否全部足夠, [{"drive", "free", "required", "sufficient"}, ...])`，
    清單依磁碟代號排序，方便呼叫端組出訊息。查不到某顆磁碟的用量
    （磁碟機代號無效、網路磁碟掉線）時該顆磁碟不列入結果，也不算失敗——
    那是「查不到」，不是「空間不足」，原本的單磁碟版本同樣不會因此擋下
    安裝（例外會往外拋給呼叫端的 except 處理）。
    """
    totals = {}
    for path, required_bytes in requirements:
        if not required_bytes:
            continue
        drive = os.path.splitdrive(path)[0] or os.path.splitdrive(fallback_path)[0] or "C:"
        totals[drive] = totals.get(drive, 0) + required_bytes

    drives = []
    for drive in sorted(totals):
        required = totals[drive]
        try:
            usage = shutil.disk_usage(drive + "\\")
        except Exception:
            continue
        drives.append({
            "drive": drive,
            "free": usage.free,
            "required": required,
            "sufficient": usage.free >= required * 1.1,
        })
    return all(d["sufficient"] for d in drives), drives
