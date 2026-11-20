# Section 32 核查清单目录（A–G）

本目录是报告表格的固定骨架。每条目给出：编号、英文事项名、中文事项名、法条备注（NOTES，照抄模板）、应抽取的内容与常见来源文件。处理新合同时按此目录逐行核查；合同中确实不存在的条目保留编号并标注 `N/A`，不要删行。

法条备注仅作法律出处，不参与结论判断。来源页码以 `(p.xx)` 标注，指向合同包原始 PDF 页码。

## A. Property & Title Information（产权与权属信息）｜s 32l / 32K

| 编号 | 英文事项 | 中文事项 | 法条备注 | 抽取要点 / 来源 |
|---|---|---|---|---|
| A1 | Property/title consistency | 产权/权属一致性 | ss 32, 32l; s 32K(1)(a)-(b) | 合同当事人/卖方与权属查册、身份姓名拼写是否一致 → `MATCH`；比对 Section 32 陈述页与 Title Search 页、税局证书。**跨文件逐字比对姓名，存在拼写变体即 IF=MISMATCH、Verified=Action**，Contents 分两行写 `Property consistency: MATCH` / `Identity spelling: MISMATCH` 并列出各变体页码 |
| A2 | Title reference / land description | 权属证书/土地描述 | s 32l(a) | 卷宗号 Volume、folio 号、Lot、Plan of Subdivision、Title Diagram（LP 编号）；来源 Title Search 页、Section 32 页 |
| A3 | Registered owner / vendor | 登记业主/卖方 | s 32l(c) | Registered proprietor 姓名；Vendor 姓名；二者是否同一人。**遗产/执行人售卖场景不得直接 Checked**，固定附注 `Executor / estate relationship identified — sale authority requires professional confirmation` 并记 Action |
| A4 | Mortgages / caveats on reviewed title | 已审核权属上的抵押/保留 | s 32l(a) | 抵押有无（YES/NO）、抵押编号与抵押权人；Caveat 有无；来源 Title Search 登记簿。**同时看两个日期**：查册 `Produced` 日期与陈述书签署日/评估日相差超过 4 个月 → 写 `Search produced 06/04/2026 — 226 days before this statement; confirm no later dealing` 并判 `Action`（期间可能新增抵押/caveat）。**特约条款与查册冲突**（如特约 9.2.1 提 `the mortgage currently encumbering the Title` 而查册显示无抵押）→ 分行写出冲突，`Action`：确认成何时无需解除任何抵押 |
| A5 | Land area / boundaries | 土地面积/边界 | s 32l(a) | 地块/规划、土地面积（sq. m）、周长（m）、边界图页码。**面积不在权属查册上**：取市政/规划报告的 `SITE DIMENSIONS`（原文常附 "All dimensions and areas are approximate"），写法 `Land area: Approx. 705 sq. m (p.49)` / `Perimeter: 119 m (p.49)`；查不到才写 `Not stated in reviewed pages`。**图则页通常没有文字层**：只读文本会得到"无尺寸"，须先 `render_pages.py` 转图，把图上的边界边长（如 `18G'9"`、`59°30'`）与面积一并引用，并注明 `Dimensions scaled from the plan; not a survey` |
| A6 | Easements and restrictions | 地役权与限制 | s 32C(a) | 影响地块的地役权；**以 registered plan / title 图面为准**（S32 第 3.1 句只是"见所附文件"的指向，不是结论）。**图则页（LP / TP）几乎都是扫描图、无文字层**：必须先渲染成图再读，图上图例原文照抄，写法 `E-1: Easement to Shire of Mulgrave created by C/E A881068 (p.50)`；图面确无标注才写 `None shown on the registered plan (pp.46-48)`。**只读文本层就写"无地役权"是本技能最高频的漏项**。title search 里的 `Section 98 Transfer of Land Act` 是统计性附注，不是地役权内容，不得当作 A6 证据；图上引用的创建文书（C/E 编号）未随附 → 在 G2 判 `Unclear / Action`，A6 仍只报地役权本身 |
| A7 | Vendor authority to sell | 卖方出售权限 | s 32l(c) | 卖方身份（登记产权人 / 代理人 / 遗嘱认证等）；是否有权出售。**必须校验授权文书完整性**：检索 `Grant of Probate` / `Grant of Representation` / `Letters of Administration` 整词与 "Page X of Y"，仅取得部分页面 → Action 并写明 `page 1 of 3 located (incomplete)`。G26 判 Action 时 A7 不得判 Checked（授权未核验） |
| A8 | Unregistered subdivision - if applicable | 未登记细分（如适用） | s 32l(d)(i)-(ii) | 第 10.1 节复选框未勾 → `N/A - box unmarked (p.41)`，Provided=Present / Verified=NA；仅当文件用文字写明不适用才用 `N/A - explicit` |
| A9 | Staged subdivision - if applicable | 分期细分（如适用） | s 32l(e)(i)-(iv) | 第 10.2 节未勾 → 同 A8 写法 |
| A10 | Proposed further subdivision | 拟议进一步细分 | s 32l(f)(i)-(ii) | 第 10.3 节未勾 → 同 A8 写法。不得用"plan 已登记"之类的推断代替表单表态 |
| A11 | Existing non-compliance with easements / covenants / restrictions | 既有地役权/契约/限制方面的不合规 | s 32C(a) | **只答本项**：地役权、契约、其他限制分别记 N/A 或具体违反内容。无证改建/加建属建筑合规，进 D2/D3/D4，不塞进 A11 |

## B. Planning, Zoning & Government（规划、分区与政府事项）｜s 32C / 32D / 32J

| 编号 | 英文事项 | 中文事项 | 法条备注 | 抽取要点 / 来源 |
|---|---|---|---|---|
| B1 | Road access | 道路出入 | s 32C(c) | 道路出入 YES/NO；来源 Section 32 陈述 |
| B2 | Bushfire-prone status | 林火易发区状态 | s 32C(b) | 是否划为林火易发区 YES/NO；来源 Bushfire 报告 |
| B3 | Planning certificate and controls | 规划证明与管控 | s 32C(d), s 32J(1) | Planning Property Report 是否 present；标的是否匹配 YES |
| B4 | Planning scheme / zoning | 规划方案/分区 | s 32C(d) | 规划方案名称、分区（Zone + Schedule 编号，如 GRZ3）、市政议会 |
| B5 | Overlays affecting the lot | 影响地块的叠加规划 | s 32C(d) | 叠加规划 NONE 或具体 overlay |
| B6 | Canopy tree controls | 树冠树木管控 | - | 树木管控是否 Apply；超过规定高度（如 5 米）树木砍伐/修剪是否需许可 |
| B7 | Road / acquisition proposals | 道路/征收提案 | s 32D(a),(c) | VicRoads 道路用地要求（注明查询截止日）；征收通知有无 |
| B8 | Heritage / flood information | 文物/洪水信息 | - | 文物保护叠加；市议会洪水易感判断；水务部门排水/1% AEP 判断 |

## C. Rates, Charges & Outgoings（税费、收费与经常性支出）｜s 32A / 32G

| 编号 | 英文事项 | 中文事项 | 法条备注 | 抽取要点 / 来源 |
|---|---|---|---|---|
| C1 | Rates, taxes and outgoings | 税费、税金与经常性支出 | s 32A(c) | 汇总项，指向 C10–C13 及对应页码 |
| C2 | Statutory charges | 法定收费 | s 32A(b) | 法定收费有无；None known to vendor |
| C3 | Instalment terms contract - Schedule 2 | 分期付款合同 - 附表 2 | s 32A(d), Schedule 2 | 条款合同是否 N/A |
| C4 | Undischarged mortgage - Schedule 1 | 未解除抵押 - 附表 1 | s 32A(a), Schedule 1 | 附表 1 是否 N/A；成交产权要求 |
| C5 | CIPT status / AVPCC | CIPT 状态 / AVPCC | s 32A(ca)(i)-(iii) | AVPCC 编号；是否符合减免用途；当前 CIPT 费用与日期；$0 是否等于永久豁免的提示 |
| C6 | OC fees / arrears / special levies | 业主团体费用/欠费/特别征收 | s 32A(c) | Owners Corporation 有无 |
| C7 | GAIC work-in-kind | GAIC 以工代缴 | s 32G(1) | 是否在 GAIC 区域内 |
| C8 | GAIC liability | GAIC 责任 | s 32G; s 32A(b)-(c) | 责任情况 |
| C9 | GAIC evidence - if applicable | GAIC 证明（如适用） | s 32G(2)(a)-(g) | 证明文件情况 |
| C10 | Council rates / arrears | 市政税费/欠费 | s 32A(c) | 应付总额（含计费区间）；欠费金额；来源市政税费单 |
| C11 | Water / sewer charges | 供水/排污收费 | s 32A(c) | 水务公司证明日期；当前余额；期初余额；利息 |
| C12 | Land tax / vacant residential land tax | 土地税/空置住宅土地税 | s 32A(c) | 当前土地税+VRLT 费用；应缴土地税；豁免；核定/已缴金额；空置住宅土地税。**土地税证明是按"人"出具的**：同一个 Land Id 下可能并列**两组 Vol/Folio**（例：`8118 / 799` 与 `7687 / 146`），意味着税局把不止一份产权算进同一计税收据。逐组照抄，并在 G29 说明另一组是否属标的、是否需一并查册；不得因为"只查了其中一份 title"就写"single parcel"。Vendor 抬头常是 `ESTATE OF …`，PPR 豁免挂在哪一份产权上要单独写 |
| C13 | Windfall gains tax | 收益暴利税（WGT） | s 32A(b),(c) | 当前 WGT 费用与日期；负债；递延 WGT；证明编号；签发日期 |

## D. Building, Construction & Condition（建筑、施工与状况）｜s 32B / 32E / 32F / 32H

| 编号 | 英文事项 | 中文事项 | 法条备注 | 抽取要点 / 来源 |
|---|---|---|---|---|
| D1 | Vendor-risk insurance - if applicable | 卖方风险保险（如适用） | s 32B(a); s 32J(2) | 有无/ N/A |
| D2 | Owner-builder works / permits | 自建业主工程/许可 | s 32B(b); Building Act 1993 s 137B | **两个来源都要看**：S32 第 2.2 节（主表）+ 合同 Special Conditions。主表留空不等于无工程；特约条款披露既有改造/扩建且无法提供 building permit 时 → `Present / Action`，写明原句（照抄 `unable to provide a building permit`，不要改写成 "without permits"）。主表与特约冲突时以**更不利于买方**者为准。**区分两类特约**：一类只是"不问许可"的免责（`whether or not all relevant permits (statutory or otherwise) including building permits have been obtained`），未描述任何具体工程 → 仍可按 `Present / NA` 处理，但 Contents 必须补一句 `Permits: vendor disclaims all permits under special conditions 5.3 and 5.5 (pp.28-29); no recourse for unpermitted works`；另一类披露了具体工程（年份、扩建、翻新）或卖方明示无法提供许可/保险（`Vendor is unable to provide a building permit`）→ `Action` / `Missing / Action`。特约提到"随附于 Section 32 的 owner builder inspection report"而包内并无该报告时，按 G22 单独记 `Missing` |
| D3 | Owner-builder condition report | 自建业主状况报告 | - | 特约条款已披露无证工程而未附状况报告 → `Missing / Action`；仅主表留空且无相关特约才可 `NA` |
| D4 | Owner-builder insurance cover | 自建业主保险 | s 32B(b); s 32J(2) | 同上；卖方明示无法提供 domestic building insurance 时 → `Missing / Action` |
| D5 | Building permit - residential 7-year check | 建筑许可 - 住宅 7 年核查 | s 32E | 过去 10 年内是否签发建筑许可；注意"未签发"只覆盖证书所述期间，不等于从未施工。**核对两处口径**：S32 第 5 节填 `Attached` 而市政 CERT 写 `No Permits or Certificates have been issued` 且包内并无许可文书 → 把两个口径都写出来，`Action`（确认第 5 节勾选是否为模板残留） |
| D6 | Occupancy permit | 入住许可 | - | 过去 10 年内是否签发入住许可 |
| D7 | Service connections | 市政接驳 | s 32H(a)-(e) | 第 8 节 SERVICES **勾 X 的才是"未接通"**，空白=已接通。按标签坐标判勾选归属，写法 `Connected: Electricity, gas, water, sewerage.` / `Not connected: Telephone. (p.41)`；不得读成"列出的都没接通" |
| D8 | Certificate of final inspection | 竣工验收证明 | - | 过去 10 年内是否签发 |
| D9 | OC applicability | 业主团体适用性 | s 32F(1)-(2) | 是否属 Owners Corporation |
| D10 | OC certificates / reports | 业主团体证明/报告 | s 32F(1)(a)(i)-(ii), (b) | 有无/ N/A |
| D11 | OC certificate or prescribed information | 业主团体证明或规定资料 | s 32F(1)(a)(i) (A)-(B) | 有无/ N/A |
| D12 | OC documents / issues | 业主团体文件/事项 | s 32F(1)(a)(ii) | 有无/ N/A |
| D13 | OC purchaser information | 业主团体买方信息 | s 32F(1)(a)(ii) | 有无/ N/A |
| D14 | OC insurance - if applicable | 业主团体保险（如适用） | s 11; s 32F | 有无/ N/A |

## E. Leases, Occupancy & Third Party Interests（租赁、占用及第三方权益）｜s 32C

| 编号 | 英文事项 | 中文事项 | 法条备注 | 抽取要点 / 来源 |
|---|---|---|---|---|
| E1 | Lease / tenancy / licence documents | 租赁/租住/牌照文件 | s 32C(a) | 租赁是否适用；用水记账对象；需确认的租赁/成交安排 |
| E2 | Transferable licences | 可转让牌照 | - | 有无识别到可转让牌照 |
| E3 | Vacant possession | 空置交付 | - | 合同是否注明空置交付；用水记账对象；成交时需确认事项 |

## F. Risks, Litigation & Special Matters（风险、诉讼与特别事项）｜s 32D

| 编号 | 英文事项 | 中文事项 | 法条备注 | 抽取要点 / 来源 |
|---|---|---|---|---|
| F1 | Public-authority notices / proposals | 公共机关通知/提案 | s 32D(a) | 卖方是否知悉相关通知/提案。**分清模板文字与具体记载**：市政 LIC 固定印着的 `A notice may be/has been served on the owner to clear a potential fire hazard`（Local Law No.3）、`Landfill Gas Risk Area: Not applicable`、`under Section 121(4) of the Local Government Act 2020` 属通用条款，每份 Monash 证书都有；证书没有针对性记载（通知编号/日期/具体事项）时只写 `Standard LIC wording; no site-specific entry (p.53)`，**不判 Action、不进买方关键事项**。只有证书另有条目或卖方明确披露时才升级 |
| F2 | Agricultural chemical / livestock notices | 农用化学品/畜牧通知 | s 32D(b) | 是否披露 |
| F3 | Acquisition notices | 征收通知 | s 32D(c) | 是否披露 |

## G. Required Documents & Attachments（所需文件与附件）｜逐件核验

| 编号 | 英文事项 | 中文事项 | 法条备注 | 抽取要点 / 来源 |
|---|---|---|---|---|
| G1 | Title search and plans | 权属查册与图则 | s 32l(a) | 权属/图则是否匹配；文件页数比例。**逐件数页**：LANDATA 封面写 `Plan LP021090 3` 就要找到 3 张图页（封面页不计）；`TP458276N` 图签写着 `Sheet 1 of 1`；图页有无文字层不影响力判定，但**没有文字层必须转图看**，并在 Contents 注明 `pp.46-48 and p.50 are image scans — read from the page image`。查册 `produced` 日期同样要在本行标出 |
| G2 | Encumbrance instruments | 产权负担文书 | s 32C(a); s 32J(1) | **只有附件包中存在该文书副本才记 Present**；若仅在图则/其他证书中被引用 → `Provided=Unclear`、`Verified=Action`，并写明 refer to plan 页码 |
| G3 | Council rates certificate | 市政税费证明 | s 32A(c); s 32J(1) | 税费通知单是否提供 |
| G4 | Water statement / asset plan | 供水对账单/资产图则 | ss 32A(c), 32C(a), 32H; s 32J(1) | 供水声明、资产图则是否提供。**水务附件是一份多页文件**（封面信列出 WIS / Melbourne Water 声明 / Asset Plan / Rates Certificate，页脚 `N of M`）：必须逐页数到末页，封面提到的附件在正文确有对应页就判 Present（Asset Plan 通常就是带 `Existing Sewer` / `Easement` 图例的那张图页）。只凭封面信提到就写 `cited but not located` 是漏翻页 |
| G5 | Separate sewer map - if needed | 单独排污图（如需） | s 32C(a) | 是否 N/A；资产图则是否已显示排污覆盖（图上出现 `Existing Sewer` / `Sewer Manhole` 图例即已覆盖 → `N/A - asset plan shows sewer coverage (p.63)`） |
| G6 | SRO tax certificates | SRO 税务证明 | s 32A(b),(c), (ca); s 32J(1) | 土地税/VRLT、CIPT、WGT 证明是否提供 |
| G7 | Congestion levy notice - if applicable | 拥堵费通知（如适用） | s 32A(c) | 是否在拥堵费区域外。无法从文件确认适用性时输出 `Unclear / Unclear`，不得直接判 N/A |
| G8 | Planning certificate | 规划证明 | s 32C(d), s 32J(1) | s199 证明是否提供。**先看那一页自称是什么**：Planning Property Report / Planning Report 常在正文写 `This report is NOT a Planning Certificate issued pursuant to Section 199 of the Planning and Environment Act 1987` —— 出现该句即"包内没有 s199 证书"，判 `Unclear / Unclear`（报告替代了证书）。只有 council 出具的 s199 证书才可 `Present / Checked` |
| G9 | Property / planning reports | 物业/规划报告 | s 32C(b)-(d) | 报告是否提供及页码范围 |
| G10 | Council building information | 市议会建筑信息 | s 32E; s 32J(1) | 建筑信息日期；过去 10 年许可/证明签发情况 |
| G11 | Building / occupancy / final certificates | 建筑/入住/竣工证明 | s 32E; s 32J(1) | 近期签发情况；附件是否经查册确认。**市政检索"近 10 年未签发"≠ 证书已作为附件提供**，未找到独立证书 → `Unclear / Unclear` |
| G12 | Relevant notices | 相关通知 | s 32D(a)-(c); s 32J(1) | 卖方是否知悉 |
| G13 | GAIC evidence - if applicable | GAIC 证明（如适用） | s 32G(2)(a)-(g) | 证明情况 |
| G14 | EPA certificate - assess need | EPA 证明 - 评估必要性 | s 32D(a),(b),; s 32J(1) | 必要性是否确认。必要性未确立 → `Unclear / Unclear`；只有确认应提供而未附上才用 `Missing` |
| G15 | VicRoads certificate | VicRoads 证明 | s 32D(a),(c),; s 32J(1) | 证明是否提供 |
| G16 | OC certificate - if applicable | 业主团体证明（如适用） | s 32F(1)(a)(i)-(ii), (b) | 有无/ N/A |
| G17 | OC free report - if applicable | 业主团体免费报告（如适用） | s 32F | 有无/ N/A |
| G18 | OC insurance - if applicable | 业主团体保险（如适用） | s 11; s 32F | 有无/ N/A |
| G19 | OC model rules - if applicable | 业主团体示范规则（如适用） | s 32F(1)(a)(ii) | 有无/ N/A |
| G20 | OC additional rules - if applicable | 业主团体附加规则（如适用） | s 32F(1)(a)(ii) | 有无/ N/A |
| G21 | OC purchaser statement - if applicable | 业主团体买方声明（如适用） | s 32F(1)(a)(ii) | 有无/ N/A |
| G22 | Owner-builder report - if applicable | 自建业主报告（如适用） | - | 与 D3 同源：特约条款披露既有改造且卖方称无法提供报告 → `Missing / Action`；不得因 S32 主表留空判 N/A |
| G23 | Owner-builder cover - if applicable | 自建业主保障（如适用） | s 32B(b); s 32J(2) | 与 D4 同源：卖方明示无法提供 domestic building insurance → `Missing / Action` |
| G24 | Lease / tenancy documents - if applicable | 租赁/租住文件（如适用） | s 32C(a) | 同 E1 |
| G25 | Transferable licence - if applicable | 可转让牌照（如适用） | - | 有无/ N/A |
| G26 | Vendor authority / probate | 卖方权限/遗嘱认证 | s 32l(c) | 代理授权/权限文件是否找到。**只认整词** `Grant of Probate` / `Grant of Representation` / `Letters of Administration`——合同附件里的 `Form of Guarantee for Directors` 是担保条款，被 "grant" 子串命中会造成虚假"授权残页"。**多页授权文书按页校验**，页数不全 → `Present / Action`，Content 写明缺哪几页；找不到 → `Unclear / Action`，写明 `Grant of representation / authority document: not located`；不得标 Checked |
| G27 | Unregistered plan - if applicable | 未登记图则（如适用） | s 32l(d)(i)-(ii) | 有无/ N/A |
| G28 | Due diligence checklist | 尽职调查清单 | ss 33A-33B | 清单是否提供及页码 |
| G29 | Coverage of separately rated parcels | 单独计税地块的覆盖范围 | ss 32A, 32l, 32J | 税费覆盖范围是否确认。**把四份账单的标的逐一对齐**：市政 LIC 的 Assessment No.、水务的 Account No.、SRO 的 Land Id 与**全部** Vol/Folio、S32 的 Lot/Plan 是否指向同一块地。任一份出现第二组卷宗号（土地税证明常见，例 `8118 / 799` 与 `7687 / 146` 并列）→ 写出差异句并判 `Action`：确认另一组权利是否属标的、是否需一并查册、豁免归属；此时**不得写"single parcel，全部一致"**——写了就是自证矛盾 |
| G30 | Final document completeness | 文件最终完整性 | s 32 and applicable ss 32A-32J | 按四类归集：①文档一致性（姓名拼写等）②授权文件不全 ③文书可得性存疑 ④结算与业务事项。漏掉文档本身瑕疵视为不合格。Contents 逐类列举未决项，不要写成一句"存在若干未决事项" |

## 尾部汇总表

**两张表是报告的买方结论，空表等于没做结论。** 生成顺序：先从 82 行里筛出 `Verified ∈ {Action, Unclear}` 的条目 → **按主题合并**（不按 code 逐条搬，否则二十多个 Action 会变成二十多行流水账）→ 每个主题一行 → 再按"要不要买方动手"分流到两张表。

### Matters requiring follow-up（需跟进事项）
列：事项 / 关键发现 / 下一步 / PDF 页码。收录需要买方**采取行动**的主题：身份/授权核验、文书缺件、占用与交付、结算调整、适用性待确认。

- 必须覆盖 G30 四个归集类别各自至少一个代表主题；
- 「下一步」要可执行（`obtain written confirmation that ...`、`request the missing pages of ...`），不要写"进一步核实"这类空话；
- 有 Action 却给出空表，视为报告未完成。

### Other important property information（其他重要物业信息）
列：事项 / 关键发现 / 买方需考虑 / PDF 页码。收录对买方有**持续影响**、但不需要立即行动的事项：规划管控与叠加层、树木管控、建筑记录时效、当前支出金额、服务接驳状况。

- 一般 3–6 行；只有确实全部无关时才可省略；
- 与 follow-up 表不重复：同一事实若已在 follow-up 出现，这里不再列。

两表的 PDF 页码列取该行所有证据页的**并集**（`58, 64, 67`），不写区间估算。
