# 使用者授權合約（EULA）

**版本 1.0｜2026-09-06**

本文件為 `mac-style-windows-installer`（以下稱「本軟體」）散布之執行檔於安裝
時向使用者呈現的授權合約。中文版與英文版內容一致，如有歧異以中文版為準。

實際嵌入安裝檔的是同一份合約的純文字版：
[`docs/eula/EULA.zh-TW.txt`](eula/EULA.zh-TW.txt) 與
[`docs/eula/EULA.en.txt`](eula/EULA.en.txt)。純文字版不做段落內的硬換行
——安裝畫面的條款框會依框寬自行斷行，兩層斷行疊在一起會排出一堆兩三個字
的短行。修改條款時三份都要一起改，`tests/test_eula_consistency.py` 會檢查
三者的版本、權利人、準據法與管轄法院是否一致。

> **本草案未經法律專業審閱。** 正式發布前應由具備管轄地資格之律師檢視，
> 尤其是責任限制、免責範圍與準據法三節——各法域對消費者契約中免責條款的
> 效力認定不同，本草案採取的寫法未必在所有法域皆完全有效。

- [中文版](#中文版)
  - [第一條　定義](#第一條定義)
  - [第二條　授權範圍](#第二條授權範圍)
  - [第三條　與開放原始碼授權的關係](#第三條與開放原始碼授權的關係)
  - [第四條　您產出之安裝檔的責任歸屬](#第四條您產出之安裝檔的責任歸屬)
  - [第五條　數位簽章與憑證](#第五條數位簽章與憑證)
  - [第六條　對電腦系統的變更](#第六條對電腦系統的變更)
  - [第七條　第三方元件與外部下載](#第七條第三方元件與外部下載)
  - [第八條　安裝密碼保護的性質](#第八條安裝密碼保護的性質)
  - [第九條　資料的移除與遺失](#第九條資料的移除與遺失)
  - [第十條　禁止用途](#第十條禁止用途)
  - [第十一條　無擔保聲明](#第十一條無擔保聲明)
  - [第十二條　責任限制](#第十二條責任限制)
  - [第十三條　補償](#第十三條補償)
  - [第十四條　資料蒐集](#第十四條資料蒐集)
  - [第十五條　合約終止](#第十五條合約終止)
  - [第十六條　準據法與管轄](#第十六條準據法與管轄)
  - [第十七條　其他](#第十七條其他)
- [English](#english)
  - [1. Definitions](#1-definitions)
  - [2. Grant of licence](#2-grant-of-licence)
  - [3. Relationship to the open-source licence](#3-relationship-to-the-open-source-licence)
  - [4. Responsibility for your Output](#4-responsibility-for-your-output)
  - [5. Digital signatures and certificates](#5-digital-signatures-and-certificates)
  - [6. Changes to computer systems](#6-changes-to-computer-systems)
  - [7. Third-party components and external downloads](#7-third-party-components-and-external-downloads)
  - [8. Nature of install password protection](#8-nature-of-install-password-protection)
  - [9. Removal and loss of data](#9-removal-and-loss-of-data)
  - [10. Prohibited uses](#10-prohibited-uses)
  - [11. No warranty](#11-no-warranty)
  - [12. Limitation of liability](#12-limitation-of-liability)
  - [13. Indemnity](#13-indemnity)
  - [14. Data collection](#14-data-collection)
  - [15. Termination](#15-termination)
  - [16. Governing law and jurisdiction](#16-governing-law-and-jurisdiction)
  - [17. General](#17-general)
- [已知限制](#已知限制)
- [待辦事項](#待辦事項)
- [已完成之待辦](#已完成之待辦)

---

## 中文版

**使用者授權合約**

在安裝或使用本軟體之前，請詳細閱讀本合約。點選「同意並繼續」、或以任何方式
安裝、重製或使用本軟體，即表示您同意受本合約拘束。若您不同意，請勿安裝或
使用本軟體。

### 第一條　定義

1. 「本軟體」指 `mac-style-windows-installer` 之執行檔、隨附之資源檔案與
   說明文件，包含其打包工具（圖形介面版與命令列版）及其產出之安裝程式模板。
2. 「權利人」指本軟體之著作權人 lx-kvn。
3. 「您」指安裝或使用本軟體之自然人或法人。
4. 「產出物」指您使用本軟體所製作之安裝檔、MSIX 套件及其附屬檔案。
5. 「終端使用者」指執行您之產出物的第三人。

### 第二條　授權範圍

1. 權利人授予您非專屬、可轉讓、免權利金之授權，得於任意數量之裝置上安裝與
   使用本軟體，包含商業用途。
2. 您得將本軟體重製並散布予第三人，惟散布時應一併提供本合約與著作權聲明。
3. 本授權不移轉本軟體之任何智慧財產權。
4. 您使用本軟體製作之產出物，其著作權歸屬於您或您的授權人，權利人不對產出物
   主張任何權利，亦不因您使用本軟體而取得產出物之任何權益。

### 第三條　與開放原始碼授權的關係

1. 本軟體之原始碼以 MIT 授權條款釋出（見隨附之 `LICENSE`）。
2. 本合約適用於本軟體之**已編譯執行檔**於安裝與使用時之關係，其目的在於就
   本軟體特有的功能（產出安裝程式、變更系統設定、進行數位簽章）向使用者
   明確揭露風險與責任歸屬。
3. 本合約與 MIT 授權條款如有牴觸，就原始碼之重製、修改與再散布，以 MIT 授權
   條款為準。第十條所列之禁止用途為使用政策之聲明，不構成對 MIT 授權所授予
   權利之限制。

### 第四條　您產出之安裝檔的責任歸屬

1. 您為產出物之發行者。產出物之內容、行為、散布方式及其對終端使用者造成之
   任何結果，均由您單獨負責。
2. 您應確保您對打包進產出物之所有檔案（包含應用程式本身、圖示、授權文字、
   相依元件）擁有必要之權利或授權。
3. 本軟體不審查、不驗證亦不擔保產出物之內容。權利人對產出物不負任何形式之
   責任，包含但不限於產出物造成之資料損失、系統損害、安全事故或法律責任。
4. 產出物於終端使用者電腦上呈現之授權條款，由您於打包時自行提供；權利人
   不因本軟體提供該功能而成為該條款之當事人。

### 第五條　數位簽章與憑證

1. 本軟體可呼叫 Microsoft 提供之簽章工具（`signtool`），以您指定之憑證對
   產出物進行數位簽章。憑證由您自行取得與保管。
2. 您聲明並保證：您對所使用之憑證及其私鑰擁有合法權利，且該憑證之使用符合
   簽發機構所定之條款。以他人憑證進行簽章、或以簽章冒充他人身分，均為禁止
   行為。
3. 本軟體不傳輸、不上傳亦不儲存您的憑證私鑰。惟請注意：於**檔案模式**
   （以 `.pfx` 檔案搭配密碼）簽章時，該密碼將以命令列參數之形式傳遞予
   `signtool`，因而可能為同一部電腦上之其他行程所讀取。使用**憑證存放區
   模式**（以憑證指紋指定）可避免此情形。此項風險已於本軟體之建置紀錄與
   說明文件明確揭露。
4. 產出物之簽章狀態、時間戳記之有效性、以及終端使用者電腦是否信任該憑證，
   均非權利人所能控制，權利人就此不負擔保責任。

### 第六條　對電腦系統的變更

1. 本軟體產出之安裝程式，依您於打包時之設定，可能對終端使用者之電腦進行下列
   變更：複製檔案至指定目錄、建立與刪除登錄檔項目、建立捷徑、修改 `PATH`
   環境變數、註冊檔案關聯、建立 Windows 服務、建立排程工作、建立系統還原點、
   執行您指定之安裝前／安裝後指令碼，以及請求系統管理員權限（UAC 提權）。
2. 上述變更之範圍與後果，由您於打包時之設定決定。您應於散布前於代表性之環境
   充分測試產出物。
3. 權利人就上述變更所導致之系統不穩定、軟體衝突、資料損失或其他損害，不負
   任何責任。
4. 您指定之安裝前／安裝後指令碼將以安裝程式當時之權限執行，其內容與後果由您
   單獨負責。

### 第七條　第三方元件與外部下載

1. 本軟體及其產出物可能自 Microsoft 或您指定之來源下載並安裝下列元件：
   Microsoft Edge WebView2 Runtime、Windows SDK 工具（`makeappx`、`signtool`
   等）、Visual C++ 可轉散發套件、.NET Desktop Runtime，以及您於設定中自行
   指定之自訂相依元件。
2. 上述元件均受其各自之授權條款拘束，並非本合約之標的。您應自行確認您有權
   於您的使用情境下取得、使用與（如適用）再散布該等元件。
3. 本軟體對自訂相依元件之下載位址不作內容審查。您指定之位址所提供之檔案，其
   來源、完整性與安全性由您負責。
4. 本軟體本身包含以開放原始碼授權散布之第三方函式庫，其授權條款隨附於本軟體
   之散布檔案中。

### 第八條　安裝密碼保護的性質

1. 本軟體提供之「安裝密碼保護」功能，其定位為**存取控制**——用以防止安裝檔
   被誤傳、誤用或於未經授權之情形下安裝。
2. 該功能**不是**用以抵禦具備專業能力與充分時間之攻擊者的資訊安全機制。持有
   安裝檔者若具備相應之技術能力，仍可能取得其內容。
3. 請勿以該功能作為保護機密資料之唯一或主要手段。權利人就依賴該功能所生之
   任何損害不負責任。

### 第九條　資料的移除與遺失

1. 產出物之解除安裝程式將刪除安裝時所記錄之檔案。若安裝目錄中存有終端使用者
   自行產生之檔案，其保留與否依本軟體說明文件所載之規則處理。
2. **MSIX 模式特有之風險**：當終端使用者以較舊之版本覆蓋較新之版本時，作業
   系統要求先行移除既有套件，而 Windows 移除 MSIX 套件時會**一併清除該應用
   程式之資料**，該等資料無法復原。本軟體於互動式安裝時就此提出警示並取得
   使用者同意；於靜默安裝（`/S`）時則直接執行並將該事實寫入紀錄檔。
3. 您於部署腳本中使用靜默安裝或靜默解除安裝時，應自行評估上述後果。權利人就
   資料遺失不負任何責任。

### 第十條　禁止用途

您不得將本軟體用於下列用途：

1. 製作或散布惡意軟體、間諜軟體、勒索軟體，或任何以損害、未經授權存取、
   監視他人電腦或資料為目的之程式。
2. 製作在安裝流程中隱瞞其真實行為、或以誤導方式取得使用者同意之安裝程式，
   包含隱藏之附加軟體、預設勾選之非必要元件、或誤導性之介面文案。
3. 冒充他人或他組織之身分，包含於產出物之發行者欄位、圖示、名稱或數位簽章
   中為不實表示。
4. 規避他人軟體之技術保護措施，或散布未經授權重製之著作。
5. 違反您所在地或終端使用者所在地之法令，包含出口管制與資料保護法規。

### 第十一條　無擔保聲明

本軟體係以「現狀」及「現有」之基礎提供，不附任何明示或默示之擔保，包含但不
限於適售性、特定目的適用性、不侵權，以及運作不中斷或無錯誤之擔保。權利人不
擔保本軟體符合您的需求，亦不擔保其產出物可於任何特定之 Windows 版本、設定或
安全性軟體環境下正常運作。

### 第十二條　責任限制

1. 於法律允許之最大範圍內，權利人就任何直接、間接、附隨、特別、懲罰性或衍生
   性之損害（包含但不限於營業損失、資料遺失、系統無法使用、商譽損害、第三人
   請求）不負任何責任，無論該等損害係基於契約、侵權或其他法律理論，亦無論
   權利人是否已被告知該等損害發生之可能性。
2. 若第一款之免責於適用法律下不生效力，權利人之全部責任總額，以您就本軟體
   實際支付之金額為限；本軟體為免費提供者，該金額為零。

### 第十三條　補償

因您使用本軟體、或因您散布之產出物，致第三人對權利人提出請求、訴訟或主張時，
您應賠償權利人因此所生之損失，並使權利人免於損害。

### 第十四條　資料蒐集

1. 本軟體不蒐集、不傳輸個人資料，亦不含追蹤或使用統計之機制。
2. 本軟體於執行下列動作時會與外部網路連線：依您之指示下載 Windows SDK 工具、
   依您之設定下載相依元件、以及在偵測到終端使用者電腦缺少 WebView2 Runtime
   時下載該元件之安裝程式。該等連線之對象為 Microsoft 或您自行指定之位址，
   其隱私權政策由各該提供者訂定。

### 第十五條　合約終止

1. 您違反本合約之任一條款時，本授權自動終止。
2. 本授權終止後，您應停止使用本軟體並刪除其所有重製物。
3. 第四條、第十一條至第十三條、第十六條於本合約終止後繼續有效。
4. 本合約之終止不影響您於終止前依本軟體所產出之產出物之效力。

### 第十六條　準據法與管轄

1. 本合約之解釋與適用，以中華民國自由地區（臺灣）現行法律為準據法。因本合約
   所生之爭議，雙方合意以臺灣臺中地方法院為第一審管轄法院。
2. 如您所在地之強制規定不允許第一款之約定，則於該強制規定之範圍內，依該地
   法律。

### 第十七條　其他

1. 本合約構成雙方就本軟體之完整合意，取代先前所有口頭或書面之協議。
2. 本合約任一條款經認定為無效或不可執行者，不影響其餘條款之效力，且該條款
   應於最接近其原意之範圍內作限縮解釋。
3. 權利人未行使或遲延行使本合約之任何權利，不構成對該權利之拋棄。
4. 本合約之修訂僅於權利人以書面（含隨新版本散布之文件）為之時生效。修訂後
   之條款適用於修訂後取得之版本，不溯及您已取得之版本。

---

## English

**End User Licence Agreement**

Please read this agreement before installing or using the Software. By clicking "Agree & Continue", or by installing, copying or using the Software in any manner, you agree to be bound by this agreement. If you do not agree, do not install or use the Software.

Where this English text differs from the Traditional Chinese text of the same agreement, the Chinese text prevails.

### 1. Definitions

**1.1** "Software" means the mac-style-windows-installer executables, bundled resources and documentation, including its builder tool (graphical and command-line editions) and the installer templates it produces.

**1.2** "Licensor" means lx-kvn, the copyright holder of the Software.

**1.3** "You" means the individual or legal entity installing or using the Software.

**1.4** "Output" means the installers, MSIX packages and accompanying files you produce with the Software.

**1.5** "End user" means a third party who runs your Output.

### 2. Grant of licence

**2.1** The Licensor grants you a non-exclusive, transferable, royalty-free licence to install and use the Software on any number of devices, including for commercial purposes.

**2.2** You may copy and redistribute the Software to third parties, provided this agreement and the copyright notice accompany it.

**2.3** This licence transfers no intellectual property rights in the Software.

**2.4** Copyright in your Output belongs to you or your licensors. The Licensor claims no rights in your Output and acquires no interest in it through your use of the Software.

### 3. Relationship to the open-source licence

**3.1** The source code of the Software is released under the MIT Licence (see the accompanying LICENSE).

**3.2** This agreement governs the installation and use of the compiled executables. Its purpose is to disclose the risks of what this particular Software does - producing installers, changing system settings and applying digital signatures - and to allocate responsibility for them.

**3.3** Where this agreement conflicts with the MIT Licence in respect of copying, modifying or redistributing the source code, the MIT Licence prevails. The prohibited uses in clause 10 are a statement of acceptable use and do not restrict the rights granted by the MIT Licence.

### 4. Responsibility for your Output

**4.1** You are the publisher of your Output. You are solely responsible for its content, its behaviour, how it is distributed, and any consequence it has for end users.

**4.2** You must hold the necessary rights or licences for everything you package into your Output, including the application itself, icons, licence texts and dependencies.

**4.3** The Software does not review, validate or warrant the content of your Output. The Licensor bears no responsibility of any kind for it, including but not limited to data loss, system damage, security incidents or legal liability it may cause.

**4.4** The licence terms your Output presents on an end user's machine are supplied by you at packaging time. The Licensor does not become a party to those terms by providing this feature.

### 5. Digital signatures and certificates

**5.1** The Software can invoke the signing tool supplied by Microsoft (signtool) to apply a digital signature to your Output using a certificate you specify. You obtain and hold that certificate yourself.

**5.2** You represent and warrant that you hold lawful rights to the certificate and its private key, and that your use of it complies with the terms of the issuing authority. Signing with another party's certificate, or misrepresenting identity through a signature, is prohibited.

**5.3** The Software does not transmit, upload or store your private key. Note, however, that in file mode (a .pfx file together with a password) the password is passed to signtool as a command-line argument and may therefore be readable by other processes on the same machine. Certificate store mode (identifying the certificate by thumbprint) avoids this.

**5.4** The signature status of your Output, the validity of timestamps, and whether an end user's machine trusts your certificate are outside the Licensor's control, and the Licensor gives no warranty in respect of them.

### 6. Changes to computer systems

**6.1** Depending on your packaging configuration, installers produced by the Software may make the following changes to an end user's machine: copy files to a chosen directory, create and delete registry entries, create shortcuts, modify the PATH environment variable, register file associations, create Windows services, create scheduled tasks, create a system restore point, run the pre-install and post-install scripts you specify, and request administrator privileges.

**6.2** The scope and consequences of those changes are determined by your packaging configuration. You must test your Output in representative environments before distributing it.

**6.3** The Licensor is not responsible for system instability, software conflicts, data loss or other damage resulting from those changes.

**6.4** Scripts you specify run with the privileges the installer holds at that moment. Their content and consequences are solely your responsibility.

### 7. Third-party components and external downloads

**7.1** The Software and its Output may download and install the following components from Microsoft or from sources you specify: the Microsoft Edge WebView2 Runtime, Windows SDK tools (makeappx, signtool and others), the Visual C++ Redistributable, the .NET Desktop Runtime, and any custom dependencies you configure.

**7.2** Each of those components is governed by its own licence terms and is not the subject of this agreement. You are responsible for confirming that you may obtain, use and, where applicable, redistribute them in your circumstances.

**7.3** The Software does not inspect the content served by the custom dependency addresses you configure. The origin, integrity and safety of files served from those addresses are your responsibility.

**7.4** The Software itself includes third-party libraries distributed under open-source licences, whose terms accompany its distribution.

### 8. Nature of install password protection

**8.1** The "install password protection" feature is an access control: it exists to stop an installer being sent to the wrong person, misused, or installed without authorisation.

**8.2** It is not a security mechanism designed to resist a skilled attacker with time and access. Anyone holding the installer may, with sufficient technical ability, still recover its contents.

**8.3** Do not rely on it as the sole or principal protection for confidential material. The Licensor is not liable for any damage arising from reliance on it.

### 9. Removal and loss of data

**9.1** The uninstaller in your Output deletes the files recorded at install time. Files an end user created inside the install directory are handled according to the rules described in the Software's documentation.

**9.2** A risk specific to MSIX mode: when an end user installs an older version over a newer one, the operating system requires the installed package to be removed first, and Windows erases that application's data along with it. That data cannot be recovered. The Software warns and obtains consent during interactive installation; during silent installation (/S) it proceeds and records the fact in the log file.

**9.3** If you use silent install or uninstall in deployment scripts, you must assess that consequence yourself. The Licensor is not liable for data loss.

### 10. Prohibited uses

You may not use the Software to:

**10.1** create or distribute malware, spyware, ransomware, or any program intended to damage, gain unauthorised access to, or surveil another party's computer or data;

**10.2** create installers that conceal their true behaviour or obtain consent misleadingly, including hidden additional software, pre-ticked non-essential components, or misleading interface copy;

**10.3** impersonate another person or organisation, including false statements in the publisher field, icon, name or digital signature of your Output;

**10.4** circumvent technical protection measures in another party's software, or distribute unauthorised copies of protected works; or

**10.5** violate the laws applicable to you or to your end users, including export control and data protection law.

### 11. No warranty

The Software is provided "as is" and "as available", without warranty of any kind, express or implied, including but not limited to merchantability, fitness for a particular purpose, non-infringement, and uninterrupted or error-free operation. The Licensor does not warrant that the Software meets your requirements, nor that its Output will function correctly on any particular version, configuration or security-software environment of Windows.

### 12. Limitation of liability

**12.1** To the maximum extent permitted by law, the Licensor shall not be liable for any direct, indirect, incidental, special, punitive or consequential damages - including but not limited to lost business, lost data, system unavailability, damage to reputation, or third-party claims - whether in contract, tort or any other theory, and whether or not the Licensor was advised of the possibility of such damages.

**12.2** If the exclusion in 12.1 is ineffective under applicable law, the Licensor's total aggregate liability is limited to the amount you actually paid for the Software, which, where it is supplied free of charge, is zero.

### 13. Indemnity

Where a third party brings a claim, action or demand against the Licensor arising from your use of the Software or from Output you distribute, you will compensate the Licensor for the resulting loss and hold the Licensor harmless.

### 14. Data collection

**14.1** The Software collects and transmits no personal data and contains no tracking or usage-statistics mechanism.

**14.2** The Software connects to external networks when it performs the following: downloading Windows SDK tools at your instruction, downloading the dependencies you have configured, and downloading the WebView2 Runtime installer when an end user's machine is found to lack it. Those connections are made to Microsoft or to addresses you specify, whose privacy policies are set by their respective providers.

### 15. Termination

**15.1** This licence terminates automatically if you breach any term of this agreement.

**15.2** On termination you must stop using the Software and delete all copies of it.

**15.3** Clauses 4, 11 to 13 and 16 survive termination.

**15.4** Termination does not affect Output you produced before it.

### 16. Governing law and jurisdiction

**16.1** This agreement is governed by the laws in force in the free area of the Republic of China (Taiwan). The parties agree that the Taiwan Taichung District Court shall be the court of first instance for disputes arising from it.

**16.2** Where mandatory provisions of the law where you are located do not permit 16.1, those provisions apply to the extent required.

### 17. General

**17.1** This agreement is the entire agreement between the parties concerning the Software and supersedes any prior oral or written understanding.

**17.2** If any provision is held invalid or unenforceable, the remainder stays in effect, and that provision is to be read narrowly, as close to its original intent as possible.

**17.3** A failure or delay by the Licensor in exercising any right under this agreement is not a waiver of that right.

**17.4** Amendments take effect only when made by the Licensor in writing, including documentation distributed with a new version. They apply to versions obtained after the amendment and not retroactively to versions you already hold.

---

## 已知限制

- 本草案未經法律專業審閱，其責任限制與免責條款於各法域之效力未經確認。
  歐盟、英國、澳洲等地對消費者契約中之免責範圍設有強制規定，第十一條至
  第十三條於該等法域可能被限縮或認定無效。
- 第三條就「MIT 授權之原始碼」與「本合約拘束之執行檔」所作之區分，是對
  同一份程式碼的兩種使用情境所為之安排。取得原始碼者依 MIT 授權自行建置之
  執行檔，不受本合約拘束——本合約僅適用於權利人所散布之執行檔。
- 第十四條所述之「不蒐集資料」係就本軟體本身而言。您以本軟體產出之安裝檔
  是否蒐集資料，取決於您打包進去的應用程式，本合約不對其作任何陳述。

## 待辦事項

- 由具備管轄地資格之律師檢視全文，尤其第十一條至第十三條與第十六條。

## 已完成之待辦

- 權利人定為 `lx-kvn`（GitHub 帳號名稱），`LICENSE` 之著作權聲明一併更新。
- 準據法定為中華民國自由地區（臺灣）現行法律，管轄法院定為臺灣臺中地方
  法院。
- 產出可直接嵌入安裝檔的純文字版本（中英各一份，`docs/eula/`）。
- README 的 EULA 畫面截圖改用這份合約，不再放 `LICENSE` 的 MIT 條文。
