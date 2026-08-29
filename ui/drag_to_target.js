/*
 * drag_to_target.js — 自繪的「把圖示拖到目的地」手勢，安裝端與解除安裝端共用。
 *
 * 為什麼是自繪而不是瀏覽器內建的 HTML5 拖放，見
 * docs/adr/0002-drag-to-install-self-rendered-drag.md：拖曳過程中跟著游標
 * 移動的那張影像由作業系統繪製，網頁端無法控制它的外觀、縮放、透明度，
 * 也無法在放開時介入，所以做不到按下的即時回饋、沒命中的彈回、命中的吸入
 * ——而那些正是這個專案想模仿的 macOS 手感的主要組成。
 *
 * 這份檔案是把安裝端（ui/index.html）已經調好的那套搬出來共用，讓解除安裝端
 * 不必再停在被換掉的舊機制上。**不是**兩份實作合併：解除安裝端原本用的是
 * 瀏覽器內建拖放，這裡直接取代它。
 *
 * 兩端真正不同的只有兩件事，用 callback 參數化：
 *   - onHoverChange：圖示懸停在目的地上方時，目的地自己要做什麼
 *     （安裝端只有 CSS class；解除安裝端還要把垃圾桶蓋掀開）
 *   - onAbsorb：命中後目的地的回應動畫
 *     （安裝端是資料夾「吞一下」；解除安裝端是垃圾桶蓋闔上）
 * 其餘（彈簧、磁吸、命中判定、吸入飛行、原位殘影、鍵盤）完全共用。
 *
 * 這個檔案沒有模組系統，載入後在全域定義 createDragToTarget()。ui/ 底下的
 * 檔案怎麼被帶進打包產物，見 packaging_core.ensure_workspace_files() 與
 * tests/test_ui_asset_packaging.py。
 */

/*
 * 下面這幾個數字是在互動原型上實際拖過幾十次調出來的觀感值，不是推導出來的
 * 常數，往後要微調直接改這裡。依 ADR-0002 的決定，它們不寫進測試——把它們
 * 釘死只會擋住往後的微調。
 *
 * 呼叫端可以用 tuning 覆寫個別數值，但預設應該就是對的：兩端共用同一組數字
 * 才會有一致的手感，那正是把這份檔案抽出來的目的。
 */
const DRAG_TUNING_DEFAULTS = {
    SPRING_BOUNCE: 0.30,     // 0 = 完全不過衝，越大越晃
    SPRING_RESPONSE: 0.25,   // 彈簧的自然週期（秒）。彈簧沒有固定長度，
                             // 這個值是「多快到位」，不是「動畫幾秒」
    PICKUP_SCALE: 1.06,      // 拿起來就是離開桌面靠近眼睛，所以變大
    ABSORB_MS: 320,          // 命中後被吸進目的地的時間
    HIT_TOLERANCE: 25,       // 命中判定往外撐幾 px（重疊即命中的容差）
    MAGNET_STRENGTH: 0.20,   // 靠近目的地時被拉過去的程度
    MAGNET_RANGE: 150,       // 這個距離內磁吸才開始作用
    ABSORB_SCALE: 0.34,      // 吸入過程中圖示縮到多小（不縮到 0：完全消失
                             // 看起來像憑空蒸發，不像被裝進去）
};

const DRAG_EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
// 更強的 ease-out：幾乎在第一幀就衝出去，後段收得很慢。用在「已經成定局」
// 的動作（圖示被吸進去、目的地被壓下去）——這類動作開頭慢半拍就會拖沓，
// 使用者要的是它趕快演完，不是欣賞它加速。
const DRAG_EASE_SNAP = 'cubic-bezier(0.16, 1, 0.3, 1)';

/**
 * 建立一組「把 item 拖到 target 上觸發動作」的手勢。
 *
 * 參數：
 *   item        — 拖曳本體（實際被搬動的元素）
 *   inner       — 本體裡負責縮放與陰影的那一層
 *   ghost       — 原位殘影：本體被拿起後留在原地的替身，同時也是沒命中時
 *                 彈回去的目的地。拿起時加上 .shown
 *   target      — 目的地容器，懸停時會被加上 .drag-over
 *   targetSlot  — 目的地實際用來算命中範圍與播放回應動畫的元素
 *   canDrag     — 回傳 false 就不讓抓起來（例如安裝已經在進行中）
 *   onHit       — 吸入動畫播完後要做的事
 *   onHoverChange(on)          — 懸停狀態改變（選填）
 *   onAbsorb({durationMs, reduced}) — 命中時目的地的回應動畫（選填）
 *   tuning      — 覆寫上面那組數值（選填）
 *
 * 回傳 { cancel, isActive, isAbsorbing }。
 */
function createDragToTarget(config) {
    const item = config.item;
    const inner = config.inner;
    const ghost = config.ghost;
    const target = config.target;
    const targetSlot = config.targetSlot;
    const canDrag = config.canDrag || (() => true);
    const onHit = config.onHit || (() => {});
    const onHoverChange = config.onHoverChange || (() => {});
    const onAbsorb = config.onAbsorb || (() => {});
    const T = Object.assign({}, DRAG_TUNING_DEFAULTS, config.tuning || {});

    const reduceMotionQuery = window.matchMedia
        ? window.matchMedia('(prefers-reduced-motion: reduce)')
        : { matches: false };
    function motionReduced() { return reduceMotionQuery.matches; }

    // 求解器收在 ui/spring.js（垃圾桶蓋的角度動畫也用同一份）。X 跟 Y 各自
    // 獨立一條，合成單一條會在兩軸速度不同時失去同步。
    //
    // SPRING_BOUNCE 是「回彈程度」，換算成阻尼比就是 1 - bounce：0 回彈 =
    // 臨界阻尼（剛好不過衝），越大越晃。
    function newSpring() {
        return createSpring({ response: T.SPRING_RESPONSE, damping: 1 - T.SPRING_BOUNCE });
    }
    const dragX = newSpring();
    const dragY = newSpring();
    let dragActive = false;
    let dragPointerId = null;
    let dragStartPoint = { x: 0, y: 0 };
    let dragBaseOffset = { x: 0, y: 0 };
    let dragHistory = [];
    let dragHovering = false;
    let dragAbsorbing = false;
    let dragRaf = null;
    let dragLastFrame = 0;

    function renderDragPosition() {
        item.style.transform =
            'translate3d(' + dragX.x.toFixed(2) + 'px, ' + dragY.x.toFixed(2) + 'px, 0)';
    }

    function setPickedUp(on) {
        inner.style.transform = 'scale(' + (on ? T.PICKUP_SCALE : 1) + ')';
        inner.style.filter = on ? 'drop-shadow(0 14px 22px rgba(0,0,0,0.20))' : 'none';
        if (ghost) ghost.classList.toggle('shown', on);
    }

    // 命中判定：重疊即命中，容差把目的地的判定範圍往外撐。不採用依放開速度
    // 往前推算落點的做法——那適合可以反悔的動作，而安裝／解除安裝會實際
    // 改動使用者的系統，不該因為手一抖甩過去就觸發（ADR-0002 決定三）。
    function isOverTarget() {
        const a = item.getBoundingClientRect();
        const b = targetSlot.getBoundingClientRect();
        return !(a.right < b.left - T.HIT_TOLERANCE || a.left > b.right + T.HIT_TOLERANCE ||
                 a.bottom < b.top - T.HIT_TOLERANCE || a.top > b.bottom + T.HIT_TOLERANCE);
    }

    function setDropHover(on) {
        if (dragHovering === on) return;
        dragHovering = on;
        target.classList.toggle('drag-over', on);
        onHoverChange(on);
    }

    function dragLoop(now) {
        const dt = Math.min((now - dragLastFrame) / 1000, 1 / 30);
        dragLastFrame = now;
        if (!dragActive) { dragX.step(dt); dragY.step(dt); }
        renderDragPosition();
        if (dragActive || !(dragX.atRest() && dragY.atRest())) {
            dragRaf = requestAnimationFrame(dragLoop);
        } else {
            dragX.x = dragX.target; dragY.x = dragY.target;
            dragX.v = 0; dragY.v = 0;
            renderDragPosition();
            dragRaf = null;
            if (!dragAbsorbing) setPickedUp(false);
        }
    }
    function ensureDragLoop() {
        if (dragRaf === null) {
            dragLastFrame = performance.now();
            dragRaf = requestAnimationFrame(dragLoop);
        }
    }

    // 讓圖示回到原位。沒命中時彈回、以及外部中止拖曳時共用同一條路徑。
    function springBackToOrigin(vx, vy) {
        dragX.target = 0; dragY.target = 0;
        if (motionReduced()) {
            dragX.x = 0; dragY.x = 0; dragX.v = 0; dragY.v = 0;
            renderDragPosition();
        } else {
            if (typeof vx === 'number') { dragX.v = vx; dragY.v = vy; }
            ensureDragLoop();
        }
    }

    item.addEventListener('pointerdown', (e) => {
        if (dragAbsorbing || !canDrag()) return;
        e.preventDefault();
        item.setPointerCapture(e.pointerId);
        dragPointerId = e.pointerId;
        dragActive = true;
        item.classList.add('grabbing');
        // 從畫面上「現在」的位置接手，所以彈回途中也抓得住，而且不會跳位
        dragBaseOffset = { x: dragX.x, y: dragY.x };
        dragStartPoint = { x: e.clientX, y: e.clientY };
        dragX.v = 0; dragY.v = 0;
        dragHistory = [{ x: e.clientX, y: e.clientY, t: performance.now() }];
        setPickedUp(true);
        ensureDragLoop();
    });

    item.addEventListener('pointermove', (e) => {
        if (!dragActive || e.pointerId !== dragPointerId) return;
        let px = dragBaseOffset.x + (e.clientX - dragStartPoint.x);
        let py = dragBaseOffset.y + (e.clientY - dragStartPoint.y);

        if (T.MAGNET_STRENGTH > 0) {
            // 磁吸：越靠近目的地越往它靠。這段不再完全跟手，所以強度壓得
            // 很低，只當成臨門一腳的暗示，不是幫使用者對準的主力（對準靠
            // 的是命中容差）——每多一分磁吸，就多犧牲一分「東西黏在手上」
            // 的跟手感。
            const a = item.getBoundingClientRect();
            const b = targetSlot.getBoundingClientRect();
            const cx = a.left + a.width / 2, cy = a.top + a.height / 2;
            const tx = b.left + b.width / 2, ty = b.top + b.height / 2;
            const d = Math.hypot(tx - cx, ty - cy);
            if (d < T.MAGNET_RANGE) {
                const pull = T.MAGNET_STRENGTH * (1 - d / T.MAGNET_RANGE);
                px += (tx - cx) * pull;
                py += (ty - cy) * pull;
            }
        }

        dragX.x = px; dragY.x = py;
        dragX.target = px; dragY.target = py;
        renderDragPosition();
        setDropHover(isOverTarget());

        dragHistory.push({ x: e.clientX, y: e.clientY, t: performance.now() });
        if (dragHistory.length > 6) dragHistory.shift();
        ensureDragLoop();
    });

    function endDragGesture(e) {
        if (!dragActive || (e && e.pointerId !== dragPointerId)) return;
        dragActive = false;
        item.classList.remove('grabbing');
        if (dragPointerId !== null && item.hasPointerCapture(dragPointerId)) {
            item.releasePointerCapture(dragPointerId);
        }
        dragPointerId = null;

        const first = dragHistory[0];
        const last = dragHistory[dragHistory.length - 1];
        const dt = Math.max((last.t - first.t) / 1000, 0.001);
        const vx = (last.x - first.x) / dt;
        const vy = (last.y - first.y) / dt;

        if (isOverTarget()) {
            // 命中時不在這裡收掉放大狀態——放大是 CSS class 控制的，收回的
            // transition 會跟接下來的吸入動畫搶同一個 transform，兩股力道
            // 疊在一起就是那種抽搐感。改由 absorbIntoTarget() 從目前的放大
            // 狀態接手，一路演到結束。
            absorbIntoTarget();
            return;
        }
        setDropHover(false);
        setPickedUp(false);
        // 沒放中：彈回原位，並把手上的速度交接給彈簧，拖曳與動畫之間才不會
        // 有一個看得出來的接縫
        springBackToOrigin(vx, vy);
    }
    item.addEventListener('pointerup', endDragGesture);
    item.addEventListener('pointercancel', endDragGesture);

    // 命中：圖示被吸進目的地，目的地同時回應一下，接著才真的執行動作。
    // 圖示不會縮到完全消失——那看起來像憑空蒸發，不像被裝進去。
    function absorbIntoTarget() {
        dragAbsorbing = true;
        const a = item.getBoundingClientRect();
        const b = targetSlot.getBoundingClientRect();
        const dx = (b.left + b.width / 2) - (a.left + a.width / 2);
        const dy = (b.top + b.height / 2) - (a.top + a.height / 2);
        const duration = motionReduced() ? 0 : T.ABSORB_MS;

        inner.style.filter = 'none';
        inner.animate(
            [{ transform: 'scale(' + T.PICKUP_SCALE + ')' },
             { transform: 'scale(' + T.ABSORB_SCALE + ')' }],
            { duration: duration, easing: DRAG_EASE_SNAP, fill: 'forwards' }
        );
        const flight = item.animate(
            [
                { transform: 'translate3d(' + dragX.x + 'px, ' + dragY.x + 'px, 0)', opacity: 1 },
                { transform: 'translate3d(' + (dragX.x + dx) + 'px, ' + (dragY.x + dy) + 'px, 0)', opacity: 0 }
            ],
            { duration: duration, easing: DRAG_EASE_SNAP, fill: 'forwards' }
        );

        // 懸停狀態要在目的地開始播回應動畫之前就收掉，而且不能經過
        // onHoverChange 的一般路徑——目的地的回應動畫接下來會自己接管同一個
        // transform，兩邊同時在管就是那種抽搐感。
        dragHovering = false;
        target.classList.remove('drag-over');
        onAbsorb({ durationMs: duration, reduced: motionReduced() });

        flight.finished.catch(() => {}).then(() => {
            // 圖示回到原位並恢復可見：動作失敗或被取消時會退回主畫面，
            // 那時候圖示本來就該還在原本的地方
            item.getAnimations().forEach((anim) => anim.cancel());
            inner.getAnimations().forEach((anim) => anim.cancel());
            dragX.x = 0; dragY.x = 0; dragX.v = 0; dragY.v = 0;
            dragX.target = 0; dragY.target = 0;
            renderDragPosition();
            setPickedUp(false);
            dragAbsorbing = false;
            // 圖示剛剛才淡出，瞬間又出現在原位會突兀，讓它淡回來
            if (!motionReduced()) {
                item.animate([{ opacity: 0 }, { opacity: 1 }],
                    { duration: 240, easing: DRAG_EASE_OUT });
            }
            onHit();
        });
    }

    // 外部中止進行中的拖曳。畫面切走／彈出覆蓋層時一定要呼叫，不能只靠
    // 覆蓋層擋：覆蓋層確實擋得住新的 pointerdown（命中測試會回傳覆蓋層），
    // 但已經 setPointerCapture() 的拖曳完全不經過命中測試，pointermove／
    // pointerup 照樣送到本體上。
    function cancel() {
        if (!dragActive) return;
        if (dragPointerId !== null && item.hasPointerCapture(dragPointerId)) {
            item.releasePointerCapture(dragPointerId);
        }
        dragActive = false;
        item.classList.remove('grabbing');
        dragPointerId = null;
        setDropHover(false);
        setPickedUp(false);
        springBackToOrigin();
    }

    return {
        cancel: cancel,
        isActive: () => dragActive,
        isAbsorbing: () => dragAbsorbing,
    };
}
