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


def _installed_package_note(full_name):
    """部署失敗且同名套件已安裝時，附在系統訊息後面的那一段。

    附加而不取代：系統給的 `error_text` 是完整且已在地化的說明，自己另編
    一則只會失去資訊（第三輪 spike 結果第七項）。

    **內容依 2026-09-05 於 Windows 11 25H2（26200.8037、zh-TW）的實測撰寫**
    （見 `docs/investigations/MSIX稽核與缺陷修正.md` 的 D3）：同版本重新安裝
    會成功、版本較新會就地更新，只有降版會失敗。稽核當時的推測「同版本重裝
    也會失敗」不成立，訊息不再那樣寫——照著那個說法，使用者會去移除一個其實
    不需要移除的東西。
    """
    return (
        f"\n這台電腦上已經安裝了同一個應用程式的套件（{full_name}）。"
        "版本較新或相同的套件系統都會自行處理（前者就地更新，後者重新註冊），"
        "因此這個失敗通常代表這次要裝的版本比它舊。要改裝比較舊的版本，"
        "請先到「設定 → 應用程式」把它解除安裝，再執行一次這個安裝程式。"
    )


def _compare_versions(left, right):
    """比較兩個四段版本號，回傳 -1／0／1。讀不出來時當作相等（0）。

    不重用 `version_compare`：那個模組處理的是本專案的版本號格式（含預發布
    後綴，見 ADR-0003），而這裡拿到的是系統回報的 MSIX 版本，永遠是四段
    純數字。把兩種格式共用同一套規則，等於讓其中一邊將來被另一邊的需求
    改壞。
    """
    def parts(value):
        try:
            return [int(p) for p in str(value).split(".")]
        except ValueError:
            return None

    a, b = parts(left), parts(right)
    if a is None or b is None:
        return 0
    a += [0] * (4 - len(a))
    b += [0] * (4 - len(b))
    return (a > b) - (a < b)


def _downgrade_question(existing, new_version):
    """降版時要問使用者的那一則，以及附帶的資料。

    訊息要說出資料會被清掉——那是傳統引擎的降版沒有的後果，也是使用者答這個
    問題時必須知道的事（ADR-0015 決定二）。
    """
    return {
        "installed_version": existing.version,
        "new_version": new_version,
        "package_full_name": existing.full_name,
        "message": (
            f"這台電腦上已經安裝了比較新的版本（{existing.version}），"
            f"而這次要安裝的是 {new_version}。\n"
            "要繼續的話必須先請系統移除已安裝的那一份，而系統移除套件時"
            "會連同這個應用程式的資料一起清除，那些資料無法復原。\n"
            "確定要改裝比較舊的版本嗎？"
        ),
    }


def _handle_existing_package(existing, package_version, package_publisher,
                             confirm_downgrade, remove_installed_package, report):
    """已安裝的同名套件要怎麼處置，回傳 `(可以繼續嗎, 中止時的訊息)`。

    三種情形（ADR-0015）：

    - **發行者不同**——系統把兩者當成互不相關的應用程式並存安裝。只告知，
      不移除：那份套件確有可能屬於另一個開發者。
    - **版本較新或相同**——系統自行處理（前者就地更新，後者重新註冊，兩者
      皆經 2026-09-05 實機量測確認）。不做任何事。
    - **版本較舊（降版）**——要問過使用者；`confirm_downgrade` 為 None 時
      直接做（靜默安裝走這一條，決定三），但把發生的事寫進紀錄。
    """
    if package_publisher and existing.publisher and \
            existing.publisher != package_publisher:
        report(
            f"注意：這台電腦上有一份同名但簽章者不同的套件（{existing.full_name}）。"
            "系統會把它與這次要安裝的視為兩個不相關的應用程式，兩者將並存。"
            "工具不會自動移除它——那份套件有可能屬於另一個開發者。"
        )
        return True, None

    if not package_version or not existing.version:
        return True, None
    if _compare_versions(package_version, existing.version) >= 0:
        return True, None

    question = _downgrade_question(existing, package_version)
    if confirm_downgrade is not None and not confirm_downgrade(question):
        return False, (
            f"安裝已取消：這台電腦上的版本（{existing.version}）比這次要安裝的"
            f"（{package_version}）新，而你選擇不移除它。"
        )

    report(
        f"要安裝的版本（{package_version}）比已安裝的（{existing.version}）舊，"
        f"因此會先請系統移除 {existing.full_name}——"
        "系統移除套件時會連同這個應用程式的資料一起清除。"
    )
    outcome = (remove_installed_package(existing.full_name)
               if remove_installed_package else None)
    if outcome is not None and not outcome.ok:
        return False, f"安裝中止：舊版本移除失敗——{outcome.error_text}"
    report("舊版本已移除")
    return True, None


def _find_installed(find_installed_package, log):
    """查同名套件，查不到或查詢本身出錯都回傳 None。

    查詢失敗不該讓一次本來會成功的安裝失敗：這個結果只用來把訊息講清楚，
    不是流程的必要條件。
    """
    if not find_installed_package:
        return None
    try:
        existing = find_installed_package() or None
    except Exception:
        return None
    if existing and log:
        version = f"，版本 {existing.version}" if existing.version else ""
        log(f"偵測到同一個應用程式的套件已安裝（{existing.full_name}{version}）。")
    return existing


def run(package_path, check_existing=None, remove_existing=None, deploy=None,
        progress=None, log=None, package_must_exist=False,
        find_installed_package=None, package_version="", package_publisher="",
        confirm_downgrade=None, remove_installed_package=None):
    """執行 MSIX 模式的安裝，回傳與傳統流程相同形狀的結果字典。

    `package_must_exist`：呼叫端已經確認過檔案存在時可以省略這道檢查。預設
    不檢查，是因為測試注入的替身不需要真的有一個檔案。

    `package_version`／`package_publisher` 是這次要安裝的套件的版本與發行者，
    用來與已安裝的那一份比較（見 `_handle_existing_package()`）。沒有給的話
    不做比較——修正之前編出的安裝檔沒有那兩個欄位，其行為維持修正前的樣子。

    `confirm_downgrade` 為 None 表示不詢問、直接做：靜默安裝走這一條
    （ADR-0015 決定三）。
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

    # 同名的 MSIX 套件是否已安裝——查一次，供版本比較與失敗訊息使用。
    # 查在部署**之前**：「要不要降版」這個決定放在失敗之後的話，使用者此時
    # 看到的是系統的錯誤訊息，不是一個他可以回答的問題（ADR-0015 決定一）。
    installed_package = _find_installed(find_installed_package, log)
    if installed_package is not None:
        proceed, refusal = _handle_existing_package(
            installed_package, package_version, package_publisher,
            confirm_downgrade, remove_installed_package, report)
        if not proceed:
            return {"status": "error", "message": refusal}

    report("正在交由 Windows 的套件引擎安裝...")
    outcome = deploy(package_path, progress=progress)
    if not outcome.ok:
        # error_text 是系統給的完整且已在地化的說明文字，直接轉呈——自己另編
        # 一則訊息只會失去資訊（第三輪 spike 結果第七項）。
        message = f"安裝失敗：{outcome.error_text}"
        if installed_package:
            message += _installed_package_note(installed_package.full_name)
        return {"status": "error", "message": message}

    report("安裝完成")
    return {"status": "success", "message": SUCCESS_MESSAGE}
