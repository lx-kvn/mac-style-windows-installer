/*
 * i18n.js — 三份畫面共用的介面翻譯機制。
 *
 * 抽出來的原因：t() 與 applyI18n() 原本在 ui/index.html、ui/uninstall.html、
 * ui/config.html 各有一份複本，而且已經彼此不同步——t() 三份幾乎一樣（94%），
 * applyI18n() 只剩 51% 相似，實際差異是「各自漏掉了不同的屬性」：
 *
 *   - index.html 有套用 data-i18n-placeholder，uninstall.html 沒有
 *   - 只有 config.html 有套用 data-i18n-html
 *
 * 這些不是各畫面「需要的不同行為」，是複製之後各自長歪的結果——在
 * index.html 或 uninstall.html 加一個 data-i18n-html 會安靜地不生效，
 * 而且沒有任何地方會報錯。收斂成一份之後，四種屬性在三份畫面上一致生效。
 *
 * 真正屬於單一畫面的收尾（config.html 的語言下拉選單、主題按鈕文字、
 * 環境檢查清單重繪）留在該頁自己的 applyI18n() 裡，在呼叫共用的 apply()
 * 之後執行——那些是那個畫面獨有的東西，不是被複製出來的重複。
 *
 * 這個檔案沒有模組系統，載入後在全域定義 createI18n()。ui/ 底下的檔案怎麼
 * 被帶進打包產物，見 packaging_core.ensure_workspace_files() 與
 * tests/test_ui_asset_packaging.py。
 */

/**
 * 建立一組翻譯函式。
 *
 *   tables       — { 語言代碼: { key: 字串 } }
 *   fallbackLang — 找不到指定語言、或該語言缺少某個 key 時退回哪一種
 *
 * 回傳 { t, apply, getLang }。
 */
function createI18n(tables, fallbackLang) {
    const fallback = fallbackLang || 'zh-TW';
    let currentLang = fallback;

    /**
     * 取翻譯字串。vars 會把字串裡的 {{名稱}} 換成對應的值。
     *
     * 查不到 key 時回傳 key 本身，不是空字串——畫面上出現一個原始的 key
     * 名稱一眼就看得出是漏翻譯，空白則會被當成「這裡本來就沒東西」。
     */
    function t(key, vars) {
        const table = tables[currentLang];
        let s = (table && table[key]) || (tables[fallback] && tables[fallback][key]) || key;
        if (vars) {
            for (const k in vars) s = s.replaceAll('{{' + k + '}}', vars[k]);
        }
        return s;
    }

    /**
     * 把翻譯套用到整份畫面，回傳實際採用的語言代碼（傳入的語言沒有對應的
     * 翻譯表時會退回 fallback，呼叫端需要知道最後用的是哪一種）。
     *
     * 四種標記方式：
     *   data-i18n              → 元素的文字內容
     *   data-i18n-title        → title 屬性（滑鼠停留提示）
     *   data-i18n-placeholder  → 輸入框的提示文字
     *   data-i18n-html         → 需要含連結、粗體等標記的整段內容
     *
     * data-i18n-html 用的是 innerHTML：內容來自這個專案自己的翻譯表，不是
     * 使用者輸入，所以是安全的。其餘三種一律走 innerText／setAttribute，
     * 不讓翻譯字串有機會被當成標記解析。
     */
    function apply(lang) {
        currentLang = tables[lang] ? lang : fallback;
        document.documentElement.setAttribute('lang', currentLang);
        document.querySelectorAll('[data-i18n]').forEach((el) => {
            el.innerText = t(el.dataset.i18n);
        });
        document.querySelectorAll('[data-i18n-title]').forEach((el) => {
            el.setAttribute('title', t(el.dataset.i18nTitle));
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
            el.setAttribute('placeholder', t(el.dataset.i18nPlaceholder));
        });
        document.querySelectorAll('[data-i18n-html]').forEach((el) => {
            el.innerHTML = t(el.dataset.i18nHtml);
        });
        return currentLang;
    }

    return {
        t: t,
        apply: apply,
        getLang: () => currentLang,
    };
}
