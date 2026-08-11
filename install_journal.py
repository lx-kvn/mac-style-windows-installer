"""install_journal.py
-------------------
安裝流程失敗時的復原動作登記簿：把「做了一個有副作用、需要能夠復原的
動作」跟「怎麼復原它」綁在一起記錄下來，失敗時依相反順序（後做的先復原）
一一呼叫。

拆分紀錄（A1 架構後續）：installer_core.py 的 `_rollback()` 原本用一長串
個別旗標（`registry_entry_created`/`shortcuts_created`/
`file_associations_registered`/`windows_service_name`/`scheduled_task_name`
……）追蹤「這次安裝做了哪些事」——新增一種新的可回滾動作，就要多加一個
旗標參數、多一段 `if 旗標: 復原(...)`，旗標本身「有沒有做」跟「怎麼復原」
分散在兩個不同地方（設定旗標的地方 vs. `_rollback()` 裡對應的 if 分支），
容易兩邊漏改其中一邊（B10/B11/B12 這幾個真實抓到的 bug都是這種「做了，
但沒有正確標記給 _rollback() 知道」的變形）。

`InstallJournal` 把這兩件事收在同一次 `record()` 呼叫裡：呼叫端在動作
成功的當下，立刻連同「怎麼復原」一起記下來，不需要另外維護一個對應的
旗標變數，也不需要在 `_rollback()` 那邊另外寫一段對應的 if 分支。

目前只有 windows_service/scheduled_task 這兩類新加的系統資源改用這個
模式（見 installer_core.py 的 `_trigger_installation_impl_inner()`）——
registry/shortcuts/file_associations/PATH 這幾類既有的回滾邏輯仍然沿用
原本的旗標寫法，這是刻意保留、暫不動的範圍（見對應的 ADR：把「回滾全部
改寫成 apply/undo 物件」這種更大範圍的重構留給下一輪，這裡先驗證這個
模式在最新加入、風險最低的兩類資源上是不是真的比較好用）。
"""


class InstallJournal:
    """依序記錄「動作描述 + 怎麼復原它」，`unwind()` 依相反順序呼叫每個
    復原函式。單一動作的復原失敗不會中斷其他動作的復原——安裝失敗後應該
    盡量清多少算多少，不能因為其中一步復原失敗，後面本來可以復原的東西
    就跟著不清了。
    """

    def __init__(self):
        self._entries = []

    def record(self, description, undo):
        """登記一個已經成功執行、需要能夠復原的動作。

        description：人看得懂的描述，只用在 `unwind()` 復原失敗時的 log
        訊息裡，不影響復原順序或邏輯。
        undo：無參數可呼叫物件，執行「復原這個動作」。呼叫時機是
        `unwind()`，不是 `record()` 當下。
        """
        self._entries.append((description, undo))

    def unwind(self, log=None):
        """依相反順序（後記錄的先復原）呼叫每個 undo。呼叫完畢後清空，
        避免同一個 journal 物件被不小心 unwind 兩次時重複執行復原動作。
        """
        while self._entries:
            description, undo = self._entries.pop()
            try:
                undo()
            except Exception as e:
                if log:
                    log(f"[警告] 回滾「{description}」失敗：{e}")
