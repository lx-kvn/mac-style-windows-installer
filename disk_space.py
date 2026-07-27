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


def check_disk_space(required_bytes, target_path, fallback_path):
    """檢查 target_path 所在磁碟的剩餘空間夠不夠裝下 required_bytes。

    target_path 通常還沒建立（安裝目錄），所以用 os.path.splitdrive 只取磁碟
    代號，不需要路徑本身存在；target_path 解析不出磁碟代號時（例如空字串）
    才 fallback 用 fallback_path，兩者都取不到再退回 "C:"。

    保留 10% 緩衝空間，回傳 (是否足夠, 剩餘空間, 需要的空間)。
    """
    drive = os.path.splitdrive(target_path)[0] or os.path.splitdrive(fallback_path)[0] or "C:"
    usage = shutil.disk_usage(drive + "\\")
    return usage.free >= required_bytes * 1.1, usage.free, required_bytes
