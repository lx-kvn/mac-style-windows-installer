"""
version_compare.py
-------------------
版本字串解析/比較的純函式，installer_core.py 的覆蓋安裝偵測
（check_existing_install()，比較「這次要裝的版本」跟「已安裝的版本」）跟
dependency_install.py 的相依元件版本檢查（min_version 門檻）共用同一份，
拆成獨立模組避免其中一邊為了另一邊的需求匯入整支 installer_core.py。

版本號格式（`<主>.<次>.<修>[-<後綴>]`）的統一定義見 CONTEXT.md 的「版本號
格式」一節與 docs/adr/0003。這個模組負責其中的「比較」那一段：數字段每段
只取開頭連續的數字（跟 version_info._parse_version_tuple() 對數字段的看法
一致），預發布的判定以「有無連字號」為準，兩個都是預發布版時後綴以字串
逐字比較。打包端的格式把關在 packaging_core._validate_version_string()。
"""


def parse_version(v):
    """把版本字串拆成數字 tuple，例如 "1.10.2" -> (1, 10, 2)，
    這樣才能正確比較「1.10.0 > 1.2.0」，單純字串比較會誤判成 1.10.0 < 1.2.0。

    每一段只取「開頭連續的數字」，遇到第一個非數字字元就停止，忽略後面的
    全部內容。真實抓到的 bug：原本的實作是把整段裡「所有」數字字元濾出來
    再串接，"1.0.0-rc2" 最後一段 "0-rc2" 會被濾成 '0'+'2' -> "02" -> 2，
    解析結果跟 "1.0.2" 完全一樣——版次後綴反而讓版本號變大，
    `compare_versions()` 因此會把候選版判斷成比正式版新，方向完全反了。
    """
    parts = []
    for p in str(v).split('.'):
        digits = ''
        for ch in p:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def has_prerelease_suffix(v):
    """版本字串裡有沒有語意上的版次後綴（例如 "1.0.0-rc2"/"1.0.0-beta"
    的 "-rc2"/"-beta"）。這個專案的版本欄位是自由文字，不強制 semver，
    用「有沒有連字號」當「這是預發布版」的慣例標記已經足夠涵蓋常見情境，
    不需要完整的 semver 解析器。"""
    return '-' in str(v)


def prerelease_suffix(v):
    """回傳第一個連字號之後的全部內容（沒有連字號就回傳空字串）。

    後綴本身含連字號時整段保留（"1.0.0-rc1-hotfix" -> "rc1-hotfix"），
    不再切第二次——後綴是自由文字，沒有進一步的內部結構可以假設。
    """
    return str(v).partition('-')[2]


def compare_versions(v1, v2):
    """回傳 1 表示 v1 > v2，0 表示相等，-1 表示 v1 < v2。

    數字部分相等時，額外比較有沒有預發布後綴——有後綴的版本視為比同樣
    數字、沒有後綴的正式版舊（"1.0.0-rc2" < "1.0.0"），才符合一般認知的
    版次語意。

    F13：兩邊都有後綴時原本一律回傳 0，`1.0.0-rc1` 升級到 `1.0.0-rc2` 會被
    判定成「版本完全一致」的重新安裝。這個情境原本踩不到，因為
    `version_info._parse_version_tuple()` 讓帶後綴的版本號根本無法打包產出；
    ADR-0003 放寬版本號格式之後就會立刻浮現。比較規則依 ADR-0003：後綴以
    字串逐字比較（ASCII 順序）。不引入 semantic versioning 對
    alpha/beta/rc 的語意排序，因為後綴是自由文字，無法保證使用者只用這
    三個詞。

    已知限制：ASCII 逐字順序讓 "1.0.0-rc10" 被判定為早於 "1.0.0-rc9"
    （'1' < '9'）。需要兩位數 rc 編號的使用者要自行補零成 rc09。
    """
    t1, t2 = parse_version(v1), parse_version(v2)
    length = max(len(t1), len(t2))
    t1 = t1 + (0,) * (length - len(t1))
    t2 = t2 + (0,) * (length - len(t2))
    if t1 > t2:
        return 1
    if t1 < t2:
        return -1
    pre1, pre2 = has_prerelease_suffix(v1), has_prerelease_suffix(v2)
    if pre1 and not pre2:
        return -1
    if pre2 and not pre1:
        return 1
    if pre1 and pre2:
        s1, s2 = prerelease_suffix(v1), prerelease_suffix(v2)
        if s1 > s2:
            return 1
        if s1 < s2:
            return -1
    return 0
