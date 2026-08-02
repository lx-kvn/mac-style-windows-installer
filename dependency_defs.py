"""
dependency_defs.py
-------------------
內建相依元件（VC++ Redistributable / .NET Desktop Runtime）的靜態中繼
資料：顯示名稱、官方下載連結、靜默安裝參數。

installer_core.py（安裝端，決定缺少時要不要自動下載安裝）跟 builder.py
（打包端，bundle_dependencies 選項要在打包當下把相依元件安裝檔下載下來
內嵌進 Setup.exe 時，需要知道去哪裡下載）共用同一份，避免兩邊各自維護
一份 URL，哪天只改了一邊、悄悄不同步。

vcredist_x64 的連結是 Microsoft 官方文件明講的永久 permalink
（https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist
的「Permalink for latest supported x64 version」），永遠指向最新版，
不需要維護。

dotnet_desktop 沒有這種版本無關的永久連結——aka.ms/dotnet/<版本>/... 這個
格式一定要指定確切的 major.minor 頻道。這裡先固定用「10.0」（2026 年寫下
這行時的最新 LTS 版本，.NET 8/9 已於 2026-11-10 到期）。等 .NET 10 也到期
（官方支援到約 2028-11）時，這個版本號要手動更新成下一個 LTS，否則舊連結
雖然還能用，但裝到的會是一個過期的舊版本。這是刻意接受的維護負擔，不是
遺漏——見規格文件.md 對應章節的已知限制說明。
"""

BUILT_IN_DEPENDENCIES = {
    "vcredist_x64": {
        "display_name": "Visual C++ Redistributable (x64)",
        "download_url": "https://aka.ms/vc14/vc_redist.x64.exe",
        "silent_args": ["/install", "/quiet", "/norestart"],
    },
    "dotnet_desktop": {
        "display_name": ".NET Desktop Runtime",
        "download_url": "https://aka.ms/dotnet/10.0/windowsdesktop-runtime-win-x64.exe",
        "silent_args": ["/install", "/quiet", "/norestart"],
    },
}
