"""
msix_all_users.py
------------------
主行程這一端怎麼取得全機器範圍，以及取不到時跟使用者說什麼。

`msix_provision.py` 是那件事本身（在提權的行程裡跑），這個模組是它的另一
半：決定要不要提權、怎麼提權、拿不到權限或做失敗時降級成什麼樣子，以及
把發生的事講成一句使用者看得懂的話。

## 三條路

依 ADR-0013 與 2026-09-08 的 `/grill-with-docs`：

- **行程已經提權**——直接在這個行程裡做（第六題）。再跳一次 UAC 只是問一個
  已經有答案的問題。
- **靜默安裝且未提權**——不跳 UAC（第六題）。那個視窗會讓無人值守的部署卡
  在那裡直到逾時，也違背呼叫端「不要詢問」的前提。改為降級，把發生的事寫
  進紀錄，結束碼仍是 0。
- **其餘情形**——`Setup.exe` 自己帶內部旗標再跑一次，提權執行（決定三、
  第一題）。不預先詢問（第二題）：決定四已經定了降級，事前再問一次等於同
  一件事問兩遍，而使用者當下能給的答案就是 UAC 那個視窗本身。

## 降級不是安裝失敗

這裡回報的 `ok` 只說「有沒有裝成全機器」。安裝本身的成敗由後續替當前使用者
註冊那一步決定——套件本身沒問題，使用者要的東西其實拿得到，中止的話他手上
什麼都沒有（第三題）。因此四種不如意（使用者取消、無法提權、子行程失敗、
靜默且未提權）全都回到同一個結果：只裝給目前這位使用者，並說明實際發生的事。

**語氣分兩種**：使用者按下取消是他的決定，用「失敗」形容會讓他去找一個不
存在的故障；其餘三種是故障，要講清楚是什麼壞了（第二題末段）。

## 說明走 `warnings`

ADR-0015 那一輪已經建好一條路：後端回傳的 `warnings` 陣列，前端在成功畫面
下方逐條以警示樣式列出，而靜默安裝也已經會把它逐條寫進 `/LOG=`。互動與靜默
兩邊因此一次到位（第九題）。

成功時不放任何一則進去——那個陣列在畫面上是警示樣式，放一則進去等於用紅色
的字說一件沒有出錯的事。成功時要交代的那件事（其他使用者要先啟動一次應用
程式，他那一端的安裝才算完成）改走 `log`。
"""
import os
import tempfile
from collections import namedtuple

import elevate
import messages
import msix_provision

FLAG_PACKAGE = "/PROVISION="
FLAG_DIGEST = "/PROVISION-HASH="
FLAG_REPORT = "/PROVISION-REPORT="

# 子行程是一顆 onefile 的安裝檔，啟動時要先把自己解壓一次——GB 級的安裝檔
# 在慢速磁碟上那一段就要好幾分鐘。取的是「久到不可能是正常情形」的值。
CHILD_TIMEOUT_MS = 600000

Result = namedtuple("Result", "ok warning")

MESSAGES = {
    "zh-TW": {
        "declined":
            "這個安裝檔設定為安裝給這台電腦上的所有使用者，但沒有取得系統管理員"
            "權限，因此只安裝給目前這位使用者。這台電腦的其他使用者不會取得這個"
            "應用程式。",
        "elevation_failed":
            "無法以系統管理員權限執行「登記給所有使用者」這個步驟（{detail}），"
            "因此只安裝給目前這位使用者。這台電腦的其他使用者不會取得這個應用程式。",
        "silent_no_admin":
            "這個安裝檔設定為安裝給這台電腦上的所有使用者，但靜默安裝不會跳出"
            "權限要求，而目前這個行程沒有系統管理員權限，因此只安裝給目前這位"
            "使用者。要裝給所有使用者，請以系統管理員身分執行這個安裝程式。",
        "provision_failed":
            "已取得系統管理員權限，但登記給這台電腦所有使用者的步驟失敗："
            "{detail} 這個應用程式只安裝給目前這位使用者，其他使用者不會取得它。",
        "child_failed_without_reason":
            "登記給所有使用者的步驟以結束碼 {code} 結束，沒有留下原因。",
        "provisioned":
            "已登記給這台電腦上的所有使用者。其他使用者要在自己登入後啟動一次"
            "這個應用程式，他那一端的安裝才會完成；在那之前他的檔案關聯不會生效。",
    },
    "en": {
        "declined":
            "This installer is set to install for every user on this machine, but "
            "administrator rights were not granted, so it was installed for the "
            "current user only. Other users on this machine will not get this "
            "application.",
        "elevation_failed":
            "The step that registers the application for every user could not be "
            "run with administrator rights ({detail}), so it was installed for the "
            "current user only. Other users on this machine will not get this "
            "application.",
        "silent_no_admin":
            "This installer is set to install for every user on this machine, but "
            "a silent installation never raises a permission prompt and this "
            "process does not have administrator rights, so it was installed for "
            "the current user only. To install for every user, run this installer "
            "as an administrator.",
        "provision_failed":
            "Administrator rights were granted, but registering the application "
            "for every user on this machine failed: {detail} The application was "
            "installed for the current user only; other users will not get it.",
        "child_failed_without_reason":
            "The step that registers the application for every user ended with "
            "exit code {code} and left no reason behind.",
        "provisioned":
            "Registered for every user on this machine. Every other user has to "
            "sign in and start this application once before their own installation "
            "completes; until then their file associations do not take effect.",
    },
}


def _t(key, lang=messages.DEFAULT_LANGUAGE, /, **params):
    return messages.translate(MESSAGES, key, lang, **params)


def build_arguments(package_path, digest, report_path):
    """子行程的命令列。引號由 `elevate` 那一層負責，這裡只給值。"""
    args = [FLAG_PACKAGE + package_path, FLAG_DIGEST + digest]
    if report_path:
        args.append(FLAG_REPORT + report_path)
    return args


def _value(argument, flag):
    return argument[len(flag):].strip().strip('"')


def parse_arguments(argv):
    """從命令列認出「這一趟是來佈建的」，回傳三個值；不是就回傳 None。

    沒有套件路徑就不算佈建模式：只認雜湊旗標會讓一個打錯的命令列走進佈建
    模式，然後以一個看不懂的錯誤結束。
    """
    parsed = {"package_path": "", "digest": "", "report_path": ""}
    for raw in argv:
        argument = raw.strip()
        upper = argument.upper()
        if upper.startswith(FLAG_PACKAGE):
            parsed["package_path"] = _value(argument, FLAG_PACKAGE)
        elif upper.startswith(FLAG_DIGEST):
            parsed["digest"] = _value(argument, FLAG_DIGEST)
        elif upper.startswith(FLAG_REPORT):
            parsed["report_path"] = _value(argument, FLAG_REPORT)
    return parsed if parsed["package_path"] else None


def run_as_child(parsed, identity_name, publisher="", write_report=None,
                 run_provisioning=None):
    """子行程那一趟：做完佈建、把說明寫進報告檔，回傳結束碼。

    例外一律收在這裡：子行程崩潰時主行程只看得到一個結束碼，讓例外把它帶走
    的話，主行程拿到的會是一個沒有意義的數字。
    """
    write_report = write_report or msix_provision.write_report
    run_provisioning = run_provisioning or msix_provision.run_provisioning
    try:
        code, message = run_provisioning(
            parsed["package_path"], parsed["digest"], identity_name, publisher)
    except Exception as e:
        code, message = msix_provision.EXIT_PROVISION_FAILED, f"未預期的錯誤：{e}"
    if parsed.get("report_path"):
        write_report(parsed["report_path"], message)
    return code


def _new_report_path():
    handle, path = tempfile.mkstemp(prefix="mswi-provision-", suffix=".txt")
    os.close(handle)
    return path


def _remove_report(path):
    try:
        os.remove(path)
    except OSError:
        pass


def provision_all_users(package_path, setup_exe, identity_name, publisher="",
                        digest=None, elevated=False, silent=False,
                        lang=messages.DEFAULT_LANGUAGE, launch=None,
                        run_in_process=None, report_path=None, read_report=None,
                        remove_report=None, log=None):
    """替這台電腦上的所有使用者登記這個套件，回傳 `Result(ok, warning)`。

    `ok` 為假時 `warning` 一定有內容——降級之後使用者看到的畫面與全機器範圍
    成功時完全一樣，沒有那一則的話「其他使用者拿不到」這件事無人知悉，而那
    正是 ADR-0009 決定三否決設定層面降級的理由（ADR-0013 決定四）。
    """
    launch = launch or elevate.run_elevated_and_wait
    run_in_process = run_in_process or msix_provision.run_provisioning
    read_report = read_report or msix_provision.read_report
    remove_report = remove_report or _remove_report

    def report(message):
        if log:
            log(message)

    def succeeded():
        report(_t("provisioned", lang))
        return Result(True, None)

    if elevated:
        code, message = run_in_process(package_path, digest or "", identity_name,
                                       publisher)
        if code == msix_provision.EXIT_OK:
            return succeeded()
        return Result(False, _t("provision_failed", lang, detail=message))

    if silent:
        return Result(False, _t("silent_no_admin", lang))

    path = (report_path or _new_report_path)()
    try:
        outcome = launch(setup_exe,
                         build_arguments(package_path, digest or "", path),
                         timeout_ms=CHILD_TIMEOUT_MS)
    except Exception as e:
        # 提權那一段爆掉時，使用者要的東西其實還拿得到——接下來的註冊會照常
        # 進行。讓例外往上跑等於把整次安裝賠進去。
        remove_report(path)
        return Result(False, _t("elevation_failed", lang, detail=str(e)))

    try:
        if outcome.status == elevate.DECLINED:
            # 使用者的決定，不是故障：這一則裡不出現「失敗」。
            return Result(False, _t("declined", lang))
        if outcome.status != elevate.OK:
            return Result(False, _t("elevation_failed", lang,
                                    detail=outcome.detail))
        if outcome.exit_code == msix_provision.EXIT_OK:
            return succeeded()
        detail = read_report(path) or _t("child_failed_without_reason", lang,
                                         code=outcome.exit_code)
        return Result(False, _t("provision_failed", lang, detail=detail))
    finally:
        remove_report(path)
