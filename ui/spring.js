/*
 * spring.js — 彈簧求解器，前端各處的動畫共用。
 *
 * 抽出來的原因：專案裡原本有兩份各自實作的彈簧——ui/index.html 的拖曳位移
 * （現在在 ui/drag_to_target.js）用一份，ui/uninstall.html 的垃圾桶蓋角度
 * 用另一份。ADR-0002 記載「自己寫約三十行即可」，那個判斷成立，但寫了兩次
 * 就不成立了：同一條方程式維護兩份，而且兩份已經不一樣——
 *
 *   **角度那份沒有固定子步長積分。** 位移那份的註解明講理由：「固定子步長
 *   積分，掉幀時彈簧才不會發散」。角度那份直接用整個影格的 dt 積分，畫面
 *   卡頓時（例如解除安裝正在刪大量檔案）蓋子的角度可能會抖動甚至發散。
 *
 * 收斂成一份之後，兩邊都拿到固定子步長。
 *
 * 為什麼是自己寫而不是引入動畫函式庫：安裝檔是離線執行的單一 exe，不能在
 * 執行期抓取外部資源；而彈簧的核心就是同一條「彈簧＋阻尼」方程式，各家差別
 * 只在參數的包裝方式。真正決定手感的是參數與互動規則，不是求解器的實作細節
 * （ADR-0002 決定一）。
 *
 * 為什麼是彈簧而不是固定長度的 CSS 動畫：彈簧天生從「目前的位置」繼續算，
 * 因此可以在任何一刻被使用者接手——彈回原位的途中再按下去，圖示會從當下所在
 * 位置繼續跟手，不需要等動畫播完，也不會跳位（ADR-0002 決定二）。
 *
 * 這個檔案沒有模組系統，載入後在全域定義 createSpring()。
 */

/**
 * 建立一條彈簧。
 *
 *   response — 自然週期（秒）。這是「多快到位」，不是「動畫幾秒」——彈簧
 *              沒有固定長度。
 *   damping  — 阻尼比。1 = 剛好不過衝（臨界阻尼），小於 1 會過衝回彈。
 *   value    — 起始值，同時也是初始目標值。
 *
 * 回傳的物件直接讀寫 `x`（目前值）、`v`（速度）、`target`（目標值），
 * 呼叫 `step(dt)` 前進一個影格、`atRest()` 判斷是否已經停下來。
 * `response`／`damping` 也可以隨時改（垃圾桶蓋開闔用不同的阻尼）。
 */
function createSpring(options) {
    const o = options || {};
    const start = o.value || 0;
    return {
        x: start,
        v: 0,
        target: start,
        response: o.response !== undefined ? o.response : 0.25,
        damping: o.damping !== undefined ? o.damping : 1,

        step(dt) {
            const w = (2 * Math.PI) / this.response;
            const k = w * w;                    // 彈簧係數
            const c = 2 * this.damping * w;     // 阻尼係數
            let remaining = dt;
            while (remaining > 0) {
                // 固定子步長積分：直接用整個影格的 dt 積分，在掉幀（dt 變大）
                // 時會讓數值解發散——彈簧會越彈越大而不是收斂。切成固定的小
                // 步長就不會，代價只是掉幀時多跑幾次迴圈。
                const h = Math.min(remaining, 1 / 240);
                this.v += (-k * (this.x - this.target) - c * this.v) * h;
                this.x += this.v * h;
                remaining -= h;
            }
        },

        atRest() {
            return Math.abs(this.x - this.target) < 0.05 && Math.abs(this.v) < 0.05;
        },

        /** 直接停在目標值，不播動畫。系統要求減少動態效果時用。 */
        settle() {
            this.x = this.target;
            this.v = 0;
        },
    };
}
