"""
msix_install.py
----------------
MSIX 引擎的安裝流程協調：照什麼順序做哪幾件事。

這一層**不負責任何一件事本身**——偵測與移除舊版、實際部署，都是注入進來的
（`check_existing`／`remove_existing`／`deploy`）。這樣拆的理由是這幾件事
各自已有實作（`upgrade.py`、`msix_deploy.py`），而順序本身是一個獨立的、
有依據的決定，值得單獨測試。

## 順序的依據

第二輪決議第九項：在交付系統部署**之前**先移除傳統模式的既有安裝。

任何中途改採 MSIX 的下游專案，其既有使用者皆處於「已安裝傳統模式版本」的
狀態。MSIX 部署不知悉該安裝的存在，不處理將導致新舊並存——兩筆同名的應用
程式清單項目、檔案關聯衝突、以及使用者手動清除時刪錯的風險。

該決議並要求於介面明確告知將先移除舊版，因此偵測到舊版時會透過 `log` 送出
一則說明。

**移除失敗時不繼續部署**：繼續下去的結果正是新舊並存，也就是這一步要避免
的情形本身。

## 這個模式沒有解除安裝程式

ADR-0006：MSIX 模式不提供自訂解除安裝介面，解除安裝由系統接管（「設定 →
應用程式」或開始功能表按右鍵）。成功訊息因此指向系統的路徑，而不是像傳統
模式那樣提到安裝目錄裡的 `uninstall.exe`——那個檔案在這個模式下不存在。
"""
import os

# 這個模式沒有自訂的解除安裝介面（ADR-0006），成功訊息要告訴使用者去哪裡
# 解除安裝，否則他會去安裝目錄找一個不存在的 uninstall.exe。
SUCCESS_MESSAGE = (
    "安裝成功。這個應用程式由 Windows 的套件引擎管理，"
    "需要移除時請到「設定 → 應用程式」，或在開始功能表的項目上按右鍵解除安裝。"
)


def run(package_path, check_existing=None, remove_existing=None, deploy=None,
        progress=None, log=None, package_must_exist=False):
    """執行 MSIX 模式的安裝，回傳與傳統流程相同形狀的結果字典。

    `package_must_exist`：呼叫端已經確認過檔案存在時可以省略這道檢查。預設
    不檢查，是因為測試注入的替身不需要真的有一個檔案。
    """
    def report(message):
        if log:
            log(message)

    if package_must_exist and not os.path.isfile(package_path):
        return {
            "status": "error",
            "message": f"安裝失敗：找不到內建的套件檔案（{package_path}）。",
        }

    if check_existing:
        existing = check_existing() or {}
        if existing.get("exists"):
            # 決議第九項要求明確告知——使用者看到安裝程式在動舊版本的東西時，
            # 應該已經知道那是預期中的步驟。
            report(
                "偵測到已安裝的舊版本（傳統安裝模式），會先把它移除再安裝新版："
                f"{existing.get('install_path', '')}"
            )
            result = (remove_existing(existing) if remove_existing else None) or {}
            if result.get("status") != "success":
                # 移除失敗還繼續部署，結果就是新舊並存——那正是這一步要避免的。
                message = result.get("message") or "舊版本移除失敗，原因不明。"
                return {
                    "status": "error",
                    "message": f"安裝中止：{message}\n新舊版本並存會造成兩筆重複的應用程式項目與檔案關聯衝突，"
                               "因此不繼續安裝。",
                }
            report("舊版本已移除")

    report("正在交由 Windows 的套件引擎安裝...")
    outcome = deploy(package_path, progress=progress)
    if not outcome.ok:
        # error_text 是系統給的完整且已在地化的說明文字，直接轉呈——自己另編
        # 一則訊息只會失去資訊（第三輪 spike 結果第七項）。
        return {"status": "error", "message": f"安裝失敗：{outcome.error_text}"}

    report("安裝完成")
    return {"status": "success", "message": SUCCESS_MESSAGE}
