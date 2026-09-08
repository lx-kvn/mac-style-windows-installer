"""
msix_provision.py
------------------
提權的子行程實際做的那幾件事：把套件登記給這台機器上的每一位使用者。

## 這裡不做註冊

**佈建**（provision）是管理員把套件登記到這台機器上，讓每一位使用者「有
資格」拿到它；**註冊**（register）是某一位使用者那一端真的完成安裝。兩者
分屬不同的權限與時機（見 `CONTEXT.md`）。

這個模組只做佈建。註冊留在未提權的主行程，因為提權之後做註冊會以
`0x80070005` 失敗——提升後的權杖之下取不到那位使用者的套件生命週期環境
（ADR-0013 背景第五項與 2026-09-07 的量測）。兩件事必須發生在不同的權限
下，這不是實作上的偏好而是系統的限制。

## 順序

1. **比對套件的 SHA-256**。這個模組由 `Setup.exe` 的一個內部旗標啟動，而
   那個旗標任何人都能打、它會以提權身分佈建參數指定的那一份套件。主行程
   把雜湊一併傳進來，比對不符就拒絕——那個旗標因此只能用來佈建「這一次
   安裝自己解壓出來的那一份」（`/grill-with-docs` 第十二題）。
2. **必要時複製到系統磁碟區**（ADR-0013 決定五）。安裝檔內嵌的套件解壓於
   `%TEMP%`，而該位置不保證位於系統磁碟區。複製由這個提權的行程做、目的地
   放在只有管理員寫得進去的位置：放在一般使用者也寫得進去的地方，從複製
   完成到佈建開始之間會有一個掉包的空窗（第七題）。
3. **stage**。佈建要求套件先經過 stage（ADR-0009 所列的三項前置條件之一）。
4. **查套件家族名稱**。佈建吃的是家族名稱，不是路徑。
5. **佈建**。失敗就當場取消佈建（第四題）——子行程當下還是提權的，清理
   不用再跳一次 UAC，而它擋掉的正是「佈建紀錄留下來、使用者自己清不掉」
   那個情形（ADR-0013 的已知限制第一項）。
6. **刪掉第 2 步的複本**，不論成敗。原始的那一份不刪：主行程還要拿它替
   當前使用者註冊。

## 成敗的判準與部署不同

`msix_deploy` 以 `is_registered` 判斷成敗，那是部署的判準。stage 與佈建都
不會讓套件變成「已註冊」，沿用那個判準會把每一次成功都讀成失敗。這裡改看
錯誤訊息與錯誤碼，與 `msix_deploy.remove()` 同一種形狀。

## 與 `msix_deploy` 共用的幾個私有函式

本模組呼叫 `msix_deploy` 的 `_resolve_manager()`／`_deployment_uri()`／
`_await_operation()`。它們掛底線是為了對模組外的一般呼叫端關門，而這兩個
模組是同一件事的兩半（同一個 `PackageManager`、同一組實測而來的陷阱），
把它們複製一份到這裡的結果是其中一份被修好的時候另一份不會跟著好——例如
`_deployment_uri()` 裡那個百分比編碼（帳號名稱含 `#` 時路徑會被截斷）。
"""
import hashlib
import os
import shutil
import tempfile

import msix_deploy

EXIT_OK = 0
# 從 10 起跳：`installer_core` 已經用掉 0（成功）、1（安裝失敗）、
# 2（缺少 WebView2 Runtime），撞在一起的話兩種失敗在紀錄裡會長得一樣。
EXIT_BAD_REQUEST = 10
EXIT_VERIFY_FAILED = 11
EXIT_COPY_FAILED = 12
EXIT_STAGE_FAILED = 13
EXIT_FAMILY_UNKNOWN = 14
EXIT_PROVISION_FAILED = 15

MESSAGES = {
    EXIT_BAD_REQUEST: "佈建的要求不完整：{detail}",
    EXIT_VERIFY_FAILED: "套件與這次安裝內嵌的那一份不符，因此不予佈建。",
    EXIT_COPY_FAILED: "把套件複製到系統磁碟區時失敗：{detail}",
    EXIT_STAGE_FAILED: "系統無法備妥這個套件：{detail}",
    EXIT_FAMILY_UNKNOWN: "備妥之後仍查不到這個套件的家族名稱，因此無法佈建。",
    EXIT_PROVISION_FAILED: "登記給所有使用者時失敗：{detail}",
}


def file_digest(path, chunk_size=1024 * 1024):
    """算一個檔案的 SHA-256，回傳小寫十六進位字串。

    分段讀取：GB 級的安裝檔真實存在（ADR-0013 決定五就是為它而設），整份
    讀進記憶體會在那些情形下失敗，而失敗的時候安裝已經走到一半了。
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def is_on_system_volume(path, system_drive=None):
    """套件是不是已經在系統磁碟區上。

    以磁碟機代號比對，不查掛載點那類進階情形——那些情境下就算判錯，後果
    也只是多複製一次（第七題的附帶決定）。網路路徑（UNC）取不到磁碟機
    代號，一律視為不在系統磁碟區。
    """
    if system_drive is None:
        system_drive = os.environ.get("SystemDrive", "C:")
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    return bool(drive) and drive.rstrip("\\/").lower() == \
        system_drive.rstrip("\\/").lower()


def admin_temp_root():
    """複本要放的位置：`%SystemRoot%\\Temp`。

    一般使用者寫不進去。`%TEMP%` 與 `C:\\Users\\Public` 都寫得進去，因此
    都不是這裡要的——同一台機器上的另一個使用者可以在佈建開始之前把檔案
    掉包。
    """
    return os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Temp")


def copy_to_admin_temp(package_path, temp_root=None):
    """把套件複製到只有管理員寫得進去的暫存目錄，回傳複本的路徑。

    每一次複製都開一個自己的目錄：檔名沿用原本的（系統的錯誤訊息會提到
    它），而同名檔案在共用目錄裡會互相覆蓋。
    """
    root = temp_root or admin_temp_root()
    os.makedirs(root, exist_ok=True)
    directory = tempfile.mkdtemp(prefix="mswi-provision-", dir=root)
    destination = os.path.join(directory, os.path.basename(package_path))
    shutil.copy2(package_path, destination)
    return destination


def remove_copy(copied_path):
    """刪掉 `copy_to_admin_temp()` 建立的那個目錄，連同裡面的複本。"""
    shutil.rmtree(os.path.dirname(copied_path), ignore_errors=True)


def outcome_from(result):
    """把系統回傳的部署結果翻成 `Outcome`（stage／佈建／取消佈建共用）。

    不看 `is_registered`：那是部署的判準，而這三個動作都不會讓套件變成
    已註冊。錯誤碼不為零、訊息卻空白的組合出現過（見 `msix_deploy` 模組
    說明第一點），因此兩者都要看。
    """
    error_text = getattr(result, "error_text", "") or ""
    code = getattr(result, "extended_error_code", None)
    if error_text:
        return msix_deploy.Outcome(False, error_text, code)
    if isinstance(code, int) and code != 0:
        return msix_deploy.Outcome(
            False, f"系統沒有說明原因，只回報了錯誤碼 0x{code & 0xFFFFFFFF:08X}。",
            code)
    return msix_deploy.Outcome(True, "", code)


def _manager(manager, manager_factory):
    return msix_deploy._resolve_manager(manager, manager_factory)


def stage(package_path, manager=None, manager_factory=None, progress=None):
    """請系統備妥（stage）一份套件，回傳 `Outcome`。"""
    manager, error = _manager(manager, manager_factory)
    if error:
        return msix_deploy.Outcome(False, error, None)
    try:
        uri = msix_deploy._deployment_uri(package_path)
        operation = manager.stage_package_async(uri, [])
        result = msix_deploy._await_operation(operation, progress)
    except Exception as e:
        return msix_deploy.Outcome(False, f"請求系統備妥套件時失敗：{e}", None)
    return outcome_from(result)


def provision(family_name, manager=None, manager_factory=None, progress=None):
    """把已備妥的套件登記給這台機器上的每一位使用者，回傳 `Outcome`。"""
    manager, error = _manager(manager, manager_factory)
    if error:
        return msix_deploy.Outcome(False, error, None)
    try:
        operation = manager.provision_package_for_all_users_async(family_name)
        result = msix_deploy._await_operation(operation, progress)
    except Exception as e:
        return msix_deploy.Outcome(False, f"請求系統登記套件時失敗：{e}", None)
    return outcome_from(result)


def deprovision(family_name, manager=None, manager_factory=None, progress=None):
    """撤銷佈建，回傳 `Outcome`。佈建失敗後的收拾動作。"""
    manager, error = _manager(manager, manager_factory)
    if error:
        return msix_deploy.Outcome(False, error, None)
    try:
        operation = manager.deprovision_package_for_all_users_async(family_name)
        result = msix_deploy._await_operation(operation, progress)
    except Exception as e:
        return msix_deploy.Outcome(False, f"請求系統撤銷佈建時失敗：{e}", None)
    return outcome_from(result)


def find_family_name(identity_name, publisher="", manager=None,
                     manager_factory=None):
    """查套件的家族名稱；查不到回傳 None。

    從套件物件直接讀，不從完整名稱拆字串——`Name_Ver_Arch__Hash` 是系統的
    內部慣例，拆它等於把一個我們控制不了的格式變成本專案的相依
    （`msix_deploy.InstalledPackage` 立下的同一條規矩）。

    **不以發行者字串當查詢條件**（`find_packages_by_name_publisher`）：設定檔
    裡那一串與系統回報的那一串是否逐字相同不在我們的控制之內，而查詢對不上時
    系統只會回傳空集合，結果是一次莫名其妙的降級。改為列舉全部再自己挑——那個
    查詢對未提權的行程會被拒（第三輪 spike 結果第二項），在這裡行得通是因為
    這個模組只在提權的子行程裡執行。

    挑的規則：同名的套件裡，發行者完全相同的優先；沒有完全相同的，而同名的
    只有一個，那就是它——剛剛備妥的那一份是這一步唯一可能的來源。同名的有
    好幾個而沒有一個對得上發行者時回傳 None：同名但簽章者不同的套件會被系統
    當成兩個不相關的應用程式並存（ADR-0015 已處理過這個情形），挑錯一個的
    後果是把別人的應用程式登記給這台機器上的每一位使用者。
    """
    manager, error = _manager(manager, manager_factory)
    if error:
        return None
    try:
        same_name = [package for package in manager.find_packages()
                     if package.id.name == identity_name]
        if publisher:
            for package in same_name:
                if str(getattr(package.id, "publisher", "") or "") == publisher:
                    return package.id.family_name
        if len(same_name) == 1:
            return same_name[0].id.family_name
    except Exception:
        return None
    return None


def _message(code, detail=""):
    return MESSAGES[code].format(detail=detail)


# `run_provisioning()` 的參數與上面幾個函式同名（呼叫端那樣讀起來才自然），
# 而參數在函式內部會把同名的模組函式遮住。先把預設實作綁到另一個名字。
_DEFAULT_STAGE = stage
_DEFAULT_PROVISION = provision
_DEFAULT_DEPROVISION = deprovision
_DEFAULT_FIND_FAMILY_NAME = find_family_name
_DEFAULT_REMOVE_COPY = remove_copy


def run_provisioning(package_path, expected_digest, identity_name, publisher="",
                     stage=None, provision=None, deprovision=None,
                     find_family_name=None, on_system_volume=None,
                     copy_to_system_volume=None, remove_copy=None, log=None):
    """跑完整段佈建，回傳 `(結束碼, 訊息)`。

    每一步都是注入點，與 `msix_install.run()` 同一種形狀：真正要被測到的是
    順序與失敗時的處置，而那幾個動作各自會在系統上留下真實的痕跡。
    """
    stage = stage or _DEFAULT_STAGE
    provision = provision or _DEFAULT_PROVISION
    deprovision = deprovision or _DEFAULT_DEPROVISION
    find_family_name = find_family_name or _DEFAULT_FIND_FAMILY_NAME
    on_system_volume = on_system_volume or is_on_system_volume
    copy_to_system_volume = copy_to_system_volume or copy_to_admin_temp
    remove_copy = remove_copy or _DEFAULT_REMOVE_COPY

    def report(message):
        if log:
            log(message)

    if not expected_digest:
        # 空字串不是「不用檢查」的意思。把它當成通行證，等於讓呼叫端漏傳
        # 一個參數就悄悄關掉這道檢查。
        return EXIT_BAD_REQUEST, _message(EXIT_BAD_REQUEST, "沒有帶套件的雜湊值。")
    if not os.path.isfile(package_path):
        return EXIT_BAD_REQUEST, _message(EXIT_BAD_REQUEST,
                                          f"找不到套件檔案（{package_path}）。")
    try:
        actual = file_digest(package_path)
    except OSError as e:
        return EXIT_BAD_REQUEST, _message(EXIT_BAD_REQUEST, f"讀不到套件檔案：{e}")
    if actual.lower() != expected_digest.strip().lower():
        return EXIT_VERIFY_FAILED, _message(EXIT_VERIFY_FAILED)

    copied = None
    source = package_path
    if not on_system_volume(package_path):
        try:
            source = copy_to_system_volume(package_path)
        except Exception as e:
            return EXIT_COPY_FAILED, _message(EXIT_COPY_FAILED, str(e))
        copied = source

    def finish(code, detail=""):
        if copied is not None:
            try:
                remove_copy(copied)
            except Exception:
                # 清不掉一個暫存檔不足以把一次成功的佈建說成失敗。
                pass
        return code, ("" if code == EXIT_OK else _message(code, detail))

    outcome = stage(source)
    if not outcome.ok:
        return finish(EXIT_STAGE_FAILED, outcome.error_text)
    report("套件已備妥，接著登記給這台電腦的所有使用者。")

    family_name = find_family_name(identity_name, publisher)
    if not family_name:
        return finish(EXIT_FAMILY_UNKNOWN)

    outcome = provision(family_name)
    if not outcome.ok:
        # 佈建失敗就當場取消佈建（第四題）：子行程當下還是提權的，清理不用
        # 再跳一次 UAC。清理本身失敗只是資訊，不再算成另一個失敗——使用者
        # 得到的結果一樣是「沒有裝成全機器」。
        try:
            deprovision(family_name)
        except Exception:
            pass
        return finish(EXIT_PROVISION_FAILED, outcome.error_text)

    return finish(EXIT_OK)


def write_report(path, message):
    """把要回報給主行程的那一段寫成檔案。

    子行程沒有主控台，結束碼也只夠說出「哪一類失敗」；系統給的那段完整
    說明要走這裡才到得了完成畫面。寫不出來時不拋例外——為了寫不出一份
    報告而讓子行程崩潰，會把一次成功變成一次不明的失敗。
    """
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(message or "")
    except OSError:
        pass


def read_report(path):
    """讀回報檔；讀不到就回傳空字串。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
