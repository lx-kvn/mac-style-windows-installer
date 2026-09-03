"""驅動本機的驗證用虛擬機。

`docs/proposals/MSIX輸出規劃.md` 待辦清單上有幾項無法在 CI 上驗證：
GitHub Actions 只提供 `windows-latest`，沒有 1809 的 runner，也不是繁體
中文環境。這支模組把「要對哪台虛擬機做什麼」翻譯成 vmrun 的指令列，讓那些
項目能以腳本重複執行。

各台虛擬機的建立方式、快照命名與已確認的環境事實記在
`.claude/skills/run-1809-vm/SKILL.md`。

**密碼不寫進任何檔案。** 由 `password_from_env()` 依機器宣告的變數名稱從
環境變數讀取，比照 builder.py 的 `signing.cert_password_env` 作法。vmrun
的介面要求密碼以 `-gp`／`-vp` 出現在指令列上，這一點無法避免，因此輸出到
log 或錯誤訊息之前一律先經過 `_scrub()`。

run／log／sleep 是測試接縫（比照 builder.py 的 run 參數與 file_assoc.py
的 registry 參數），預設分別是 subprocess.run、不輸出、time.sleep。
"""
import collections
import contextlib
import io
import os
import re
import subprocess
import time


VMRUN = r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
VMWARE_EXE = r"C:\Program Files (x86)\VMware\VMware Workstation\vmware.exe"
PREFERENCES_INI = os.path.join(
    os.path.expandvars("%APPDATA%"), "VMware", "preferences.ini")

# 這支模組會送出的 vmrun 子指令。列成常數不只是為了自我說明——測試以它
# 辨識每次呼叫送出的是哪一個子指令，藉此斷言先後順序。
SUBCOMMANDS = frozenset({
    "revertToSnapshot",
    "start",
    "stop",
    "captureScreen",
    "CopyFileFromHostToGuest",
    "CopyFileFromGuestToHost",
    "runProgramInGuest",
})

# 判斷客體是否就緒時執行的程式：存在於任何 Windows、不需參數、瞬間結束。
READY_PROBE = r"C:\Windows\System32\whoami.exe"

REDACTED = "***"


Machine = collections.namedtuple(
    "Machine", "key vmx snapshot user password_env encryption_env profiles")

# 一張快照代表一種起始情境。快照名稱與登入帳號綁在同一個具名元組裡，因為
# 兩者必須成對——標準使用者的快照裡登入的是 `User` 而不是 `Tester`。分開記
# 會出現「拿管理員帳號去登入標準使用者快照」這種對不起來的組合，而 vmrun
# 回報的會是認證失敗，不會指向情境選錯。
Profile = collections.namedtuple("Profile", "key snapshot user note")


def _machine(profiles, **fields):
    """由 profiles 推出預設的 snapshot／user，避免同一份資料寫兩次。"""
    default = profiles["default"]
    return Machine(snapshot=default.snapshot, user=default.user,
                   profiles=profiles, **fields)


MACHINES = {
    # 驗證 MSIX 的 MinVersion 能否部署、企業版側載預設值、缺少 WebView2
    # Runtime 時安裝精靈的行為。組建停在 17763.316（不連網路，見 SKILL）。
    "win1809": _machine(
        {"default": Profile("default", "Clean", "Tester",
                            "單一 C 槽，Tester（管理員帳號）已登入")},
        key="win1809",
        vmx=r"D:\VMware\Win10-1809-LTSC\Windows10-1809-LTSC.vmx",
        password_env="WIN1809_VM_PASSWORD",
        encryption_env=None,
    ),
    # 真正的繁體中文 Windows 環境——CI 的 runner 是英文的，中文介面從未
    # 在真的中文系統上跑過。也用於新版 Windows 上的 MSIX 與憑證行為。
    #
    # encryption_env 不是可選的裝飾：Windows 11 要求 TPM 2.0，VMware 以
    # 虛擬 TPM 滿足它，而帶虛擬 TPM 的機器必須加密存放。沒帶 -vp 時連
    # 「列出快照」都會被回以 "A password is required for this operation"。
    #
    # 四張快照對應四種起始情境。`User` 與 `Tester` 共用同一個密碼，因此
    # 只有一個 password_env。
    "win11": _machine(
        {
            "default": Profile(
                "default", "Clean", "Tester",
                "單一 C 槽，Tester（管理員帳號）已登入"),
            "two_disks": Profile(
                "two_disks", "Clean_C:/E:", "Tester",
                "C 與 E 兩顆磁碟，用於驗證跨磁碟的安裝行為（稽核 F08）"),
            "standard_user": Profile(
                "standard_user", "Clean_User", "User",
                "單一 C 槽，User（標準使用者）已登入。與 interactive=True 的"
                "未提升權杖不同：這個帳號本身沒有管理員身分，無法經 UAC 提升"),
            "standard_user_two_disks": Profile(
                "standard_user_two_disks", "Clean_User_C:/E:", "User",
                "兩顆磁碟 + 標準使用者，同時涵蓋前兩種情境"),
        },
        key="win11",
        vmx=r"D:\VMware\Windows11-25h2\Windows11-25H2.vmx",
        password_env="WIN11_VM_PASSWORD",
        encryption_env="WIN11_VM_ENCRYPTION_PASSWORD",
    ),
}


class VmError(Exception):
    """虛擬機操作失敗。訊息一律不含密碼。"""


def machine(key):
    """依代號取出機器定義。找不到時把可選的代號一併說出來。"""
    try:
        return MACHINES[key]
    except KeyError:
        raise VmError(
            "沒有代號為 " + repr(key) + " 的虛擬機。可用的有："
            + "、".join(sorted(MACHINES))
        )


def _as_machine(value):
    return value if isinstance(value, Machine) else machine(value)


def password_from_env(name, environ=None):
    """讀出指定環境變數裡的密碼。

    空字串視同未設定。若讓空密碼通過，後續失敗會出現在 vmrun 的登入或
    解密階段，訊息是「認證失敗」——那會把人引去檢查帳號與虛擬機狀態，
    而真正的成因只是環境變數沒設。
    """
    environ = os.environ if environ is None else environ
    value = environ.get(name, "")
    if not value:
        raise VmError(
            "環境變數 " + name + " 沒有值。請先設定："
            "[Environment]::SetEnvironmentVariable('" + name
            + "','<密碼>','User')，設定後需重開終端機。"
        )
    return value


def connect(machine_or_key, environ=None, profile=None, **kwargs):
    """依機器定義組出一個 Vm，密碼從它宣告的環境變數讀取。

    未宣告 encryption_env 的機器不會去讀那個變數——未加密的機器不該因為
    少設一個與它無關的環境變數而無法使用。

    profile 選定起始情境，同時決定用哪張快照與哪個帳號登入（見 Profile）。
    省略時用 "default"。
    """
    target = _as_machine(machine_or_key)
    chosen = _profile(target, profile or "default")
    target = target._replace(snapshot=chosen.snapshot, user=chosen.user)
    environ = os.environ if environ is None else environ
    encryption = None
    if target.encryption_env:
        encryption = password_from_env(target.encryption_env, environ)
    return Vm(target,
              password_from_env(target.password_env, environ),
              encryption_password=encryption,
              **kwargs)


def _profile(machine, key):
    """取出情境定義。找不到時把可選的一併說出來。"""
    try:
        return machine.profiles[key]
    except KeyError:
        raise VmError(
            machine.key + " 沒有名為 " + repr(key) + " 的情境。可用的有："
            + "、".join(sorted(machine.profiles)))


def write_guest_script(path, text):
    """把要送進客體執行的 PowerShell 腳本寫成 UTF-8 with BOM。

    客體端是 Windows PowerShell 5.1，讀取無 BOM 的 `.ps1` 時以系統 ANSI
    編碼解讀。客體為 en-US 時中文字元因此被拆成無效 token，回報的是語法
    錯誤而不是編碼錯誤——症狀完全不指向成因。加上 BOM 之後 5.1 才會以
    UTF-8 解讀。

    先把既有的 CRLF 正規化再寫出，避免文字裡原本就有 CRLF 時被 Python 的
    換行轉換寫成 CR CR LF。
    """
    with io.open(path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(text.replace("\r\n", "\n"))


class Vm:
    def __init__(self, machine, password, encryption_password=None,
                 vmrun=VMRUN, run=None, log=None, sleep=None):
        self.machine = machine
        self.vmrun = vmrun
        self._password = password
        self._encryption_password = encryption_password
        self._run = run or subprocess.run
        self._log = log
        self._sleep = sleep or time.sleep

    # ---- 主機端操作（不進客體，因此不帶客體帳密）----

    def revert(self, snapshot=None):
        self._invoke("revertToSnapshot", [snapshot or self.machine.snapshot])

    def start(self, gui=False):
        """開機。gui=True 會開出 VMware 的主控台視窗。

        預設無畫面，因為多數驗證只要結果；但有些事只能用看的（例如缺少
        WebView2 Runtime 時安裝精靈畫面的樣子），而且人在旁邊盯著跑也比
        事後讀 log 容易發現不對勁。模式在開機時決定，開機後要改必須先關機
        重開——所以呼叫端應該在流程開始前就把這件事問清楚。
        """
        self._invoke("start", ["gui" if gui else "nogui"])

    def stop(self):
        self._invoke("stop", ["hard"])

    def wait_until_ready(self, attempts=60, delay=2):
        """等到客體真的接受指令為止。

        `vmrun start` 回來時客體可能還沒能接受指令，此時送檔案或執行程式
        會失敗，而失敗訊息（找不到檔案／認證失敗）不會指向「開太快」這個
        成因。

        **不以 checkToolsState 的字串判斷。** 那個指令對同一台虛擬機回過
        `running` 與 `installed` 兩種值；實測回報 `installed` 時，客體已經
        在正常桌面、`runProgramInGuest` 結束碼為 0、截圖也正常。把 `running`
        當成唯一的就緒條件，會在客體明明可用時空等到逾時（實際發生過一次，
        白等兩分鐘）。改成直接試一個最便宜的客體指令——那正是我們真正需要
        的能力，不必再從狀態字串推論。
        """
        for _ in range(attempts):
            probe = self._invoke(
                "runProgramInGuest", [READY_PROBE], guest=True, check=False)
            if probe.returncode == 0:
                return
            self._sleep(delay)
        raise VmError("等待客體就緒逾時（已嘗試 " + str(attempts) + " 次）。")

    # ---- 客體端操作 ----

    def copy_in(self, host_path, guest_path):
        self._invoke("CopyFileFromHostToGuest",
                     [host_path, guest_path], guest=True)

    def copy_out(self, guest_path, host_path):
        self._invoke("CopyFileFromGuestToHost",
                     [guest_path, host_path], guest=True)

    def capture_screen(self, host_path):
        """把客體當下的畫面存成 PNG。

        產出的檔案寫在主機端，但 VMware 仍把這歸類為客體操作，不帶帳密會
        回以 "Anonymous guest operations are not allowed"。

        無畫面模式下一樣可用（實測結束碼 0）。解析度不是固定值：實測有畫面
        模式為 2558x1190、無畫面模式為 2558x1186，比對兩張截圖時不要假設
        尺寸相同。
        """
        self._invoke("captureScreen", [host_path], guest=True)

    def run_program(self, program, *args, **kwargs):
        """在客體裡執行程式。

        **預設落在工作階段 0**，也就是服務用的階段，不是使用者看得到的
        桌面（實測：客體回報 SessionId 為 0）。因此以這種方式啟動的視窗
        程式不會出現在畫面上，截圖也拍不到。要在桌面上跑就傳
        interactive=True（實測回報 SessionId 為 1）。

        interactive=True 要求客體確實有人以互動方式登入。這個條件在正常
        流程下成立——快照恢復後就是 Tester 已登入的桌面——但若略過還原
        直接開機，客體是從硬碟冷開機、停在鎖定畫面，此時 vmrun 會回報
        「使用者必須以互動方式登入」。又一個「一定要先還原快照」的理由。

        客體程式拿到的是**已提升的權限**（實測 IsInRole(Administrator)
        為 True，且確實寫得進 HKLM 的側載機碼與 LocalMachine\\Root 憑證
        存放區），因此裝憑證與改側載設定都不需要額外處理。

        **客體端請用 powershell.exe，不要用 cmd.exe。** vmrun 會把每個參數
        各自加上引號再交給客體，powershell.exe 接受被引號包住的參數（實測
        `-NoProfile` `-Command` `<script>` 分開傳，客體確實執行並寫出檔案），
        但 cmd.exe 不接受被引號包住的 `/c`，會解析失敗——實測 `/c` `ver`
        分開傳時回報結束碼 1，把 `"/c ver"` 併成單一參數才會成功。症狀是
        「指令明明沒錯卻回報失敗」，不會指向引號。

        **回傳的 returncode 是 vmrun 自己的結束碼，不是客體程式的。** 客體
        回傳任何非零值時，vmrun 一律回報 1（實測：客體 `exit 3`，這裡拿到
        的是 1）。因此只能判斷成敗，不能用它區分不同的失敗原因——要區分就
        得讓客體把結果寫進檔案再 copy_out() 取回。驗證「側載預設為關閉」時
        尤其重要：光是「失敗了」不足以歸因，必須另外讀回登錄值佐證失敗確實
        來自側載設定，而不是別的原因。

        check=False 時客體程式的非零結束碼不視為錯誤——有些情境預期它失敗
        （例如驗證側載預設為關閉時，部署本來就該被拒絕），把那當成例外會讓
        「測到了預期中的失敗」與「工具本身壞掉」混為一談。
        """
        check = kwargs.pop("check", True)
        interactive = kwargs.pop("interactive", False)
        if kwargs:
            raise TypeError("未預期的參數：" + ", ".join(sorted(kwargs)))
        head = ["-interactive"] if interactive else []
        return self._invoke("runProgramInGuest", head + [program] + list(args),
                            guest=True, check=check)

    # ---- 內部 ----

    def _invoke(self, subcommand, args=(), guest=False, check=True):
        cmd = [self.vmrun, "-T", "ws"]
        # 加密的機器連還原、開機、列快照都要帶 -vp，不只客體操作。
        if self._encryption_password:
            cmd += ["-vp", self._encryption_password]
        if guest:
            cmd += ["-gu", self.machine.user, "-gp", self._password]
        cmd += [subcommand, self.machine.vmx] + [str(arg) for arg in args]
        if self._log:
            self._log(" ".join(self._redact(cmd)))
        # errors="replace"：解碼失敗時 stdout/stderr 會變成 None，下方的 detail
        # 變成空字串，VmError 只剩結束碼。詳見 docs/investigations/子行程輸出的解碼修正.md。
        result = self._run(cmd, capture_output=True, text=True, errors="replace")
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise VmError(
                "vmrun " + subcommand + " 失敗（結束碼 "
                + str(result.returncode) + "）：" + self._scrub(detail)
            )
        return result

    def _secrets(self):
        return [s for s in (self._password, self._encryption_password) if s]

    def _redact(self, cmd):
        secrets = self._secrets()
        return [REDACTED if token in secrets else token for token in cmd]

    def _scrub(self, text):
        for secret in self._secrets():
            text = text.replace(secret, REDACTED)
        return text


_TAB_FILE = re.compile(
    r'^pref\.ws\.session\.window\d+\.tab\d+\.file\s*=\s*"(.*)"\s*$')


def open_tabs(preferences_path=PREFERENCES_INI):
    """讀出 Workstation 目前在分頁列上開著哪些虛擬機。

    Workstation 把這件事寫進 preferences.ini，因此不需要去問視窗。首頁
    分頁的 file 是空字串，不是一台虛擬機，略過。

    **這份檔案不是即時的**：實測分頁狀態改變後約十五到二十秒才落到檔案上。
    因此讀到的是「稍早的樣子」，判斷剛剛發生的變化時要把這一點算進去
    （見 preserved_tab 對此的處理）。

    Workstation 沒開過時檔案可能不存在，那不是錯誤，回傳空清單。

    以 UTF-8 讀取並容錯，因為讀不出路徑最多讓「分頁本來就開著」判成沒開，
    後果是少補一個分頁；為此讓整個驗證流程中斷並不划算。
    """
    if not os.path.exists(preferences_path):
        return []
    found = []
    with io.open(preferences_path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            matched = _TAB_FILE.match(line.strip())
            if matched and matched.group(1):
                found.append(matched.group(1))
    return found


def reopen_tab(vmx, vmware=VMWARE_EXE, run=None):
    """在 Workstation 裡重新開出該虛擬機的分頁。

    `vmware.exe -t` 只開分頁、不改變電源狀態（實測後 `vmrun list` 仍為 0）。
    副作用是 Workstation 的視窗會被叫到最前面（實測前景由 brave 變成
    vmware），所以只在原本就開著分頁時呼叫——見 preserved_tab()。
    """
    (run or subprocess.run)([vmware, "-t", vmx])


def _tab_key(path):
    """把路徑正規化成可比對的形式。

    Windows 路徑不分大小寫，而 Workstation 寫回設定檔時的大小寫不保證與
    呼叫端給的一致。不使用 os.path.normcase，因為它在非 Windows 平台上原樣
    回傳，會讓比對結果隨執行平台而異。
    """
    return os.path.normpath(path).replace("/", "\\").lower()


@contextlib.contextmanager
def preserved_tab(vmx, list_tabs=None, reopen=None):
    """讓無畫面執行不會把使用者原本開著的分頁弄不見。

    以 nogui 啟動虛擬機時，Workstation 沒有主控台可以顯示，會把該虛擬機的
    分頁收掉；收分頁的當下它還會把自己叫到最前面。這個包裝器在結束時把
    分頁補回去，讓畫面回到使用者交出去時的樣子。

    **只補原本就開著的分頁。** 使用者沒開時什麼都不做——補分頁本身會搶走
    前景焦點，對沒在看虛擬機的人而言那是無故的打斷。

    補分頁放在 finally，因為驗證失敗時更需要把畫面還原。

    結束時的「分頁還在嗎」是**盡力而為**的判斷，因為 open_tabs() 讀到的
    檔案落後現況約十五到二十秒。實務上不成問題：分頁是在流程一開始啟動
    虛擬機時就被收掉的，等到流程結束早已寫進檔案。判斷失準的後果也有限
    ——誤判為「還在」會少補一個分頁，誤判為「不在」會多開一次已經開著的
    分頁；兩者都只是小麻煩，不值得為此在流程尾端多等二十秒。
    """
    list_tabs = list_tabs or open_tabs
    reopen = reopen or reopen_tab
    key = _tab_key(vmx)
    was_open = key in [_tab_key(path) for path in list_tabs()]
    try:
        yield
    finally:
        if was_open and key not in [_tab_key(path) for path in list_tabs()]:
            reopen(vmx)


def fresh_boot(vm, gui=False):
    """把虛擬機帶回它的快照狀態並開到可用為止。

    每一輪驗證都從這裡開始，理由有兩個。其一，若在殘留狀態上執行，測到的
    可能是上一輪留下的東西——例如上一輪為了驗證部署而開啟的側載設定，會讓
    下一輪「側載預設是關的」測出相反的結果。其二，略過還原直接開機是從硬碟
    冷開機，客體會停在鎖定畫面（實測截圖確認），沒有互動登入，
    run_program(interactive=True) 會被拒絕。

    gui 決定這一輪看不看得到畫面，在此定案並往下傳。中途改不了：切換模式
    必須關機重開，而關機重開等於這一輪從頭來過。
    """
    vm.revert()
    vm.start(gui=gui)
    vm.wait_until_ready()
