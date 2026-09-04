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

# 占用協調由獨立的 vm-lease 提供，不留在這個 repo 裡面。搬出去的理由是它
# 不屬於這個專案：同一批虛擬機也被 FileLocker 使用，規則留在其中一個 repo
# 裡面時，另一邊就得知道這個 repo 的路徑才協調得起來，而規則的說明會落在
# 沒有進版的地方（`.claude/` 被 .gitignore 排除）。
#
# 以 vm_lock 這個名字匯入，是因為這個模組原本就這樣稱呼它；改名會讓這一次
# 的搬移混進一批與搬移無關的更名。
try:
    import vm_lease as vm_lock
    from vm_lease import machines
except ImportError as _error:  # pragma: no cover - 只在沒安裝時走到
    raise ImportError(
        "找不到 vm_lease。虛擬機的占用協調由獨立的 vm-lease 提供，請先安裝：\n"
        r"    pip install -e D:\Github\vm-lease_專案\vm-lease" "\n"
        "（用法見該 repo 的 docs/使用說明書.md）"
    ) from _error


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


# 機器清單不定義在這裡，改由 vm-lease 提供（`vm-lease machines list` 可以
# 直接看）。清單描述的是「這台實體電腦上有哪些虛擬機」，跟哪個專案在用無關
# ——與占用租約同一種性質。寫在這個 repo 裡的後果已經發生過：FileLocker 必須
# 知道這個 repo 的路徑才開得了虛擬機。
#
# Machine 與 Profile 也一併沿用該模組的定義，不在這裡另立一份同名的類別。
Machine = machines.Machine
Profile = machines.Profile


def all_machines():
    """讀出這台電腦上登記過的虛擬機。清單不存在時的訊息會指出下一步。"""
    try:
        return machines.load()
    except machines.MachineListError as error:
        raise VmError(str(error)) from None

# VmError 定義在 vm_lease 而不是這裡，因為占用協調的錯誤（機器被別人佔著）
# 跟操作失敗是同一類事情，呼叫端理應用同一個 except 接住。在這裡再定義一個
# 同名類別會變成兩個不同的例外型別，catch 得到一個、漏掉另一個。
VmError = vm_lock.VmError
VmBusy = vm_lock.VmBusy
LeaseLost = vm_lock.LeaseLost

# 占用協調的公開介面就掛在 vms 底下，呼叫端不必再多 import 一個模組。
acquire = vm_lock.acquire
release = vm_lock.release
holder = vm_lock.holder
reserved = vm_lock.reserved
renew = vm_lock.renew


def machine(key):
    """依代號取出機器定義。找不到時把可選的代號一併說出來。"""
    found = all_machines()
    try:
        return found[key]
    except KeyError:
        raise VmError(
            "沒有代號為 " + repr(key) + " 的虛擬機。可用的有："
            + "、".join(sorted(found))
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


def connect(machine_or_key, environ=None, profile=None, reserve=True,
            purpose="", lock_minutes=vm_lock.DEFAULT_MINUTES, lock_dir=None,
            **kwargs):
    """依機器定義組出一個 Vm，密碼從它宣告的環境變數讀取。

    未宣告 encryption_env 的機器不會去讀那個變數——未加密的機器不該因為
    少設一個與它無關的環境變數而無法使用。

    profile 選定起始情境，同時決定用哪張快照與哪個帳號登入（見 Profile）。
    省略時用 "default"。

    這裡順手取得占用租約（見 vm-lease 的 docs/規格文件.md）——這台機器上可能同時有多個 agent
    session 在跑，而 revertToSnapshot 會把另一邊做到一半的工作無聲還原掉。
    協調掛在 connect 而不是掛在 revert：真正要保護的是「先佔住再慢慢做」
    這整段時間，等到送出還原指令那一瞬間才比對已經太晚，另一邊那時已經在
    這台機器上做了半小時的事。`purpose` 會寫進租約，另一邊被擋下來時看得到
    是誰、為了什麼佔著。用完請呼叫 `vms.release(機器代號)` 交回去。

    `reserve=False` 明確跳過協調，給「只是要組指令列、不會真的碰到機器」的
    用途（測試即是）。
    """
    target = _as_machine(machine_or_key)
    # with_profile 回傳一份新的機器定義，不動到清單上那一份——同一份清單會被
    # 多個呼叫端共用。
    target = target.with_profile(profile or "default")
    environ = os.environ if environ is None else environ
    encryption = None
    if target.encryption_env:
        encryption = password_from_env(target.encryption_env, environ)
    # 占用排在密碼解析之後——環境變數沒設就直接失敗，不要先佔住一台機器再
    # 因為設定問題離開，那會留下一筆沒人要用卻擋著別人的租約。
    lease = None
    if reserve:
        lease = vm_lock.acquire(target.key, purpose=purpose,
                                minutes=lock_minutes, lock_dir=lock_dir,
                                environ=environ)
    return Vm(target,
              password_from_env(target.password_env, environ),
              encryption_password=encryption,
              lease=lease, lock_dir=lock_dir, lock_minutes=lock_minutes,
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
                 vmrun=VMRUN, run=None, log=None, sleep=None,
                 lease=None, lock_dir=None,
                 lock_minutes=vm_lock.DEFAULT_MINUTES):
        self.machine = machine
        self.vmrun = vmrun
        self._password = password
        self._encryption_password = encryption_password
        self._run = run or subprocess.run
        self._log = log
        self._sleep = sleep or time.sleep
        # 租約與續租所需的一切。lease 為 None 時完全不碰協調機制——那是
        # reserve=False 的用途（只組指令列、不真的碰機器）。
        self._lease = lease
        self._lock_dir = lock_dir
        self._lock_minutes = lock_minutes
        self._lease_now = time.time

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

        **會停下來等使用者的程式一定要傳 no_wait=True。** vmrun 預設等客體
        程式結束；若那支程式正停在一個等人回答的對話框上，兩邊會互相等到
        逾時，而症狀是「指令沒有回來」，看不出成因（實際踩到過）。

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
        no_wait = kwargs.pop("no_wait", False)
        if kwargs:
            raise TypeError("未預期的參數：" + ", ".join(sorted(kwargs)))
        head = []
        if interactive:
            head.append("-interactive")
        if no_wait:
            head.append("-noWait")
        return self._invoke("runProgramInGuest", head + [program] + list(args),
                            guest=True, check=check)

    # ---- 內部 ----

    def _renew_lease(self):
        """每次真的要碰這台機器之前續租一次。

        這使租約時間的意思從「一次工作最多能做多久」變成「最後一次碰它之後
        多久視為離開」——前者猜不準，訂短了會在工作進行中被別人接手，而那種
        接手不會有任何錯誤訊息。

        比對編號後若發現租約已經不是自己的，`vm_lease.renew` 會拋 `LeaseLost`，
        這裡讓它往上拋，且**在送出指令之前**——另一邊可能正在這台機器上工作。
        """
        if self._lease is None:
            return
        self._lease = vm_lock.renew(
            self.machine.key, owner=self._lease.owner,
            token=self._lease.token, minutes=self._lock_minutes,
            now=self._lease_now(), lock_dir=self._lock_dir)

    def _invoke(self, subcommand, args=(), guest=False, check=True):
        self._renew_lease()
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


_CDROM_DEVICE = re.compile(
    r'^\s*([\w:]+)\.deviceType\s*=\s*"cdrom-(?:image|raw)"\s*$')


def set_cdrom_image(vmx_text, iso_path):
    """把 vmx 文字裡既有的光碟機指到 iso_path，並設為開機時連線。

    存在的理由是速度：`CopyFileFromHostToGuest` 走 VMware Tools 的控制通道，
    那條管線是設計來傳設定值這類小東西的，實測 GB 級別的檔案只有 1.8 MB/s
    ——2.23 GB 要跑二十分鐘。改由虛擬光碟讀取，客體是以虛擬磁碟的速度存取
    主機上的檔案，且安裝檔可以直接從光碟執行，複製那一步整個消失。

    只改既有的光碟機，不新增硬體。回傳新的文字，不碰檔案——寫入的時機
    （必須在關機狀態下）由呼叫端決定，見 attach_iso()。

    重複套用的結果與套用一次相同：每次還原快照之後都會重新套用，若每次都
    追加一行，檔案會越改越亂。
    """
    lines = vmx_text.splitlines()
    device = None
    for line in lines:
        matched = _CDROM_DEVICE.match(line)
        if matched:
            device = matched.group(1)
            break
    if not device:
        raise VmError("這台虛擬機的設定裡找不到光碟機，無法掛載 ISO。")

    wanted = {
        device + ".deviceType": "cdrom-image",
        device + ".fileName": iso_path,
        device + ".present": "TRUE",
        device + ".startConnected": "TRUE",
    }
    seen = set()
    result = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in wanted:
            result.append('%s = "%s"' % (key, wanted[key]))
            seen.add(key)
        else:
            result.append(line)
    for key in sorted(set(wanted) - seen):
        result.append('%s = "%s"' % (key, wanted[key]))

    ending = "\r\n" if "\r\n" in vmx_text else "\n"
    return ending.join(result) + (ending if vmx_text.endswith(("\n", "\r")) else "")


_VMX_ENCODING = re.compile(r'^\s*\.encoding\s*=\s*"([^"]+)"', re.M)


def vmx_encoding(raw_bytes):
    """讀出 `.vmx` 第一行宣告的編碼。

    這個檔案自己宣告用什麼編碼寫成（實測本機這兩台是 Big5）。一律以 UTF-8
    讀寫會在檔案裡出現非 ASCII 字元時把設定檔寫壞——虛擬機名稱用中文就會
    踩到，而症狀是虛擬機開不起來，沒有人會聯想到是掛 ISO 造成的。

    宣告行本身必為 ASCII，因此先以 latin-1 解出那一行（latin-1 不會對任何
    位元組拋例外），再用讀到的編碼解整個檔案。沒有宣告時用 UTF-8。
    """
    header = raw_bytes[:512].decode("latin-1", errors="replace")
    matched = _VMX_ENCODING.search(header)
    return matched.group(1) if matched else "utf-8"


def read_vmx(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    return raw.decode(vmx_encoding(raw), errors="replace")


def write_vmx(path, text):
    with open(path, "rb") as handle:
        encoding = vmx_encoding(handle.read())
    with open(path, "wb") as handle:
        handle.write(text.encode(encoding, errors="replace"))


def attach_iso(vm, iso_path):
    """把 ISO 掛到虛擬機的光碟機上。**必須在關機狀態下呼叫。**

    寫入 `.vmx` 這個動作在虛擬機執行中會被 VMware 覆寫回去，因此呼叫順序是
    「還原快照 → 恢復 → 關機 → 掛載 → 冷開機」（見 fresh_boot）。還原會把
    設定還原成快照當時的樣子，所以每一輪都要重新掛一次；也因為如此，這個
    改動不會留下永久痕跡。
    """
    write_vmx(vm.machine.vmx, set_cdrom_image(read_vmx(vm.machine.vmx),
                                              iso_path))


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


def fresh_boot(vm, gui=False, iso=None, attach=None):
    """把虛擬機帶回它的快照狀態並開到可用為止。

    每一輪驗證都從這裡開始，理由有兩個。其一，若在殘留狀態上執行，測到的
    可能是上一輪留下的東西——例如上一輪為了驗證部署而開啟的側載設定，會讓
    下一輪「側載預設是關的」測出相反的結果。其二，略過還原直接開機是從硬碟
    冷開機，客體會停在鎖定畫面（實測截圖確認），沒有互動登入，
    run_program(interactive=True) 會被拒絕。

    gui 決定這一輪看不看得到畫面，在此定案並往下傳。中途改不了：切換模式
    必須關機重開，而關機重開等於這一輪從頭來過。

    iso 不為 None 時把該映像掛到客體的光碟機上，代價是多一次關機與冷開機
    （見下方註解）。GB 級別的檔案用這條路送進客體：VMware Tools 的檔案傳輸
    實測只有 1.8 MB/s，2.23 GB 要跑二十分鐘，而做一片 4 GB 的 ISO 只要
    3.8 秒、冷開機 17.8 秒。attach 是測試接縫。
    """
    vm.revert()
    if iso:
        # `startConnected` 只在冷開機時套用。實測：還原後掛上 ISO 再恢復，
        # 客體回報「媒體已載入 = False」，連在客體內重新開機也無效——恢復
        # 時裝置狀態是從記憶體映像還原的，不重新列舉硬體。先恢復一次再強制
        # 關機把記憶體狀態丟掉，之後那次 start 才是真正的冷開機（實測 17.8
        # 秒，客體隨即看得到光碟）。
        #
        # 掛載排在關機之後：虛擬機關機時 VMware 會重寫 .vmx，在那之前改有
        # 被覆寫的風險。
        vm.start(gui=gui)
        vm.wait_until_ready()
        vm.stop()
        (attach or attach_iso)(vm, iso)
    vm.start(gui=gui)
    vm.wait_until_ready()
    keep_awake(vm)


# 關閉閒置逾時的四項。`powercfg /change` 一次只吃一項，因此逐項送出。
_KEEP_AWAKE_SETTINGS = (
    "standby-timeout-ac", "hibernate-timeout-ac",
    "monitor-timeout-ac", "disk-timeout-ac",
)

POWERCFG = r"C:\Windows\System32\powercfg.exe"


def keep_awake(vm):
    """阻止客體在長時間操作途中進入睡眠。

    自動化全程沒有任何使用者輸入，Windows 因此認定客體閒置並依電源配置進入
    睡眠。實際發生過：送入一個 2.23 GB 的檔案時，客體在傳輸途中發出 ACPI S1
    睡眠要求，VMware 隨即暫停虛擬機並寫出記憶體映像，主機端拿到的錯誤是
    「虛擬機需要處於開機狀態」——訊息指向電源狀態，完全看不出成因是客體
    自己睡著了。數秒等級的操作永遠碰不到這條線，因此這個問題直到有 GB 級別
    的傳輸才浮現。

    在快照還原之後才套用，隨快照一起丟棄；快照本身維持原樣，不必為了自動化
    重拍。單項失敗不中斷流程——設定不成功時最壞的結果是回到原本的行為，
    為此讓整輪驗證中止並不划算。
    """
    for setting in _KEEP_AWAKE_SETTINGS:
        vm.run_program(POWERCFG, "/change", setting, "0", check=False)
