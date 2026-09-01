"""
png_size.py
------------
讀出 PNG 的像素尺寸，並判斷它適不適合當 MSIX 的套件圖示。

## 為什麼不用影像處理套件

第五輪決議第一項決定「預設沿用既有的 PNG、不做縮放」，而該決議成立的理由
正是「不為此引進一個影像處理相依（且要一併打包進 exe）」。若為了檢查尺寸
反而引進同一個相依，等於繞回去付了當初決定不付的代價。

不需要——PNG 的寬高就寫在檔頭的固定位置。規格要求檔案以 8 個位元組的簽章
開頭，緊接著第一個區塊必須是 `IHDR`，而寬高是該區塊資料的前 8 個位元組
（兩個大端序的 32 位元整數）。讀出來只需要標準函式庫。

## 為什麼要檢查

兩項檢查都來自第五輪決議第一項：

- **必須是正方形**——三個宣告位置（磚塊、工作列、商店）皆為正方形，長方形
  的圖會被拉扁。
- **邊長不得小於宣告的尺寸**——低於該值即發生放大。該決議之所以成立，靠的
  就是「使用者提供的 PNG 通常是大圖，填入這些位置全部是**縮小**，而縮小是
  安全的操作」；會產生明顯劣化的是放大，那正是這裡要擋住的情形。

第十一輪 CI 探針確認尺寸與宣告不符**不會**被系統拒絕部署，因此這兩項檢查
的理由是顯示品質，不是部署可行性——訊息要照這個講，說成「會裝不起來」是
不實的。

## 順帶擋掉一種既有驗證擋不住的情形

既有的圖示驗證只看副檔名（`packaging_core.py`）。一顆改過副檔名的 JPEG
會一路走到 `makeappx` 才失敗，而那時的錯誤訊息與「圖示」無關。這裡讀檔頭，
內容不是 PNG 就直接說清楚。
"""
import struct

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class NotAPng(Exception):
    """檔案讀不到、不是 PNG、或檔頭不完整。"""


def read(path):
    """回傳 `(寬, 高)`。讀不出來一律拋 `NotAPng`。

    不回傳 `None` 或 `(0, 0)`：那種值會被呼叫端拿去跟最小邊長比較，得到
    「太小」這個與事實無關的結論，而真正的問題（檔案根本不是 PNG）會消失。
    """
    try:
        with open(path, "rb") as f:
            header = f.read(24)
    except OSError as e:
        raise NotAPng(f"讀不到圖片檔案：{e}")

    if len(header) < 24:
        raise NotAPng("圖片檔案不完整，讀不到尺寸資訊。")
    if header[:8] != PNG_SIGNATURE:
        raise NotAPng("這個檔案的內容不是 PNG（副檔名可能與實際格式不符）。")
    # 規格要求 IHDR 是第一個區塊。不檢查的話，遇到不符規格的檔案會把另一個
    # 區塊的內容當成寬高讀出來，得到一組看起來合理的假尺寸。
    if header[12:16] != b"IHDR":
        raise NotAPng("PNG 檔頭的結構不符合規格，讀不到尺寸資訊。")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def describe_problem(path, minimum):
    """檢查一張圖能不能當 MSIX 的套件圖示，沒問題回傳 None。

    判斷與訊息放在一起，讓每個呼叫點不必各自組一次措辭——三個圖示位置加上
    共用的那一張，措辭若各寫一份就會慢慢長歪。
    """
    try:
        width, height = read(path)
    except NotAPng as e:
        return str(e)

    if width != height:
        return (
            f"套件圖示必須是正方形，這張是 {width}×{height}。"
            "MSIX 的三個圖示位置都是正方形，長方形的圖會被拉扁。"
        )
    if width < minimum:
        return (
            f"套件圖示的邊長至少要 {minimum} 像素，這張是 {width}×{height}。"
            "小於這個尺寸時 Windows 顯示它必須放大，而放大會讓圖示明顯糊掉"
            "（縮小則是安全的，所以大圖不受限制）。"
        )
    return None
