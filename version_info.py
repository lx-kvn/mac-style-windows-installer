"""version_info.py
------------------
產生 PyInstaller `--version-file` 要求的內容，讓打包出來的 exe 帶上 Win32
VERSIONINFO 資源（Windows 檔案總管「內容 → 詳細資料」頁籤看到的
FileDescription/ProductName/FileVersion/CompanyName/LegalCopyright 等欄位）。

這個模組只在**建置這個工具本身**（build_config_tool.py）跟**建置使用者
安裝檔**（builder.py）這兩個開發機流程裡用到，產生出來的 version-file
只是餵給 PyInstaller 讀的暫存輸入檔，不會被打包進最終 exe、也不會在
使用者電腦上執行，所以不需要列進 packaging_core.py 的 SHARED_DEEP_MODULES。

PyInstaller 的 version-file 格式是一段它自己會解析的 Python 原始碼文字
（結構固定：VSVersionInfo(ffi=FixedFileInfo(...), kids=[StringFileInfo([...]),
VarFileInfo([...])])），這裡直接用字串樣板組出這段文字，不 import
PyInstaller 內部模組——避免依賴一個非公開 API 的內部結構，字串樣板本身
就是穩定、經過官方文件公開記載的檔案格式。
"""


def _parse_version_tuple(version_str):
    """把 "0.12.0" 這種版本字串解析成 4 個整數的 tuple（Win32 VERSIONINFO
    的 filevers/prodvers 固定要 4 段）。不足 4 段補 0，正好 4 段原樣使用，
    超過 4 段或任何一段不是純數字都拋 ValueError——讓呼叫端在建置當下就
    發現版本字串寫錯，而不是生成一份 PyInstaller 唸不懂的檔案。
    """
    parts = version_str.split(".")
    if len(parts) > 4:
        raise ValueError(f"版本號 {version_str!r} 不能超過 4 段（filevers/prodvers 固定是 4 個整數）")
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        raise ValueError(f"版本號 {version_str!r} 每一段都必須是整數")
    numbers += [0] * (4 - len(numbers))
    return tuple(numbers)


def render_version_file(*, product_name, file_version, file_description,
                         company_name="", legal_copyright="",
                         product_version=None, original_filename=None,
                         language=0x0409, codepage=0x04B0):
    """把版本欄位組成 PyInstaller --version-file 要求的文字格式。純函式，
    回傳字串，不寫檔（寫檔交給 write_version_file()，方便測試只驗證內容，
    不用碰檔案系統）。

    product_version 省略時預設等於 file_version。language/codepage 固定用
    英文（0x0409/0x04B0）——這只是 Windows 用來決定「同一份資源有多語系
    版本時要挑哪一份」的代碼，這個工具只會生成一份，不影響 Explorer 顯示
    的實際文字內容（那些文字就是下面 StringStruct 填的字串本身）。
    """
    file_ver_tuple = _parse_version_tuple(file_version)
    prod_ver_tuple = _parse_version_tuple(product_version if product_version is not None else file_version)
    original_filename = original_filename or ""
    effective_product_version = product_version if product_version is not None else file_version
    lang_codepage_hex = f"{language:04X}{codepage:04X}"
    translation_pair = f"[{language}, {codepage}]"

    # 真實抓到的 bug：這幾個欄位（發行者/應用程式名稱等自由文字）原本用
    # f-string 手動包一層單引號直接塞進去，值裡只要有一個單引號（例如
    # "O'Brien Software"）或反斜線，就會讓產生出來的內容不是合法 Python
    # 語法，PyInstaller 讀取 --version-file 時會編譯失敗——而且是在
    # build_all() 已經清空 dist/ 之後才爆炸。改成用 repr()，讓 Python
    # 自己決定怎麼逸出（含選字元/跳脫反斜線），保證產生的內容永遠是
    # 合法的字串字面值，不管欄位值裡有什麼字元。
    company_name_lit = repr(company_name)
    file_description_lit = repr(file_description)
    file_version_lit = repr(file_version)
    original_filename_lit = repr(original_filename)
    legal_copyright_lit = repr(legal_copyright)
    product_name_lit = repr(product_name)
    product_version_lit = repr(effective_product_version)

    return f"""# UTF-8
#
# 這份檔案由 version_info.py 自動產生，供 PyInstaller --version-file 讀取，
# 不需要手動編輯。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={file_ver_tuple!r},
    prodvers={prod_ver_tuple!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '{lang_codepage_hex}',
        [StringStruct('CompanyName', {company_name_lit}),
        StringStruct('FileDescription', {file_description_lit}),
        StringStruct('FileVersion', {file_version_lit}),
        StringStruct('InternalName', {original_filename_lit}),
        StringStruct('LegalCopyright', {legal_copyright_lit}),
        StringStruct('OriginalFilename', {original_filename_lit}),
        StringStruct('ProductName', {product_name_lit}),
        StringStruct('ProductVersion', {product_version_lit})])
      ]),
    VarFileInfo([VarStruct('Translation', {translation_pair})])
  ]
)
"""


def write_version_file(path, **fields):
    """render_version_file(**fields) 的結果寫入 path（UTF-8，PyInstaller
    版本檔要求的編碼）。薄包裝。
    """
    content = render_version_file(**fields)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
