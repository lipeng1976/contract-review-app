# 状态判定、取值与格式规范

## 1. 两列状态体系（必须分开判定）

### Provided（提供情况）——只看支撑文件是否出现
| 枚举 | 英文标签 | 中文标签 | 判定 |
|---|---|---|---|
| Present | Present | 已提供 | 该清单项对应的文件/证明在合同包中出现 |
| Missing | Missing | 缺失 | 应提供但合同包中找不到 |
| Unclear | Unclear | 不明确 | 文件是否齐备无法判断 |
| Awaiting | Awaiting | 待提供 | 尚待对方/第三方补充 |
| NA | N/A | 不适用 | 事项本身不适用 |

### Verified（核验结果）——看内容对不对、要不要行动
| 枚举 | 英文标签 | 中文标签 | 判定 |
|---|---|---|---|
| Checked | Checked | 已核对 | 文件内容与标的一致、数据已核实 |
| Action | Action | 需跟进 | 存在未决事项，需要买方/律师下一步行动 |
| Unclear | Unclear | 不明确 | 内容无法核实或附件未确认 |
| NA | N/A | 不适用 | 事项本身不适用 |

规则要点：
- Provided 与 Verified 相互独立。例：文件已就"不适用"作出说明 → Provided=Present，Verified=NA。
- 只要存在任一 Action，或有关键未决项，整体状态 Overall = Follow-up required（需后续跟进）。
- 证据不足时优先标 Unclear，不要臆断为 Checked。

## 2. Contents 取值与措辞

统一格式：`字段: 值 (p.原始页码)`；多条结论分行书写。

- **每行必须以「标签: 值」开头**（标签不超过 6 个词 / 48 字符），如 `Title reference: ...`、`Mortgage: NO`、`Applicability: N/A — ...`。禁止写成没有标签的整句。
- **标签自动加粗**：渲染脚本对每个 CONTENTS 行首标签加粗（无冒号时加粗首个关键词/实体名）。模型 JSON 中不要重复手写 `<b>`，只有需要额外强调的值（如 `MATCH`、`MISMATCH`、金额）才写 `<b>`。
- 项目符号行（`- ` 开头）用于列举子项（如姓名变体），行首实体名会被加粗。

- **一致性核对**：当事人/权属人与查册比对一致 → `MATCH`；拼写一致 → `MATCH`。
- **判定词是闭集**：值位置只用 `MATCH` / `MISMATCH` / `YES` / `NO` / `NONE` / `N/A` / `Apply`，或文件原文措辞。**禁止自造** `CONFLICT`、`CHECKED`、`PARTIAL`、`UNKNOWN`、`PENDING` —— 它们看起来像结论，但不在枚举里，G30 归集和徽章都读不了。两个来源冲突时分行陈述事实，由 `Verified=Action` 承载冲突：

  ```
  Lease: YES - usage charges billed to a tenant (p.64)
  Contract: vacant possession stated (p.8)
  Review: confirm the tenancy ends before settlement
  ```

  不要写 `Disclosure status: CONFLICT`。
- **有无类**：`YES / NO`、`NONE`、`Apply`、`None disclosed`、`None known to the vendor`、`None identified`。
- **不适用**（两种写法必须区分，不可混用）：
  - `N/A - explicit (p.N)` —— 文件用**文字**明确写了不适用（如 S32 2.2 印着 `Not Applicable`）；
  - `N/A - box unmarked (p.N)` —— Section 32 表单式条目以**复选框未勾选**表达不适用（如 10.1 未登记细分、10.2 分期细分、7.1 work-in-kind）。
  两者都判 `Provided=Present` + `Verified=NA`：陈述书本身已就该事项作出表态，所以"提供情况"是已提供；不得因为"推断该事项不适用"就直接给 `NA / NA`。
  **禁止把"没填"写成"明确"**：出现 `not completed` / `left blank` / `no entry` / `not marked` 的，一律是 `box unmarked`。写成 `N/A - explicit` 会被机检判错，也是报告里最容易被律师一眼抓住的硬伤。
  
  | Provided | Verified | 是否成立 | 说明 |
  |---|---|---|---|
  | Present | Checked / Action / Unclear / NA | ✔ | 文件在，结论另判 |
  | Missing | Action / Unclear | ✔ | 应提供而未提供 |
  | Unclear | Action / Unclear | ✔ | 是否齐备无法判断 |
  | Awaiting | Action / Unclear | ✔ | 待第三方补充 |
  | NA | NA | ✔ | 事项不适用 |
  | **NA** | **Checked** | ✘ | 没有可提供的东西，谈不上核对过 → 改 `Present / Checked` 或 `NA / NA` |
  | **NA** | **Action** | ✘ | "不适用"和"要跟进"互相排斥 → 说明该事项其实适用（`Present` 或 `Missing`）+ `Action` |
- **交叉引用**：汇总项指向明细项，如 `See C10-C13 (pp.58, 64, 67, 71)`。
- **金额/编号/日期/地名/机构名原样照抄**，不得换算、缩写或改写。
- 页码引用原始合同包 PDF 页码；多份文件支撑给多个页码，页码区间用 `pp.44-48`。

## 3. 必须保留原文不译的内容

- 物业地址、人名（含卖方/买方/代理人）、Docusign Envelope ID
- 证书编号、卷宗/folio/Lot/Plan/LP 编号、AVPCC/AVPC 代码
- 金额、税率、日期、地价/估价
- 法条与法规名（如 Sale of Land Act 1962、Building Act 1993、s 32C(a)）
- 网址、机构与品牌名（REIV、Law Institute Victoria、Consumer Affairs Victoria、Monash、VicRoads、Yarra Valley Water、WESTPAC 等）

备注列（NOTES）只放法条引用，保持原文，不翻译。

## 4. 来源定位建议（常见文件 → 条目）

- 合同正文 Particulars of Sale：当事人、中介、律师、物业地址
- Section 32 陈述：卖方姓名、法定收费、地役权/契约、道路出入、各类披露
- Title Search 权属查册：Volume/Folio、Lot/Plan、登记产权人、抵押/Caveat、面积边界
- 规划/市政报告：规划方案、分区、叠加、树木管控、建筑信息
- 市政税费单：C10 税费总额与欠费
- 水务证明：C11 余额与日期
- 土地税/VRLT/CIPT/WGT 证明：C12、C13、G6
- VicRoads 证明：B7、G15；Bushfire 报告：B2；EPA：G14

## 5. 版式与成品要求

- 输出为文字可选中、可搜索、表格可结构提取的 PDF；不得是纯扫描图片。
- 保留原版式：分区标题、六列清单表（# / 事项 / 提供情况 / 核验结果 / 内容 / 备注）、状态徽章配色、尾部汇总表、免责说明框。
- 徽章配色：绿色=Present/Checked；红色=Missing；琥珀=Unclear；橙色=Awaiting；灰=NA；桃色=Action；浅琥珀=Follow-up required。
- 纯扫描、无可用文字层的证书/图则/签名页：不强行 OCR 翻译，按原件保留，并在回报中列出保留原文的页码。
- 语言：默认英文；用户要求中文时输出简体中文版，法律语体严谨；中文使用系统中文字体（微软雅黑），不得出现方块乱码。

## 6. 结论口径（免责表述固定）

- 报告结论仅为文件审核结果，不构成法律合规结论。
- 法律所有权与出售权限尚未核实（除非查册与授权文件均已核验一致）。
- 自动化 findings 是文档审核结果，不等同于法律意见。

## 7. 判定红线（以下情形禁止输出 Verified = Checked）

自动化最容易在这几类上误判为 Checked，必须降级为 Action / Unclear：

| 情形 | 正确输出 | 说明 |
|---|---|---|
| 当事人姓名存在拼写变体（大小写、少字母、缩写） | `Present / Action` | 逐行列出各变体及页码，写明 `Identity spelling: MISMATCH`；物业标的相符只写 `Property consistency: MATCH` |
| 卖方为遗嘱执行人 / 遗产售卖 | `Present / Action` | 身份关系识别 ≠ 售卖权限已核验，固定附注 `Executor / estate relationship identified — sale authority requires professional confirmation` |
| 授权类文书页数不全（如 grant of representation 只有第 1 页 / 共 3 页） | `Present / Action` | 写明 `page 1 of 3 located (incomplete)`；全文检索 'Page X of Y' 判断完整性 |
| 某文书仅在图纸/其他证书中被**引用**，附件包中没有副本 | `Provided=Unclear` + `Verified=Action` | 引用 ≠ 提供；必须区分「图纸写了编号」与「附件里有该文书」 |
| 市政/第三方检索**结论**（如近 10 年未签发许可）被当成附件 | `Provided=Unclear` + `Verified=Unclear` | 检索结论只能写进 Contents，不能据此把 Provided 记 Present |
| 事项适用性无法确认（拥堵费、EPA 必要性、建筑证书适用性等） | `Unclear / Unclear` | 不得直接判 `N/A`；`N/A` 仅用于文件已明确说明不适用 |
| 检索/证书类事项本身缺失但必要性未定 | `Unclear / Unclear` | 只有确认「应提供而未提供」才用 `Missing` |
| 商业规划报告被当作 s199 规划证书 | `Unclear / Unclear` | Planning Property Report / Planning Report 常在正文自述 `This report is NOT a Planning Certificate issued pursuant to Section 199 of the Planning and Environment Act 1987`。引到这一句就说明包里没有 s199 证书，G8 不得记 `Checked`；B3 的"控制信息"来源要注明是报告而非证书 |
| 权属查册早于陈述书签署日 90 天以上 | `Present / Action` | 查册是唯一能反映抵押/caveat 变动的文件；过期的查册不能支撑 `A4 = Checked`，见第 12 节 |
| 把 `Guarantee / Guarantor` 当成 `Grant of Probate` | `Unclear / Action` | 检索授权文书只认 `Grant of Probate`、`Grant of Representation`、`Letters of Administration` 整词；合同附件的 `Form of Guarantee for Directors` 是担保条款，不是授权文书。凭 "grant" 子串命中会造成虚假"授权文书残页" |
| Section 32 主表留空即判 `N/A`，未查合同特约条款 | 按特约条款判 | 主表 2.2 Owner-Builder 未填只代表"未按本节披露"，不代表没有工程。特约条款披露过既有改造/扩建（如 "renovation works ... undertaken in or about 2023"、"Vendor is unable to provide a building permit / domestic building insurance"）时，必须落到 D2 / D3 / D4 / G22 / G23 并判 `Action` 或 `Missing / Action` |

配套规则：
- **Provided 只回答「附件包里有没有这份独立文件」**；任何来自其他文件正文的信息一律只进 Contents。
- G30 必须按四类归集全部未决项：①文档一致性（姓名拼写等）②授权文件不全 ③文书可得性存疑 ④结算与业务事项。只列业务待办、漏掉文档本身瑕疵视为不合格。

## 8. 表单式条目怎么读（Section 32 的"勾选语义"）

Section 32 是**勾选框表单**，不是叙述文。"没勾"、"留空"、"勾了"三种状态的含义各不相同，读错方向会把结论判反：

| 位置 | 表达方式 | 正确读法 |
|---|---|---|
| 第 2.1 / 2.2 节 | 整节留空 | 只代表"未按本节披露"，**不代表不存在该情形**；必须回头查合同 Special Conditions 再定性 |
| 第 3.1(a)/(b) | 勾 (a) = 见所附 title 文件 | 要翻到 title search 页读出具体 easement / covenant，不能只写"见附件" |
| 第 7.1 / 7.2 | 未勾 | 无 work-in-kind 协议 / 无 GAIC recording → `N/A - box unmarked` |
| 第 8 节 SERVICES | **勾 X 的才是"未接通"**（原文：*The services which are marked with an "X" ... are NOT connected*），空白 = 已接通 | 逐项按坐标判勾选归属。读成"列出的都没接通"会整条判反。正确写法：`Connected: Electricity, gas, water, sewerage.` / `Not connected: Telephone.` |
| 第 9.1 / 9.2 | 勾 (a) registered title 或 (b) general law title | 对应 G1；9.2 仅在卖方非登记产权人时才勾 |
| 第 10.1 / 10.2 / 10.3 | 未勾 | 不适用 → `N/A - box unmarked` |

**勾选归属怎么判**：PDF 的文本流顺序不可靠（本例五项服务后面的 `x` 全部堆在段末）。用 PyMuPDF 取 `page.get_text("dict")` 里 `x` / `×` 的 bbox，与右侧标签**同一 y 行、x 距离最近**的那个标签就是被勾的项。只按字符顺序相邻判断会张冠李戴。

**附件齐不齐，看两处，不要只看封面**：
1. 首页/目录信列出的清单（例：*This statement includes … Water Information Statement / Melbourne Water Property Information Statement / Asset Plan / Rates Certificate*）；
2. 每页页脚的 `N of M` 或 `Page X of Y`。

封面提到 ≠ 已提供：必须翻到实际页确认那份附件真的在。反之，正文里确有该附件（如 Asset Plan 就是水务附件里的图页），就不能因为"只看到目录信提到"而判 `Cited but not located`。缺页/断号才是 `Unclear / Action`。

## 9. 条目间分工（同一事实只在一个主场判 Action）

同一件瑕疵在多个条目里重复判 `Action`，会让 G30 归集失真、买方也读不出重点。按主场分工，其余只留一句证据：

| 事实 | 主场（判 Action / Unclear） | 其他条目 |
|---|---|---|
| 当事人姓名拼写变体 | A1（`Identity spelling: MISMATCH`）+ G26（授权文书可得性） | A3 只报 proprietor / vendor 是谁；C10–C13、G3、G6 只写 `Name variant: ... (p.N)`，**不判 Action** |
| 无证工程 / 缺业主自建保险 | D2、D3、D4、G22、G23 | A11 **不收录**（A11 只答对 easement / covenant / restriction 的违反） |
| 缺 s199 规划证书 | G8 | B3 写一句"控制信息来自 Planning Property Report，非 s199 证书" |
| 水费单显示有租户占用 | E1 与 E3（二者都判，属不同问题：租约文件 vs 空置交付） | G24 交叉引用 `See E1`，不重复铺陈 |
| 抵押 / caveat | A4（有无）+ G2（文书） | C4 只写一句与 terms contract 的关系 |

汇总类条目（C1、G30）优先用交叉引用：`See C10-C13 (pp.58, 64, 67, 71)`，不要自己再判一次 Action。

## 10. 交付前自证矛盾检查（逐行过，任一命中即改）

自动化最容易出的不是"没查到"，而是"查到了却判反"。出报告前对这九条做一遍：

1. **判定与证据同向吗？** 写了 `MATCH` 的行里是否同时列着缩写名、不同地址、不同编号？同一行出现反证即改 `MISMATCH`。姓名缩写（`TUAN TRINH`、`T D Trinh`）永远是变体，不是 MATCH 的佐证。
2. **A1 的两个结论与 A3 / G26 / C 类一致吗？** A1 写 MATCH、G26 写 `Identity spelling: MISMATCH` 属自相矛盾。
3. **判 Checked 的行里有没有** `not located` / `unconfirmed` / `unable to` / `no copy` / `cited but not`？有则必须改 `Action` 或 `Unclear`。
4. **判 N/A 的行，是真的有"不适用"表达**（文字写明或复选框未勾），还是只是"我没找到"？后者一律 `Unclear`。
5. **G8 引用的那一页自称是什么？** 出现 `NOT a Planning Certificate ... Section 199` 就是报告不是证书。
6. **A7 / G26 引的页是不是 `Guarantee` / `Guarantor`？** 是则属误命中，重新检索真正的 grant / probate。
7. **判 Checked 的行里有没有祈使句？** `confirm` / `obtain` / `verify` / `arrange` / `reconcile` 一出现，这一行实际上是待办，必须改 `Action`。典型：`Mortgage: none … ; confirm any release at settlement`。
8. **同一件事在不同条目里 Provided 一致吗？** "没有业主团体"横跨 C6、D9–D14、G16–G21，"自建业主"横跨 D2–D4、G22–G23，"GAIC"横跨 C7–C9、G13，"细分"横跨 A8–A10、G27。同一句 S32 表态只能对应一个 Provided；一半 `Present` 一半 `NA` 说明有人没回原文核对。
9. **遗产售卖的三行（A1 / A7 / G26）是否都落了 Action，且 A3 没重复判？** 见第 11 节。

机检脚本：`scripts/check_model.py --model <model.json>` 会把上述可机检的部分（判定词白名单、页码引用、A1 双结论、同向性、重复 Action、尾部三块是否为空）逐条列出来，生成 PDF 前必须先跑通。

## 11. 遗产 / 执行人售卖（estate sale）的四步核对

卖方写 `as Executor of`、`as Administrator of`、登记产权人已故——这三件事同时出现时，报告最容易把"身份对上了"误当成"权限核好了"。四步都要走：

| 步 | 查什么 | 不合格的表现 |
|---|---|---|
| 1 | **姓名三种写法**：登记产权人（title）、合同/S32 当事人、遗嘱或认证文书内的写法、税费单抬头。逐条列页码 | 只比两处全名就写 `Identity spelling: MATCH` |
| 2 | **授权文书页数**：`Page X of Y`，X < Y 即缺页；Grant of Probate 常把 will 附在后面（第 2–3 页），缺页意味着遗嘱内容未知 | 拿到第 1 页就写 `attached` + `Checked` |
| 3 | **身份关系 ≠ 出售权限**：执行人是否已完成登记（transmission）、是否需要 probate 之外的授权 | 用 `consistent with an estate sale` 当结论 |
| 4 | **土地税**：证明抬头常是 `ESTATE OF …`，豁免（PPR）可能挂在遗产名下另一块地 | 直接照抄 `$0.00 exempt` 而不问豁免归属 |

固定措辞（A1 / A7 / G26 共用内核，各自只写自己那段）：

```
Executor / estate relationship identified - sale authority requires professional confirmation
Grant of probate: S PRB 2026 03563, granted 6 March 2026 (p.43)
Pages located: page 1 of 3 - will annexure not in the bundle
Name forms: NICKY POUKI (p.44); NIKI POUKI - 'in the Will called Niki Pouki' (p.43); NIKY POUKI - 'ESTATE OF NIKY POUKI' (p.62)
```

- A1 两个结论照旧：`Property consistency: MATCH` + `Identity spelling: MISMATCH`，`Verified=Action`。
- A7 / G26 写文书名、编号、授予日期、**已得页数/总页数**，`Verified=Action`。
- A3 只写 proprietor 与 vendor 是谁，不重复判 Action；出现变体就加一行 `Name variant: … (p.N)`。
- 执行人住址与登记产权人住址不同（`5 Laurel Court` vs `36 Hinkler Road`）**不是**不一致，不要写进 MISMATCH。

## 12. 时间轴：证书比陈述书老多少

报告里的日期不是装饰。把这几条拉成一条线，写进 G30 或模板 bullets：

```
登记查册 Produced 06/04/2026
市政 LIC / 水务 / SRO / VicRoads 06-08/04/2026
s199 规划证书 24/08/2026
Section 32 签署 26/08/2026
评估 18/11/2026
```

判定口径：

- **查册**早于陈述书签署日 > 90 天 → A4 / G1 判 `Action`，写明 `Search produced DD/MM/YYYY — N days before this statement; confirm no later dealing`。查册是唯一能反映抵押/caveat 变化的文件，过期的查册等于没有查册。
- **其他证书**（市政、水务、SRO、VicRoads）早于陈述书 > 90 天 → 状态可留 `Checked`（内容确已核对），但必须写老化说明：`Certificates dated April 2026 are ~4.5 months old at the date of this statement; refresh figures before settlement`。
- 只有**签署日晚于评估日**或**证书晚于陈述书**才是真正的异常，要单独指出。
- `Activity in the last 125 days: NIL` 是查册自身的回溯窗口，可以引用，但不能替代上面的日期比对。

## 13. 不要把模板条款写成发现（过度解读也是错）

本技能的失败模式有两种：漏判，和把通用文字当成具体风险。第二种会让买方关键事项表充满噪音，反而盖住真问题。

| 常见模板文字 | 正确写法 | 错误写法 |
|---|---|---|
| 市政 LIC：`A notice may be/has been served on the owner to clear a potential fire hazard`（Local Law No.3） | `Standard LIC wording; no site-specific entry (p.53)` | 升级成 Action，并写进买方关键事项 |
| 市政 LIC：`Landfill Gas Risk Area: Not applicable` | 一句备注即可 | 当作独立的污染发现 |
| S32：`This section only applies if …` + 空白 | `N/A - box unmarked` | 当作"卖方否认该情形"写成 `N/A - explicit` |
| 合同 5.x 「不接受关于许可/瑕疵的追索」的标准免责 | D2 一行说明免责范围 | 当作"披露了无证工程"直接 Action |
| 图则上的 `Section 98 Transfer of Land Act` 附注 | 不写进 A6 | 当作地役权内容 |

判据：**这句话是不是本案独有的？** 同一模板的下一份合同也会有——那就不是发现。发现必须能落到具体的编号、日期、金额或图纸标注。
