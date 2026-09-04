r"""虛擬機占用協調——避免同時在跑的多個 session 互相把對方的工作還原掉。

這台機器上的虛擬機不只這個 repo 在用：FileLocker repo 也透過 `vms.py`
驅動同一批機器（見兩邊各自的 `.claude/skills/run-test-vm/SKILL.md`），而
使用者有時會同時開著兩個 agent session。其中 `revertToSnapshot` 是破壞性
的——另一邊裝到一半的安裝程式、正在等的畫面，會在毫無徵兆的情況下被還原
掉，而且事後從症狀看不出原因（看起來只像是「剛才那步沒生效」）。

## 為什麼用檔案協調，不用 session 之間互相傳訊息

傳訊息要求對方此刻活著、而且會即時讀。兩者都不保證：另一個 session 可能
早已關閉，也可能正忙著別的事。而協調必須在動手之前就有答案——「發出去然後
等」在等不到回覆時只剩下卡住或硬幹兩條路。檔案相反：對方關掉之後他留下的
租約仍然讀得到，讀完當下就有答案，且不消耗任何額度。

## 租約，不是行程鎖

直覺的做法是把持有者的行程 ID 記進鎖檔，靠「那個行程還活著嗎」判斷是不是
殘留。不採用，因為每次執行腳本都是一個新的 python 行程、跑完就結束——同一個
session 在兩次操作之間就會失去鎖，另一邊剛好插進來就還原掉快照，正是這個
模組要防的事。改成記「誰佔的、佔到幾點」：同一個持有者再要就是續租，跨得過
行程邊界；時間到自動視為無人持有，卡住時會自己解開。

## 三道保護

1. **不可分割的占用**（`_guard`）——「讀檔 → 判斷 → 寫檔」被圍成一個作業
   系統層級的臨界區。沒有它時實測八個同時搶的人會八個都以為自己拿到了。
2. **編號**（`Lease.token`）——使用者拆鎖之後另一邊佔了進去，原持有者續租時
   會拿到 `LeaseLost` 而不是安靜地再佔一次。
3. **事件紀錄**（`log_path()`）——事後查得出某段時間機器是否換過手。曾發生
   一輪量測拿到看似合理、實則錯誤的數字而當下毫無錯誤訊息的情形。

租約放在 `%LocalAppData%\vm-locks\`，不放在任何一個 repo 裡面——佔用狀態是
這台實體機器的事實，跟哪個專案在用無關；放進某個 repo 會變成「另一個 repo
要知道第一個 repo 的路徑才能協調」，也可能被誤 commit 進版本紀錄。
"""
import contextlib
import ctypes
import datetime
import errno
import hashlib
import json
import os
import secrets
import time


# 每個持有者要能被別人認出來是誰，因此不接受匿名持有——匿名等於任何人都可以
# 續租任何人的租約，協調機制就退化成沒有。名字用 session 代號即可。
OWNER_ENV = "VM_LOCK_OWNER"

LOCK_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA")
    or os.path.expanduser(r"~\AppData\Local"),
    "vm-locks")

# 預設租期。續租綁在每一次碰虛擬機的動作上（見 vms.py），因此這個值的意思
# 不是「一次工作最多能做多久」，而是「最後一次碰它之後多久視為離開」。訂得
# 夠長，長到操作之間的空檔（讀結果、想下一步、等開機）不會誤判為離開；也訂
# 得夠短，短到 session 當掉時不會把機器擋太久。
#
# 交出畫面給人操作的情境是唯一的例外：那段時間確實沒有任何操作發生，呼叫端
# 要自己傳一個夠長的 minutes。
DEFAULT_MINUTES = 5

# 續租過了一半才真的寫檔。續租會被呼叫得很頻繁（每次操作一次），而在租期
# 前半段續租不會改變任何結果——省掉的是寫入，不是保護。
RENEW_AFTER_FRACTION = 0.5

LOG_NAME = "events.log"
# 超過就轉存一份，避免無限長大。只留一代——這份紀錄的用途是查最近發生的事，
# 不是長期稽核。
LOG_MAX_BYTES = 1 << 20

_NOTE = ("這是虛擬機占用租約，由 vm_lock.py 建立。到期後自動失效，不需要"
         "手動清理；要立刻放掉這台機器，直接刪掉這個檔案即可（原持有者下次"
         "續租時會收到 LeaseLost，不會安靜地把它佔回去）。")


class VmError(Exception):
    """虛擬機操作失敗。訊息一律不含密碼。"""


class VmBusy(VmError):
    """這台虛擬機正被別人佔用。"""


class LeaseLost(VmError):
    """手上這張租約已經不是你的了——別人接手，或它被拆掉之後由別人佔走。

    收到這個例外時必須停止操作那台虛擬機：另一邊可能正在上面工作，繼續下去
    就是這個模組要防的那件事。
    """


class Lease:
    """一筆占用租約。不使用具名元組，因為欄位會隨著記錄的東西增加而變動，
    位置式解包會在那時候安靜地錯位。"""

    __slots__ = ("vm", "owner", "purpose", "acquired_at", "expires_at", "token")

    def __init__(self, vm, owner, purpose, acquired_at, expires_at, token=""):
        self.vm = vm
        self.owner = owner
        self.purpose = purpose
        self.acquired_at = acquired_at
        self.expires_at = expires_at
        self.token = token

    def __repr__(self):
        return ("Lease(vm=%r, owner=%r, purpose=%r, expires_at=%r)"
                % (self.vm, self.owner, self.purpose, self.expires_at))

    def describe(self):
        purpose = ("（%s）" % self.purpose) if self.purpose else ""
        return ("虛擬機 %s 正被 %s 占用%s，租約到 %s"
                % (self.vm, self.owner, purpose, _clock(self.expires_at)))


def _clock(stamp):
    return datetime.datetime.fromtimestamp(stamp).strftime("%H:%M")


def _timestamp(stamp):
    return datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")


def _path(vm, lock_dir=None):
    return os.path.join(lock_dir or LOCK_DIR, "%s.lock" % vm)


def log_path(lock_dir=None):
    """事件紀錄的位置。與租約檔同一個資料夾——兩者是同一件事的兩面。"""
    return os.path.join(lock_dir or LOCK_DIR, LOG_NAME)


def _resolve_owner(owner, environ):
    if owner:
        return owner
    environ = os.environ if environ is None else environ
    from_env = environ.get(OWNER_ENV)
    if from_env:
        return from_env
    raise VmError(
        "不知道是誰要占用這台虛擬機。請設定環境變數 " + OWNER_ENV
        + "（用你的 session 代號即可，例如 filelocker-ca），或呼叫時明確傳入"
        " owner=。不接受匿名持有，否則任何人都能續租別人的租約。")


# --------------------------------------------------------------------------
# 臨界區
#
# 「讀檔 → 判斷 → 寫檔」必須是一個不可分割的動作。少了這一段時，兩個同時
# 開始的呼叫會都先讀到「沒人用」、然後都寫上去，後寫的蓋掉先寫的，而兩邊都
# 以為自己是持有者——實測八執行緒同時搶，八個全部拿到。
#
# Windows 上用作業系統的具名互斥鎖：它本質上不可能同時被兩個人拿到，而且
# 持有者的行程死掉時作業系統會自己放掉（回報 WAIT_ABANDONED），不需要用
# 「放超過幾秒就當它壞了」這種猜出來的門檻去拆殘留。
#
# 其他平台退回「只有檔案不存在時才建得起來」的號誌檔。那個建檔動作同樣是
# 不可分割的，只是殘留要靠時間判斷——這裡的臨界區只有幾次檔案操作，握著的
# 時間是微秒等級，因此門檻訂得很寬也不會誤拆活著的號誌。
# --------------------------------------------------------------------------
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_GUARD_TIMEOUT_MS = 10000
_GUARD_STALE_SECONDS = 30


def _guard_name(vm, lock_dir):
    """臨界區的名字。含資料夾——測試用暫存資料夾，不能跟真正在用的機器互卡。"""
    digest = hashlib.sha1(
        os.path.abspath(lock_dir or LOCK_DIR).encode("utf-8")).hexdigest()[:16]
    return "vm-lease-%s-%s" % (digest, vm)


@contextlib.contextmanager
def _guard_windows(name):
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise VmError("建立占用臨界區失敗（錯誤碼 %d）" % ctypes.GetLastError())
    try:
        result = kernel32.WaitForSingleObject(handle, _GUARD_TIMEOUT_MS)
        if result == _WAIT_TIMEOUT:
            raise VmError(
                "等待占用臨界區逾時。可能有另一個程序正卡在占用協調中，"
                "請確認沒有其他 session 卡住，或稍後再試。")
        if result not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            raise VmError("等待占用臨界區失敗（回傳 %d）" % result)
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
    finally:
        kernel32.CloseHandle(handle)


@contextlib.contextmanager
def _guard_posix(path):
    deadline = time.time() + _GUARD_TIMEOUT_MS / 1000.0
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
            try:
                if time.time() - os.path.getmtime(path) > _GUARD_STALE_SECONDS:
                    os.remove(path)
                    continue
            except OSError:
                continue
            if time.time() > deadline:
                raise VmError("等待占用臨界區逾時（號誌檔 %s）" % path)
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@contextlib.contextmanager
def _guard(vm, lock_dir=None):
    directory = lock_dir or LOCK_DIR
    os.makedirs(directory, exist_ok=True)
    if os.name == "nt":
        with _guard_windows(_guard_name(vm, directory)):
            yield
    else:
        with _guard_posix(os.path.join(directory, "%s.guard" % vm)):
            yield


# --------------------------------------------------------------------------
# 租約檔與事件紀錄
# --------------------------------------------------------------------------
def _read(path):
    """讀不到、讀不懂都回 None（視為無人持有）——壞掉的鎖檔沒有可信的持有者
    資訊，把機器永久鎖死比放行更糟，那需要人工介入才能恢復。"""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return Lease(data["vm"], data["owner"], data.get("purpose", ""),
                     float(data["acquired_at"]), float(data["expires_at"]),
                     data.get("token", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write(path, lease):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "vm": lease.vm,
        "owner": lease.owner,
        "purpose": lease.purpose,
        "token": lease.token,
        "acquired_at": lease.acquired_at,
        "expires_at": lease.expires_at,
        # 同樣的兩個時間再存一份人看得懂的格式——使用者打開這個檔案想知道的
        # 是「還要等多久」，不是 epoch 秒數。
        "acquired_at_local": _timestamp(lease.acquired_at),
        "expires_at_local": _timestamp(lease.expires_at),
        "note": _NOTE,
    }
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def _log(event, vm, owner, detail="", now=None, lock_dir=None):
    """一行一件事，追加寫入。

    紀錄本身不能讓占用失敗——寫紀錄出錯時放行，因為協調的正確性不依賴它。
    """
    now = time.time() if now is None else now
    path = log_path(lock_dir)
    line = "%s  %-8s %-14s %-16s %s\n" % (
        _timestamp(now), vm, event, owner, detail)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def holder(vm, now=None, lock_dir=None):
    """誰正佔著這台機器；沒有人（或已到期）時回 None。"""
    now = time.time() if now is None else now
    lease = _read(_path(vm, lock_dir))
    if lease is None or lease.expires_at <= now:
        return None
    return lease


def acquire(vm, owner=None, purpose="", minutes=DEFAULT_MINUTES, now=None,
            lock_dir=None, environ=None):
    """占用一台虛擬機。同一個持有者重複呼叫是續租，不是錯誤。"""
    now = time.time() if now is None else now
    owner = _resolve_owner(owner, environ)
    path = _path(vm, lock_dir)

    with _guard(vm, lock_dir):
        current = holder(vm, now=now, lock_dir=lock_dir)
        if current is not None and current.owner != owner:
            _log("refuse", vm, owner,
                 "被 %s 佔著，到 %s" % (current.owner,
                                        _clock(current.expires_at)),
                 now=now, lock_dir=lock_dir)
            raise VmBusy(
                current.describe()
                + "。等它到期、或請對方釋放；確定要搶過來時由使用者決定，"
                "刪掉 " + path + " 即可。")

        # 續租保留最初取得的時間、原本的用途與編號——續租不是一次新的占用，
        # 把 acquired_at 往前推會看不出這台機器實際上已經被佔多久，換一張新
        # 編號則會讓呼叫端手上那張失效。
        if current is not None:
            acquired_at = current.acquired_at
            token = current.token or secrets.token_hex(8)
            purpose = purpose or current.purpose
            event = "renew"
        else:
            acquired_at = now
            token = secrets.token_hex(8)
            event = "acquire"

        lease = Lease(vm, owner, purpose, acquired_at, now + minutes * 60,
                      token)
        _write(path, lease)
        _log(event, vm, owner, purpose, now=now, lock_dir=lock_dir)
        return lease


def renew(vm, owner=None, token=None, minutes=DEFAULT_MINUTES, now=None,
          lock_dir=None, environ=None):
    """延長手上這張租約。碰虛擬機的每個動作都會呼叫它。

    帶著 `token` 呼叫時會比對編號：對不上（或租約已不存在）代表這張租約已經
    不是你的，拋 `LeaseLost`。沒有這道比對時，使用者拆鎖、另一邊佔進來之後，
    原持有者會安靜地再佔一次，兩邊同時以為自己拿著。

    過了租期一半才真的寫檔（`RENEW_AFTER_FRACTION`）——前半段續租不改變任何
    結果，省掉的是寫入而不是保護。
    """
    now = time.time() if now is None else now
    owner = _resolve_owner(owner, environ)

    with _guard(vm, lock_dir):
        current = holder(vm, now=now, lock_dir=lock_dir)
        if current is None:
            raise LeaseLost(
                "虛擬機 %s 的租約已經不在了（到期或被拆掉），不能續租。"
                "請停止操作這台機器——可能已經有別人接手。" % vm)
        if current.owner != owner or (token and current.token != token):
            _log("lost", vm, owner, "現由 %s 持有" % current.owner,
                 now=now, lock_dir=lock_dir)
            raise LeaseLost(
                current.describe()
                + "，已經不是你的租約。請停止操作這台機器——另一邊可能正在"
                "上面工作。")

        remaining = current.expires_at - now
        if remaining > minutes * 60 * RENEW_AFTER_FRACTION:
            return current

        lease = Lease(vm, current.owner, current.purpose, current.acquired_at,
                      now + minutes * 60, current.token)
        _write(_path(vm, lock_dir), lease)
        _log("renew", vm, owner, current.purpose, now=now, lock_dir=lock_dir)
        return lease


def release(vm, owner=None, force=False, now=None, lock_dir=None,
            environ=None):
    """釋放。放掉不是自己的租約會被拒絕——那等於繞過整個協調機制；
    `force=True` 是留給使用者親自決定要拆鎖時的逃生門。"""
    now = time.time() if now is None else now
    path = _path(vm, lock_dir)

    with _guard(vm, lock_dir):
        current = holder(vm, now=now, lock_dir=lock_dir)
        if not force:
            owner = _resolve_owner(owner, environ)
            if current is not None and current.owner != owner:
                raise VmBusy(
                    current.describe() + "，不是你的租約，不能替它釋放。")

        try:
            os.remove(path)
        except OSError:
            return

        if force:
            _log("force-release", vm, owner or "(使用者)",
                 ("原持有者 %s" % current.owner) if current else "",
                 now=now, lock_dir=lock_dir)
        else:
            _log("release", vm, owner, "", now=now, lock_dir=lock_dir)


@contextlib.contextmanager
def reserved(vm, owner=None, purpose="", minutes=DEFAULT_MINUTES, now=None,
             lock_dir=None, environ=None):
    """單一腳本內從頭佔到尾的用法。中途拋例外也會釋放。"""
    lease = acquire(vm, owner=owner, purpose=purpose, minutes=minutes,
                    now=now, lock_dir=lock_dir, environ=environ)
    try:
        yield lease
    finally:
        release(vm, owner=lease.owner, now=now, lock_dir=lock_dir,
                environ=environ)
