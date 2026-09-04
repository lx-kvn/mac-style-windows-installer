r"""虛擬機占用協調——避免同時在跑的多個 session 互相把對方的工作還原掉。

這台機器上的虛擬機不只這個 repo 在用：FileLocker repo 也透過 `vms.py`
驅動同一批機器（見兩邊各自的 `.claude/skills/run-test-vm/SKILL.md`），而
使用者有時會同時開著兩個 agent session。其中 `revertToSnapshot` 是破壞性
的——另一邊裝到一半的安裝程式、正在等的畫面，會在毫無徵兆的情況下被還原
掉，而且事後從症狀看不出原因（看起來只像是「剛才那步沒生效」）。

**租約，不是行程鎖。** 直覺的做法是把持有者的行程 ID 記進鎖檔，靠「那個
行程還活著嗎」判斷是不是殘留。這裡不採用，因為每次執行腳本都是一個新的
python 行程、跑完就結束——同一個 session 在兩次操作之間就會失去鎖，另一邊
剛好插進來就還原掉快照，正是這個模組要防的事。改成記「誰佔的、佔到幾點」：
同一個持有者再要就是續租，跨得過行程邊界；時間到自動視為無人持有，卡住時
會自己解開，不需要人工清理殘留檔案。

租約放在 `%LocalAppData%\vm-locks\`，不放在任何一個 repo 裡面——佔用狀態是
這台實體機器的事實，跟哪個專案在用無關；放進某個 repo 會變成「另一個 repo
要知道第一個 repo 的路徑才能協調」，也可能被誤 commit 進版本紀錄。
"""
import contextlib
import json
import os
import time


# 每個持有者要能被別人認出來是誰，因此不接受匿名持有——匿名等於任何人都可以
# 續租任何人的租約，協調機制就退化成沒有。名字用 session 代號即可。
OWNER_ENV = "VM_LOCK_OWNER"

LOCK_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA")
    or os.path.expanduser(r"~\AppData\Local"),
    "vm-locks")

# 預設租期。訂得夠長，長到一輪驗證（還原、開機、安裝、操作、截圖）不會做到
# 一半就過期；也訂得夠短，短到忘記釋放時不會把機器擋一整天。續租沒有次數
# 限制，真的要跑更久的工作就沿路續租，不是把預設值調大。
DEFAULT_MINUTES = 30

_NOTE = ("這是虛擬機占用租約，由 tools/vm_lock.py 建立。到期後自動失效，"
         "不需要手動清理；要立刻放掉這台機器，直接刪掉這個檔案即可。")


class VmError(Exception):
    """虛擬機操作失敗。訊息一律不含密碼。"""


class VmBusy(VmError):
    """這台虛擬機正被別人佔用。"""


class Lease:
    """一筆占用租約。刻意不用具名元組——欄位會隨著記錄的東西增加而變動，
    位置式解包會在那時候安靜地錯位。"""

    __slots__ = ("vm", "owner", "purpose", "acquired_at", "expires_at")

    def __init__(self, vm, owner, purpose, acquired_at, expires_at):
        self.vm = vm
        self.owner = owner
        self.purpose = purpose
        self.acquired_at = acquired_at
        self.expires_at = expires_at

    def __repr__(self):
        return ("Lease(vm=%r, owner=%r, purpose=%r, expires_at=%r)"
                % (self.vm, self.owner, self.purpose, self.expires_at))

    def describe(self):
        """給人看的一行描述，直接放進錯誤訊息或轉述給使用者。"""
        text = "%s 被 %s 佔用中，租約到 %s" % (
            self.vm, self.owner, _clock(self.expires_at))
        if self.purpose:
            text += "（用途：%s）" % self.purpose
        return text


def _clock(stamp):
    return time.strftime("%H:%M:%S", time.localtime(stamp))


def _timestamp(stamp):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))


def _path(vm, lock_dir=None):
    return os.path.join(lock_dir or LOCK_DIR, "%s.lock" % vm)


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


def _read(path):
    """讀不到、讀不懂都回 None（視為無人持有）——壞掉的鎖檔沒有可信的持有者
    資訊，把機器永久鎖死比放行更糟，那需要人工介入才能恢復。"""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return Lease(data["vm"], data["owner"], data.get("purpose", ""),
                     float(data["acquired_at"]), float(data["expires_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write(path, lease):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "vm": lease.vm,
        "owner": lease.owner,
        "purpose": lease.purpose,
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


def holder(vm, now=None, lock_dir=None):
    """目前有效的租約；沒人佔、或租約已過期時回 None。"""
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

    current = holder(vm, now=now, lock_dir=lock_dir)
    if current is not None and current.owner != owner:
        raise VmBusy(
            current.describe()
            + "。等它到期、或請對方釋放；確定要搶過來時由使用者決定，"
            "刪掉 " + path + " 即可。")

    # 續租保留最初取得的時間與原本的用途——續租不是一次新的占用，把
    # acquired_at 往前推會看不出這台機器實際上已經被佔多久。
    acquired_at = current.acquired_at if current is not None else now
    if current is not None and not purpose:
        purpose = current.purpose

    lease = Lease(vm, owner, purpose, acquired_at, now + minutes * 60)
    _write(path, lease)
    return lease


def release(vm, owner=None, force=False, now=None, lock_dir=None,
            environ=None):
    """釋放。放掉不是自己的租約會被拒絕——那等於繞過整個協調機制；
    `force=True` 是留給使用者親自決定要拆鎖時的逃生門。"""
    now = time.time() if now is None else now
    path = _path(vm, lock_dir)

    if not force:
        owner = _resolve_owner(owner, environ)
        current = holder(vm, now=now, lock_dir=lock_dir)
        if current is not None and current.owner != owner:
            raise VmBusy(
                current.describe() + "，不是你的租約，不能替它釋放。")

    try:
        os.remove(path)
    except OSError:
        pass


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
