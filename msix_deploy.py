"""
msix_deploy.py
---------------
請求 Windows 的套件引擎部署／移除 MSIX 套件。這是 MSIX 引擎的核心動作——
應用程式檔案的落地由系統接管，「保證乾淨解除安裝」這個採用 MSIX 的理由
就是從這裡來的。

## 接縫

第二輪決議第十五項要求把這個動作設計為可注入的參數，且該設計須自實作第一天
即成立：實際部署耗時，失敗時會在開發機留下殘留，而事後才拆解接縫會讓測試
被寫成「描述實作實際做了什麼」而非「描述它應該做什麼」。

注入的是 `PackageManager` 本身而非更上層的包裝，因為需要被測到的正是「拿到
非同步操作之後怎麼判斷成敗」那一段——那裡有一個會產生「安裝失敗卻回報成功」
的陷阱（見下）。

## 三個來自實測的事實

1. **一律用 `get()`，不用 `get_results()`。** 後者不等待操作完成，在操作
   仍進行中呼叫會回傳一個 `extended_error_code = 0`、`error_text = ""`、
   `is_registered = False` 的結果——與成功難以區分。誤用它會做出「安裝失敗
   卻回報成功」的安裝檔，而且本機不易顯現（本機部署快，看起來就像成功）。
   出處：規劃文件第三輪 spike 結果第四項。
2. **判斷成敗看 `is_registered`，不是只看例外或錯誤碼。** 同上。
3. **綁定把 WinRT 的多載拆成不同名稱，與官方 API 文件不同。** 列舉當前
   使用者的套件是 `find_packages_by_user_security_id("")`（沒有
   `find_packages_for_user`）；移除是 `remove_package_async(full_name)`
   單一參數，帶 `RemovalOptions` 的版本另名為
   `remove_package_with_options_async`。以兩個參數呼叫前者會得到
   `TypeError: Invalid parameter count`。出處：規劃文件第十一輪 CI 探針結果
   第六項（首次執行即因此中止）。

## 錯誤訊息

`DeploymentResult.error_text` 提供的是完整且已在地化的說明文字，不是只有
錯誤碼——例如「錯誤 0x800B0109: 應用程式套件或套件組合中之簽章的根憑證
必須受信任。」。直接轉呈該欄位即可，不需要另外編一則訊息（規劃文件第三輪
spike 結果第七項的附帶發現）。
"""
import os
import urllib.parse
from collections import namedtuple


class Outcome(namedtuple("Outcome", "ok error_text error_code")):
    """一次部署或移除的結果。

    `error_text` 在失敗時是可以直接顯示給使用者看的完整說明（見模組說明）。
    """

    __slots__ = ()


def _default_manager():
    """建立真正的 `PackageManager`。

    延遲匯入：綁定套件只有 MSIX 引擎用得到，而這個模組會被安裝端一併帶著
    走。在匯入時就要求它存在，會讓傳統引擎的安裝檔也綁上這個相依。
    """
    from winrt.windows.management.deployment import PackageManager
    return PackageManager()


def _resolve_manager(manager, manager_factory):
    if manager is not None:
        return manager, None
    factory = manager_factory or _default_manager
    try:
        return factory(), None
    except Exception as e:
        return None, (
            f"無法使用 Windows 的套件部署介面：{e}。"
            "這個功能需要 winrt-* 綁定套件，安裝檔在打包時應該已經一併帶上它。"
        )


def _outcome_from(result):
    """把系統回傳的結果翻成本模組的 Outcome。

    `is_registered` 是「真的裝上去了」的唯一可靠依據。錯誤碼為 0、錯誤訊息
    為空、但 `is_registered` 是 False 的組合確實會出現（見模組說明），因此
    不能只看錯誤欄位。
    """
    error_text = getattr(result, "error_text", "") or ""
    error_code = getattr(result, "extended_error_code", None)
    registered = bool(getattr(result, "is_registered", False))
    if registered and not error_text:
        return Outcome(True, "", error_code)
    if not error_text:
        error_text = (
            "系統沒有回報失敗的原因，但套件並未完成註冊。"
            "這通常代表部署在中途被中止。"
        )
    return Outcome(False, error_text, error_code)


def _await_operation(operation, progress=None):
    """掛上進度回報並等待操作完成。

    **用 `get()`，不用 `get_results()`**——理由見模組說明，這是本模組最容易
    寫錯、且錯了不會被本機測試抓到的一行。
    """
    if progress is not None:
        def on_progress(_sender, value):
            percentage = getattr(value, "percentage", None)
            if percentage is not None:
                progress(percentage)

        operation.progress = on_progress
    return operation.get()


def deploy(package_path, manager=None, manager_factory=None, progress=None):
    """請求系統部署一份 `.msix`，回傳 `Outcome`。

    `progress` 收到 0 到 100 的整數。第十一輪 CI 探針確認該回報在真實部署
    中會實際觸發、且是真實進度（實測 12 次），因此進度條不需要退化為不確定
    動畫（第二輪決議第六項所訂的備案不會被觸發）。
    """
    manager, error = _resolve_manager(manager, manager_factory)
    if error:
        return Outcome(False, error, None)
    try:
        uri = _deployment_uri(package_path)
        operation = manager.add_package_async(uri, [], _deployment_options())
        result = _await_operation(operation, progress)
    except Exception as e:
        return Outcome(False, f"請求系統部署套件時失敗：{e}", None)
    return _outcome_from(result)


def find_installed(identity_name, manager=None, manager_factory=None):
    """找出已安裝的同名套件，回傳它的完整名稱；沒有則回傳 None。

    只查當前使用者的套件。列舉全機器所有使用者的套件會被系統以權限不足
    拒絕，那是正確的權限檢查而非故障（第三輪 spike 結果第二項）。
    """
    manager, error = _resolve_manager(manager, manager_factory)
    if error:
        return None
    try:
        packages = manager.find_packages_by_user_security_id("")
        for package in packages:
            if package.id.name == identity_name:
                return package.id.full_name
    except Exception:
        # 查不到就是查不到——呼叫端的處置（例如「沒有舊版就直接裝」）在兩種
        # 情況下相同，把例外往上拋只會讓呼叫端多一段一樣的處理。
        return None
    return None


def remove(package_full_name, manager=None, manager_factory=None, progress=None):
    """請求系統移除一份已安裝的套件，回傳 `Outcome`。

    以單一參數呼叫 `remove_package_async`（見模組說明第三點）。
    """
    manager, error = _resolve_manager(manager, manager_factory)
    if error:
        return Outcome(False, error, None)
    try:
        operation = manager.remove_package_async(package_full_name)
        result = _await_operation(operation, progress)
    except Exception as e:
        return Outcome(False, f"請求系統移除套件時失敗：{e}", None)
    outcome = _outcome_from(result)
    # 移除成功時 is_registered 的語意與部署相反，不能沿用同一套判斷：這裡
    # 只以「有沒有錯誤訊息」為準。
    error_text = getattr(result, "error_text", "") or ""
    if not error_text:
        return Outcome(True, "", getattr(result, "extended_error_code", None))
    return Outcome(False, error_text, outcome.error_code)


def _file_uri(package_path):
    """把本機路徑轉成部署介面要的 URI 字串。

    **一定要做百分比編碼**（稽核 S4）。修正前是字串直接相接：套件路徑來自
    `sys._MEIPASS`，也就是使用者的 `%TEMP%`，其中含使用者名稱——而 Windows
    帳號名稱允許 `#`。`#` 在 URI 裡是片段的起點，路徑會從那裡被截斷，部署
    因此找不到檔案，而錯誤訊息不會提到帳號名稱。`%` 與空白同理。

    `safe="/:"` 保留磁碟機代號的冒號與路徑分隔符。編碼過頭會讓 URI 不再
    指向同一個檔案，那與不編碼是同一種錯誤的另一半。
    """
    path = os.path.abspath(package_path).replace(os.sep, "/")
    return "file:///" + urllib.parse.quote(path, safe="/:")


def _deployment_uri(package_path):
    """把路徑包成部署介面吃的 `Uri` 物件。

    winrt 不在時直接回傳字串——測試注入替身時不需要真的綁定套件，替身只
    看得到路徑。字串的組法與編碼在 `_file_uri()`，兩條路徑共用同一份。
    """
    uri = _file_uri(package_path)
    try:
        from winrt.windows.foundation import Uri
    except Exception:
        return uri
    return Uri(uri)


def _deployment_options():
    try:
        from winrt.windows.management.deployment import DeploymentOptions
    except Exception:
        return None
    return DeploymentOptions.NONE
