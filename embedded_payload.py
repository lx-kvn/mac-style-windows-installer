"""
embedded_payload.py
--------------------
安裝檔裡要放哪一份應用程式內容，以及設定檔要怎麼描述它。

## 為什麼是一個模組，而不是 build_all() 裡的一段 if

稽核 D1（見 `docs/investigations/MSIX稽核與缺陷修正.md`）：這個決定原本是
`builder.build_all()` 裡的一段三選一分支，而「設定檔裡的 `password_protected`
該寫什麼」是同一個函式裡另一個位置的另一行。兩者可以不一致——而且真的不一致
了：選了 MSIX 引擎又設定密碼保護時，加密那一條永遠走不到，`password_protected`
卻仍然無條件寫成真。產出的安裝檔顯示密碼關卡，然後去開一個從未被內嵌的檔案。

**這個模組的存在理由就是讓那種不一致在結構上不可能發生。** 兩件事都由同一個
`kind` 推導：`is_password_protected(kind)` 決定設定檔怎麼寫，
`materialise(kind, ...)` 決定實際內嵌什麼。要讓它們分岔，得先讓同一個值同時
是兩個不同的東西。

## 三種內容

| kind | 內嵌什麼 | 安裝端怎麼取用 |
| --- | --- | --- |
| `PLAIN` | `app_dir` 整個目錄 | `app_contents/` |
| `ENCRYPTED` | 加密後的單一檔案 | `app_contents.enc`，密碼驗證通過後解密 |
| `MSIX` | 已簽章的 `.msix` | 不取用——檔案由系統從套件裡落地 |

表格右欄是對外契約：那兩個名字寫死在 `installer_core.py`
（`_app_contents_dir()` 與 `verify_install_password()`），改了安裝端就找不到
東西。因此名字定義在這裡，由打包端與安裝端共用同一份說法。

## 為什麼拆成 kind_for() 與 materialise() 兩步

設定檔在建置流程的很前面就要寫出來，而加密要花時間、且會產生一份需要清理的
暫存檔。合成一步的話，設定檔那一步就得承擔加密的副作用與清理責任。

拆開之後兩步之間唯一的連結是 `kind` 這個值，而那正是要維持的不變式本身。
"""
import os
from collections import namedtuple

PLAIN = "plain"
ENCRYPTED = "encrypted"
MSIX = "msix"

# 內嵌資源的名稱。這兩個是打包端與安裝端之間的契約（見模組說明的表格）。
APP_CONTENTS_DIR_NAME = "app_contents"
ENCRYPTED_FILE_NAME = "app_contents.enc"

# PyInstaller 的 `--add-data` 在 Windows 上以分號分隔來源與目的地，`.` 代表
# 放在資源區塊的根層。
_SEPARATOR = ";"
_ROOT = "."


class UnsupportedCombination(Exception):
    """兩個設定湊在一起沒有對應的內容形式。

    目前只有一種：MSIX 引擎加上安裝密碼保護（稽核 D1）。打包階段的驗證
    （`install_engine`）已經擋下它，這裡是第二道——`build_all()` 也可以被
    直接呼叫，而安靜地挑一邊正是原本產出壞安裝檔的方式。
    """


Prepared = namedtuple("Prepared", "add_data temp_file")
"""一次內容準備的結果。

`add_data` 是可以直接接在 `--add-data=` 後面的字串。`temp_file` 是這次產生
出來、事後要刪掉的暫存檔；沒有產生任何東西時是 None。

回報暫存檔而不是自己清理：清理的時機是整個建置流程的 finally，那不是這個
模組看得到的範圍。漏掉回報的後果是一份加密過的應用程式內容留在工作目錄裡。
"""


def kind_for(install_engine, password_protected):
    """這次要內嵌哪一種內容。

    `install_engine` 收的是 `install_engine.py` 的引擎字串。這個模組不匯入
    那一支：它需要的只是「是不是 msix」這一個判斷，而反過來被匯入的那一邊
    （`install_engine`）是打包設定的驗證層，讓驗證層依賴內容形式會把方向
    弄反。
    """
    if install_engine == MSIX:
        if password_protected:
            raise UnsupportedCombination(
                "MSIX 引擎目前不支援安裝密碼保護：套件內容由系統落地，"
                "而密碼保護的做法是把應用程式檔案整包加密內嵌，兩者需要另行接合。"
                "請改用傳統引擎，或取消密碼保護。"
            )
        return MSIX
    return ENCRYPTED if password_protected else PLAIN


def is_password_protected(kind):
    """設定檔裡的 `password_protected` 該寫什麼。

    只有 `ENCRYPTED` 為真。這個函式看起來只是一個等號比較，它的價值在於
    「設定檔的那個欄位由 kind 推導」這條規則有一個名字與一個位置——原本那
    個值是獨立算出來的，因此可以與實際內嵌的東西不一致。
    """
    return kind == ENCRYPTED


def materialise(kind, app_dir, workspace_dir, password="", embedded_msix="",
                encrypt=None):
    """把決定變成實際的東西，回傳 `Prepared`。

    `encrypt` 是測試接縫（比照 `file_assoc.py` 的 registry 參數），預設是
    `install_encryption.encrypt_directory`。延遲匯入：`install_encryption`
    對 `cryptography` 的匯入本身也是延遲的，而沒有用到密碼保護的人不應該
    因為這個模組而多一個相依。
    """
    if kind == PLAIN:
        return Prepared(f"{app_dir}{_SEPARATOR}{APP_CONTENTS_DIR_NAME}", None)

    if kind == MSIX:
        if not embedded_msix:
            raise ValueError(
                "MSIX 內容需要一份已備妥的 .msix 路徑，但沒有收到。"
            )
        return Prepared(f"{embedded_msix}{_SEPARATOR}{_ROOT}", None)

    if kind == ENCRYPTED:
        if encrypt is None:
            import install_encryption
            encrypt = install_encryption.encrypt_directory
        target = os.path.join(workspace_dir, ENCRYPTED_FILE_NAME)
        encrypt(app_dir, target, password)
        # 明文的 app_dir 不能同時內嵌——那會讓密碼保護形同虛設。這裡回傳的
        # 是單一字串而不是清單，因此不存在「順手也加一份」的位置。
        return Prepared(f"{target}{_SEPARATOR}{_ROOT}", target)

    raise ValueError(f"不認得的內容形式：{kind!r}")
