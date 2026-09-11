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

import messages

# 這個模組的訊息全部會被使用者看到，因此都走翻譯表。原本是 Python 裡的字面
# 中文字串，英文環境的使用者會收到中文——包含降版確認那一則，而那一則要
# 使用者決定是否讓系統清除應用程式的資料（2026-09-06 修正）。
MESSAGES = {
    "zh-TW": {
        # 這個模式沒有自訂的解除安裝介面（ADR-0006），成功訊息要告訴使用者
        # 去哪裡解除安裝，否則他會去安裝目錄找一個不存在的 uninstall.exe。
        "success":
            "安裝成功。這個應用程式由 Windows 的套件引擎管理，"
            "需要移除時請到「設定 → 應用程式」，或在開始功能表的項目上按右鍵解除安裝。",
        "installed_package_note":
            "\n這台電腦上已經安裝了同一個應用程式的套件（{full_name}）。"
            "版本較新或相同的套件系統都會自行處理（前者就地更新，後者重新註冊），"
            "因此這個失敗通常代表這次要裝的版本比它舊。要改裝比較舊的版本，"
            "請先到「設定 → 應用程式」把它解除安裝，再執行一次這個安裝程式。",
        "downgrade_question":
            "這台電腦上已經安裝了比較新的版本（{installed}），"
            "而這次要安裝的是 {new}。\n"
            "要繼續的話必須先請系統移除已安裝的那一份，而系統移除套件時"
            "會連同這個應用程式的資料一起清除，那些資料無法復原。\n"
            "確定要改裝比較舊的版本嗎？",
        "downgrade_declined":
            "安裝已取消：這台電腦上的版本（{installed}）比這次要安裝的"
            "（{new}）新，而你選擇不移除它。",
        "downgrade_notice":
            "要安裝的版本（{new}）比已安裝的（{installed}）舊，"
            "因此會先請系統移除 {full_name}——"
            "系統移除套件時會連同這個應用程式的資料一起清除。",
        "different_publisher":
            "注意：這台電腦上有一份同名但簽章者不同的套件（{full_name}）。"
            "系統會把它與這次要安裝的視為兩個不相關的應用程式，兩者將並存。"
            "工具不會自動移除它——那份套件有可能屬於另一個開發者。",
        "found_installed": "偵測到同一個應用程式的套件已安裝（{full_name}{version}）。",
        "found_installed_version": "，版本 {version}",
        "removal_failed": "安裝中止：舊版本移除失敗——{error}",
        "old_version_removed": "舊版本已移除",
        "legacy_found":
            "偵測到已安裝的舊版本（傳統安裝模式），會先把它移除再安裝新版：{path}",
        "legacy_removal_unknown": "舊版本移除失敗，原因不明。",
        "legacy_dir_left":
            "舊版本的安裝資料夾沒有清乾淨（{error}）。裡面的應用程式已經移除，"
            "剩下的是解除安裝助手本身——它現在沒有作用，可以手動刪除：{path}",
        "legacy_removal_failed":
            "安裝中止：{message}\n新舊版本並存會造成兩筆重複的應用程式項目與"
            "檔案關聯衝突，因此不繼續安裝。",
        "package_missing": "安裝失敗：找不到內建的套件檔案（{path}）。",
        "provisioning": "正在登記給這台電腦上的所有使用者...",
        "provision_crashed":
            "登記給所有使用者的步驟發生未預期的錯誤：{error}\n"
            "這個應用程式只安裝給目前這位使用者，其他使用者不會取得它。",
        "deploying": "正在交由 Windows 的套件引擎安裝...",
        "deploy_failed": "安裝失敗：{error}",
        "done": "安裝完成",
    },
    "en": {
        "success":
            "Installed. This application is managed by the Windows packaging "
            "engine; to remove it, go to Settings > Apps, or right-click its "
            "entry in the Start menu and uninstall it.",
        "installed_package_note":
            "\nA package for this same application is already installed on this "
            "machine ({full_name}). Windows handles a newer or identical version "
            "on its own (updating in place, or re-registering), so this failure "
            "usually means the version being installed is older. To install the "
            "older version, uninstall the existing one from Settings > Apps "
            "first, then run this installer again.",
        "downgrade_question":
            "A newer version ({installed}) is already installed on this machine, "
            "and this installer carries {new}.\n"
            "Continuing means asking Windows to remove the installed package "
            "first, and Windows removes an application's data along with it. "
            "That data cannot be recovered.\n"
            "Install the older version anyway?",
        "downgrade_declined":
            "Installation cancelled: the version on this machine ({installed}) is "
            "newer than the one being installed ({new}), and you chose to keep it.",
        "downgrade_notice":
            "The version being installed ({new}) is older than the installed one "
            "({installed}), so Windows will be asked to remove {full_name} first "
            "— removing a package also erases that application's data.",
        "different_publisher":
            "Note: a package with the same name but a different signer "
            "({full_name}) is installed on this machine. Windows treats the two "
            "as unrelated applications and they will coexist. This installer "
            "does not remove it — that package may belong to another developer.",
        "found_installed":
            "A package for this application is already installed ({full_name}{version}).",
        "found_installed_version": ", version {version}",
        "removal_failed":
            "Installation stopped: removing the old version failed — {error}",
        "old_version_removed": "Old version removed",
        "legacy_found":
            "An older version installed the traditional way was found; it will be "
            "removed before the new one is installed: {path}",
        "legacy_removal_unknown": "Removing the old version failed for an unknown reason.",
        "legacy_dir_left":
            "The old installation folder could not be cleaned up ({error}). The "
            "application itself has been removed; what remains is the old "
            "uninstaller, which no longer does anything and can be deleted by "
            "hand: {path}",
        "legacy_removal_failed":
            "Installation stopped: {message}\nLeaving both versions in place would "
            "produce two duplicate application entries and conflicting file "
            "associations, so the installation does not continue.",
        "package_missing":
            "Installation failed: the bundled package file was not found ({path}).",
        "provisioning": "Registering the application for every user on this machine...",
        "provision_crashed":
            "The step that registers the application for every user hit an "
            "unexpected error: {error}\n"
            "The application was installed for the current user only; other users "
            "will not get it.",
        "deploying": "Handing the package to the Windows packaging engine...",
        "deploy_failed": "Installation failed: {error}",
        "done": "Installation complete",
    },
}


def _t(key, lang=messages.DEFAULT_LANGUAGE, /, **params):
    return messages.translate(MESSAGES, key, lang, **params)


def success_message(lang=messages.DEFAULT_LANGUAGE):
    """安裝成功後要顯示給使用者的那一則。

    是這個模式底下使用者唯一被告知「去哪裡解除安裝」的機會，因此它必須真的
    走到畫面上——`ui/index.html` 曾經把這個訊息丟掉（見
    `tests/test_msix_success_guidance.py`）。
    """
    return _t("success", lang)


# 既有呼叫端與文件仍以這個名字引用預設語言的那一則。
SUCCESS_MESSAGE = MESSAGES["zh-TW"]["success"]


def _installed_package_note(full_name, lang=messages.DEFAULT_LANGUAGE):
    """部署失敗且同名套件已安裝時，附在系統訊息後面的那一段。

    附加而不取代：系統給的 `error_text` 是完整且已在地化的說明，自己另編
    一則只會失去資訊（第三輪 spike 結果第七項）。

    **內容依 2026-09-05 於 Windows 11 25H2（26200.8037、zh-TW）的實測撰寫**
    （見 `docs/investigations/MSIX稽核與缺陷修正.md` 的 D3）：同版本重新安裝
    會成功、版本較新會就地更新，只有降版會失敗。稽核當時的推測「同版本重裝
    也會失敗」不成立，訊息不再那樣寫——照著那個說法，使用者會去移除一個其實
    不需要移除的東西。
    """
    return _t("installed_package_note", lang, full_name=full_name)


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


def _downgrade_question(existing, new_version, lang=messages.DEFAULT_LANGUAGE):
    """降版時要問使用者的那一則，以及附帶的資料。

    訊息要說出資料會被清掉——那是傳統引擎的降版沒有的後果，也是使用者答這個
    問題時必須知道的事（ADR-0015 決定二）。
    """
    return {
        "installed_version": existing.version,
        "new_version": new_version,
        "package_full_name": existing.full_name,
        "message": _t("downgrade_question", lang,
                      installed=existing.version, new=new_version),
    }


def _handle_existing_package(existing, package_version, package_publisher,
                             confirm_downgrade, remove_installed_package, report,
                             warnings, lang=messages.DEFAULT_LANGUAGE):
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
        report(_t("different_publisher", lang, full_name=existing.full_name))
        return True, None

    if not package_version or not existing.version:
        return True, None
    if _compare_versions(package_version, existing.version) >= 0:
        return True, None

    question = _downgrade_question(existing, package_version, lang)
    if confirm_downgrade is not None and not confirm_downgrade(question):
        return False, _t("downgrade_declined", lang,
                         installed=existing.version, new=package_version)

    notice = _t("downgrade_notice", lang, new=package_version,
                installed=existing.version, full_name=existing.full_name)
    report(notice)
    # 也放進回傳值：`log` 收到的那些進的是安裝檔內部的 install_log.txt，而
    # 靜默安裝 `/LOG=` 指定的那一份由 run_silent_install() 自己維護，兩者互不
    # 相通。ADR-0015 決定三要求的是後者，而它已經會把 warnings 逐條寫進去
    # （F01 建立的慣例）——實機驗證抓到這個缺口時，那個出口就已經在那裡了。
    warnings.append(notice)
    outcome = (remove_installed_package(existing.full_name)
               if remove_installed_package else None)
    if outcome is not None and not outcome.ok:
        return False, _t("removal_failed", lang, error=outcome.error_text)
    report(_t("old_version_removed", lang))
    return True, None


def _find_installed(find_installed_package, log, lang=messages.DEFAULT_LANGUAGE):
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
        version = (_t("found_installed_version", lang, version=existing.version)
                   if existing.version else "")
        log(_t("found_installed", lang, full_name=existing.full_name, version=version))
    return existing


def run(package_path, check_existing=None, remove_existing=None, deploy=None,
        progress=None, log=None, package_must_exist=False,
        find_installed_package=None, package_version="", package_publisher="",
        confirm_downgrade=None, remove_installed_package=None,
        provision_all_users=None, remove_legacy_dir=None,
        lang=messages.DEFAULT_LANGUAGE):
    """執行 MSIX 模式的安裝，回傳與傳統流程相同形狀的結果字典。

    `package_must_exist`：呼叫端已經確認過檔案存在時可以省略這道檢查。預設
    不檢查，是因為測試注入的替身不需要真的有一個檔案。

    `package_version`／`package_publisher` 是這次要安裝的套件的版本與發行者，
    用來與已安裝的那一份比較（見 `_handle_existing_package()`）。沒有給的話
    不做比較——修正之前編出的安裝檔沒有那兩個欄位，其行為維持修正前的樣子。

    `confirm_downgrade` 為 None 表示不詢問、直接做：靜默安裝走這一條
    （ADR-0015 決定三）。

    `provision_all_users` 為 None 時完全不走全機器範圍那條路——沒有啟用該
    設定的安裝檔行為與這個參數出現之前相同。

    `remove_legacy_dir` 在傳統模式那一份移除成功之後被呼叫，收掉剩下的空殼。
    需要它的理由見下方呼叫處的說明。為 None 時完全不做，行為與這個參數出現
    之前相同。

    `lang` 決定所有回傳與回報的訊息用哪一種語言。安裝端傳的是它自己依系統
    語言算出來的那一個值，兩邊不會分岔。
    """
    def report(message):
        if log:
            log(message)

    if package_must_exist and not os.path.isfile(package_path):
        return {
            "status": "error",
            "message": _t("package_missing", lang, path=package_path),
        }

    # warnings 先建立：清掉舊資料夾失敗時要往這裡放一條，而那一步比下面
    # 原本建立它的地方更早。
    warnings = []
    if check_existing:
        existing = check_existing() or {}
        if existing.get("exists"):
            # 決議第九項要求明確告知——使用者看到安裝程式在動舊版本的東西時，
            # 應該已經知道那是預期中的步驟。
            report(_t("legacy_found", lang, path=existing.get("install_path", "")))
            result = (remove_existing(existing) if remove_existing else None) or {}
            if result.get("status") != "success":
                # 移除失敗還繼續部署，結果就是新舊並存——那正是這一步要避免的。
                message = result.get("message") or _t("legacy_removal_unknown", lang)
                return {
                    "status": "error",
                    "message": _t("legacy_removal_failed", lang, message=message),
                }
            report(_t("old_version_removed", lang))
            # 那個資料夾本身還在：舊的解除安裝助手被以 `--upgrade` 呼叫，而
            # `self_delete` 對那個旗標不排背景自我刪除——傳統換傳統的更新裡
            # 那是對的（避免那段刪除把剛複製進去的新檔案一併帶走），但這裡
            # 不會有任何東西複製回那個資料夾，剩下的是一支沒有作用的解除安裝
            # 助手（2026-09-11 實機量到）。
            #
            # 收不掉不中止安裝：使用者要的東西已經裝好了，剩下的是一個空殼。
            if remove_legacy_dir:
                try:
                    remove_legacy_dir(existing.get("install_path", ""))
                except Exception as error:
                    warnings.append(_t("legacy_dir_left", lang, error=error,
                                       path=existing.get("install_path", "")))

    # 同名的 MSIX 套件是否已安裝——查一次，供版本比較與失敗訊息使用。
    # 查在部署**之前**：「要不要降版」這個決定放在失敗之後的話，使用者此時
    # 看到的是系統的錯誤訊息，不是一個他可以回答的問題（ADR-0015 決定一）。
    installed_package = _find_installed(find_installed_package, log, lang)
    if installed_package is not None:
        proceed, refusal = _handle_existing_package(
            installed_package, package_version, package_publisher,
            confirm_downgrade, remove_installed_package, report, warnings, lang)
        if not proceed:
            return {"status": "error", "message": refusal}

    # 佈建排在註冊之前（ADR-0013 決定三）。順序不是偏好而是系統的限制：
    # 佈建需要提權、註冊不能提權，兩者必須發生在不同的權限下。
    #
    # 這一段的任何失敗都不中止安裝：套件本身沒問題，接下來的註冊會照常
    # 進行，而使用者要的東西正是那一步給的。降級的說明走 warnings（第九
    # 題），與降版警示在畫面上長得一樣。
    if provision_all_users is not None:
        report(_t("provisioning", lang))
        try:
            provisioned = provision_all_users()
        except Exception as e:
            warnings.append(_t("provision_crashed", lang, error=e))
        else:
            if not provisioned.ok and provisioned.warning:
                warnings.append(provisioned.warning)

    report(_t("deploying", lang))
    outcome = deploy(package_path, progress=progress)
    if not outcome.ok:
        # error_text 是系統給的完整且已在地化的說明文字，直接轉呈——自己另編
        # 一則訊息只會失去資訊（第三輪 spike 結果第七項）。
        message = _t("deploy_failed", lang, error=outcome.error_text)
        if installed_package:
            message += _installed_package_note(installed_package.full_name, lang)
        return {"status": "error", "message": message}

    report(_t("done", lang))
    return {"status": "success", "message": success_message(lang),
            "warnings": warnings}
