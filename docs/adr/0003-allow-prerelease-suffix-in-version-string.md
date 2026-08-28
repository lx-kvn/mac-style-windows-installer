# ADR-0003：版本號允許帶預發布後綴

## 狀態

已接受（2026-08-29 決定）。決定一至三已於
[`docs/proposals/跨模組一致性稽核與修正規劃.md`](../proposals/跨模組一致性稽核與修正規劃.md)
第三輪（F10）實作完成；決定四排入第四輪（F13）。

## 背景

本專案的打包設定有一個「版本」欄位，最終寫入使用者安裝檔的兩顆 exe
（安裝檔本體、`uninstall.exe`）的 Win32 VERSIONINFO 資源，也寫入
`installer_config.json` 供安裝端做覆蓋安裝的版本比較。

同一份程式碼庫中，對「什麼是合法版本號」存在兩套不一致的定義：

1. 版本比較的純函式 [`version_compare.py`](../../version_compare.py) 接受帶
   預發布後綴的版本號。`has_prerelease_suffix()` 以「字串中是否含連字號」
   判定預發布版，`compare_versions()` 在數字段相同時將帶後綴的一方視為
   較舊（`1.0.0-rc2` < `1.0.0`）。

2. 產生 VERSIONINFO 內容的 [`version_info.py`](../../version_info.py)
   `_parse_version_tuple()`（`version_info.py:20`）要求每一段皆為純整數，
   `1.0.0-rc1` 會拋 `ValueError`。此例外在 `builder.write_version_file()`
   呼叫點（`builder.py:240` 一帶）被觸發，中止整個建置流程。

合併結果：帶預發布後綴的版本號無法通過打包，因此 `version_compare.py`
中已寫好的預發布比較邏輯在實際流程中永遠不會被執行。

此外，`packaging_core.validate_and_build_pack_data()`（`packaging_core.py:402`）
對版本欄位只檢查是否為非空字串，真正的格式檢查發生在建置流程中段，此時
`dist/`／`build/` 已於流程開頭被清空。純函式
`validate_and_build_pack_data()` 的設計目的是在產生任何副作用之前攔截
設定錯誤，版本號格式檢查未走這條路徑。

## 決定

### 一、支援帶預發布後綴的版本號

版本欄位接受 `<主>.<次>.<修>[-<後綴>]` 形式，例如 `1.0.0-rc1`、
`2.0.0-beta`。後綴為連字號之後的任意非空文字，不強制符合 semantic
versioning 的完整規範——`version_compare.py` 既有的判定以「有無連字號」
為準，維持同一套慣例即可涵蓋常見情境。

不採用「版本欄位僅接受純數字段、將 `version_compare.py` 的預發布邏輯
視為未使用程式碼移除」的方案，因為覆蓋安裝時「預發布版可升級為同號正式版」
是一個明確的預期行為，該邏輯應被啟用而非移除。

### 二、數值欄位與字串欄位分開處理

Win32 VERSIONINFO 的數值欄位（`filevers`／`prodvers`）依規格固定為 4 個
16 位元整數，無法容納文字。字串欄位（`FileVersion`／`ProductVersion`）
規格上允許任意字串。

因此 `version_info._parse_version_tuple()` 改為只取每一段開頭連續的數字段
組成數值 tuple（`1.0.0-rc1` → `(1, 0, 0, 0)`），字串欄位則保留使用者
輸入的原始文字（`1.0.0-rc1`）。Windows 檔案總管「內容 → 詳細資料」頁籤
顯示的是字串欄位，終端使用者仍會看到完整的預發布版本號。

此取法與 [`version_compare.py`](../../version_compare.py) `parse_version()`
既有的「每段只取開頭連續數字」邏輯一致，兩個模組對版本號數字段的解析
方式相同。

### 三、格式驗證前移至純函式

版本號格式驗證移至 `packaging_core.validate_and_build_pack_data()`，在
任何檔案系統副作用發生前回報。驗證規則為：以連字號切出數字段與可選
後綴，數字段須為 1 至 4 段、每段皆為非負整數；後綴若存在則不得為空字串。

`builder.py` 中段呼叫 `version_info.write_version_file()` 時的例外處理
保留作為最後防線，但正常情況下不應再被觸發。

### 四、補上兩個預發布版之間的比較規則（連動 F13）

`compare_versions()` 目前在數字段相同、兩邊皆有後綴時回傳 0，導致
`1.0.0-rc1` 升級至 `1.0.0-rc2` 被判定為版本完全一致的重新安裝。放寬
版本號格式後此情境即可達成，因此 `compare_versions()` 須補上後綴之間的
比較規則：後綴以字串逐字比較（ASCII 順序），`rc1` < `rc2`、`beta` < `rc1`。
不引入 semantic versioning 對 `alpha`／`beta`／`rc` 的語意排序，因為
後綴為自由文字，無法保證使用者只用這三個詞。

## 後果

- `version_info._parse_version_tuple()` 的行為改變：原本對非純數字段
  拋例外，改為截取數字段。既有測試若斷言「`1.0.0-rc1` 會拋 `ValueError`」
  會轉紅，此為預期的契約修正，須逐一檢視測試意圖後更新。
- `packaging_core.validate_and_build_pack_data()` 新增版本號格式驗證，
  可直接單元測試，不需啟動建置流程。
- `version_compare.compare_versions()` 對「兩個預發布版」的回傳值改變，
  相關測試需同步更新。
- 使用者可打包 `rc`／`beta` 版本安裝檔，並依賴「預發布版可升級為同號
  正式版、`rc1` 可升級為 `rc2`」的覆蓋安裝行為。

## 已知限制

- 後綴比較採 ASCII 逐字順序，`1.0.0-rc10` 會被判定為早於 `1.0.0-rc9`
  （`'1'` < `'9'`）。使用者若需要兩位數的 rc 編號，須自行補零為 `rc09`。
- 數值欄位捨棄後綴資訊，兩個僅後綴不同的版本（`1.0.0-rc1` 與
  `1.0.0-rc2`）在 VERSIONINFO 的數值欄位上完全相同。依賴數值欄位判斷
  版本的外部工具無法區分兩者，需改讀字串欄位。

## 待辦事項

- [ ] 第四輪：實作決定四（`version_compare.py` 的 F13），並補上
      `version_compare.py` 的模組說明文字。

## 已完成之待辦

- [x] 第三輪：實作決定一至三。`version_info._parse_version_tuple()` 改為
      每段只取開頭連續數字（後綴被捨棄，字串欄位保留原始文字）；
      `packaging_core._validate_version_string()` 新增格式驗證，在
      `validate_and_build_pack_data()` 內於任何副作用發生前回報。
- [x] 更新 `version_info.py`／`packaging_core.py` 的說明文字，並在
      `CONTEXT.md` 新增「版本號格式（三個模組共用同一個定義）」一節，
      集中記載這個定義以及三個模組各自負責的部分。
