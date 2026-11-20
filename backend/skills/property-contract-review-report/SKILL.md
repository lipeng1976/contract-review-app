---
name: property-contract-review-report
description: 把澳大利亚（尤其维多利亚州）土地买卖合同/Section 32 披露材料包整理成固定格式的产权核查台账报告。当用户提供 Contract of Sale of Land、物业买卖合同、Section 32 statement、权属查册与税费/规划证书等 PDF，要求提取信息、生成尽调摘要、清单式核查报告（含 Provided/Verified 状态、条款备注、买方待跟进事项），或需要把同类合同总结成与样例一致的英文/中文 PDF 时使用。不适用于合同翻译全文、合同起草或法律意见出具。
---

# Property Contract Review Report

## Overview

将一份物业买卖合同包（合同正文 + Section 32 陈述 + 权属查册 + 规划/税费证书/图则）按固定 Section 32 清单骨架，逐项核查并输出文字可提取、表格可解析的核查报告 PDF（英文或中文）。报告不是合同全文翻译，而是"披露事项核对表 + 关键事项摘要"。

## 工作流程

1. **读入合同包，先出三张表**：用 PyMuPDF 逐页提取文字与图片清单，判断每页是文字层还是纯扫描件；随即跑 `scripts/scan_clauses.py --pdf <包>`，拿到 ①无文字层页清单 ②`Page X of Y` 缺页清单 ③特约/正文关键词命中（无证工程、石棉、通知、抵押、租赁、地役权、细分、授权）。纯扫描页不做 OCR，但**必须 `scripts/render_pages.py` 转成 PNG 再逐页看**——图则上的 `E-1`、创建文书编号、边界边长不在文字层里，只读文本就会漏成"无地役权"。
2. **按清单目录逐项核查**：对照 `references/checklist-catalog.md` 的 A–G 条目，在对应文件中定位证据，抽取字段值并标注原始页码。条目缺失时保留编号并标 `N/A`，不要删行。
   - A1 / A3 / G26 的姓名比对先跑 `scripts/scan_names.py --pdf <包>`：以全包扫描结果为准，**不要只比合同与查册上的两处全名**——缩写往往只出现在市政费单与 SRO 证书上。
   - A5 / A6 / A11 / G1 / G2 看图说话：第 1 步渲染出的图页要真的打开看，图例原文照抄。
3. **拉一条时间轴**：查册 `Produced`、各证书签发日、s199 日期、S32 签署日、评估日。查册早于签署日 90 天以上 → A4/G1 判 `Action`；其余证书老化写进 G30 或 bullets（见 `status-and-format.md` 第 12 节）。
4. **判定状态**：按 `references/status-and-format.md` 分别判定 Provided 与 Verified，并逐条对照其中"判定红线"；汇总 Action/Unclear 形成尾部"需跟进事项"和"其他重要物业信息"两张表。
   - Provided 只回答"附件包里有没有这份独立文件"；其他文件正文里提到的内容只进 Contents。
   - 姓名拼写变体、遗产/执行人售卖、多页授权文书缺页、仅被引用的文书、适用性不明，一律不得记 Checked。
   - **Section 32 是勾选表单**：勾选/留空/未勾三种状态含义不同，按 `status-and-format.md` 第 8 节读；主表留空 ≠ 不存在该情形，必须回头查合同 Special Conditions。
   - 同一件事横跨多个条目（无业主团体、自建业主、GAIC、细分）时，Provided 必须一致，见第 10 节检查 8。
   - 尾部两张表必须非空且按主题合并，归集口径见 `checklist-catalog.md` 末节；**模板条款（市政 LIC 火险、Landfill Gas）不是发现**，不要塞进买方关键事项。
5. **填写模型 JSON**：按下方 Schema 组织数据，金额、编号、姓名、法条、机构名原样保留。
6. **交付前自检**：先跑 `scripts/check_model.py --model <model.json>`，把判定词、页码引用、A1 双结论、同向性、Checked 行里的待办动词、N/A 组合、尾部空表等问题清零；再人工过一遍 `status-and-format.md` 第 10 节的九条自证矛盾检查。
7. **生成 PDF**：运行 `scripts/build_review_report.py`，由 JSON 渲染成品；生成后校验文字可提取、表格行数正确、中文无乱码、无嵌入图片依赖。

## 数据模型（JSON）

脚本 `scripts/build_review_report.py --model <model.json> --out <report.pdf> [--lang en|zh]` 读取如下结构：

```json
{
  "lang": "zh",
  "meta": {"address": "22 CALISTA AVENUE ...", "assessment_date": "评估日期：2026 年 11 月 10 日"},
  "cards": {
    "left": {"title": "整体审核状态", "badge_enum": "FollowUp", "badge_label": "需后续跟进",
             "notes": ["提供情况 ＝ 文件是否齐备。", "核验结果 ＝ 内容审核结论。", "备注仅供法律条文引用。"]},
    "right": {"title": "状态对照", "provided_title": "提供情况",
              "provided": ["Present", "Missing", "Unclear", "Awaiting", "NA"],
              "verified_title": "核验结果", "verified": ["Checked", "Action", "Unclear", "NA"]}
  },
  "sections": [
    {"heading": "A. 产权与权属信息", "rows": [
      {"code": "A1", "item": "产权/权属一致性", "provided": "Present", "verified": "Checked",
       "contents": ["产权一致性：<b>相符</b>（p.37, p.44）"], "notes": "ss 32, 32l; s 32K(1)(a)-(b)"}
    ]}
  ],
  "followup": {"title": "买方关键事项", "subheading": "需跟进事项", "columns": ["事项","关键发现","下一步","PDF 页码"],
    "rows": [{"matter": "", "finding": [], "action": [], "pages": ""}]},
  "other": {"subheading": "其他重要物业信息", "columns": ["事项","关键发现","买方需考虑","PDF 页码"],
    "rows": [{"matter": "", "finding": [], "action": [], "pages": ""}]},
  "bullets_heading": "证据引用与法律说明",
  "bullets": ["..."],
  "note_box": {"title": "重要提示", "body": "..."},
  "footer": "模板 v1.0 · 自动生成 · 不构成法律意见"
}
```

- 徽章枚举（`provided`/`verified`/`badge_enum`）：`Present`、`Missing`、`Unclear`、`Awaiting`、`NA`、`Checked`、`Action`、`FollowUp`；颜色由脚本按枚举映射，标签可由 `provided_label`/`verified_label`/`badge_label` 覆盖。
- `contents` 每一行必须是"标签: 值 (p.页码)"结构（标签 ≤6 词 / 48 字符），因为渲染脚本会**自动把行首标签加粗**；不要手写标签的 `<b>`，只对需要强调的值（`MATCH`/`MISMATCH`/金额）用 `<b>`。多行用数组分行。
- 列表子项用 `- ` 开头，行首实体名（如姓名变体）会被自动加粗。
- 英文模型不传 `*_label`，脚本按 `lang=en` 取默认英文标签；中文模型 `lang=zh` 取默认中文标签。

## 判定红线速查

| 情形 | Provided | Verified |
|---|---|---|
| 姓名/权利人拼写存在变体 | Present | Action |
| 卖方为遗嘱执行人（遗产售卖） | Present | Action |
| 授权文书（grant/probate）缺页 | Present | Action |
| 文书仅被图纸或他件引用、附件无副本 | Unclear | Action |
| 检索结论替代了证书附件 | Unclear | Unclear |
| 商业规划报告替代 s199 规划证书 | Unclear | Unclear |
| 适用性（拥堵费/EPA/建筑证书）无法确认 | Unclear | Unclear |
| 合同特约条款披露无证工程 / 缺业主自建保险 | Missing | Action |
| 权属查册早于陈述书签署日 90 天以上 | Present | Action |
| 规划/图则页只有图像、未看过图面 | Unclear | Action |

容易判反的三种情形（详见 `status-and-format.md` 第 7 节）：`Planning Property Report ≠ s199 证书`、`Guarantee ≠ Grant of Probate`、`S32 主表留空 ≠ 该情形不存在`。

G30 必须按四类归集：文档一致性、授权文件不全、文书可得性存疑、结算与业务事项。

## 参考资源

- `references/checklist-catalog.md`：A–G 全部条目的事项名（英/中）、法条备注、抽取要点与常见来源文件。
- `references/status-and-format.md`：Provided/Verified 判定规则、取值措辞、原样保留清单、表单式条目读法（第 8 节）、条目间分工（第 9 节）、自证矛盾检查（第 10 节）、遗产售卖四步核对（第 11 节）、时间轴与陈旧证书（第 12 节）、模板条款噪声（第 13 节）、版式与免责口径。
- `scripts/scan_clauses.py`：开箱第一跑。输出 ①无文字层页 ②`Page X of Y` 缺页 ③特约/正文关键词命中（按条目归位）。授权文书"第 1 页共 3 页"、图则扫描页、特约里的无证工程都靠它先浮出来。
- `scripts/render_pages.py`：把图则/扫描页渲染成 PNG。图则没有文字层，`E-1`、`C/E A881068`、边界边长只在图上；A5/A6/A11/G1/G2 必须先看图再下笔。
- `scripts/scan_names.py`：全包姓名变体扫描。`--pdf pack.pdf` 自动从权属查册取登记产权人，扫全包列出每种写法及页码，并区分"真实姓名字段"与"参考号"；给 A1 / A3 / G26 提供证据，避免只看两处全名就判 MATCH。
- `scripts/check_model.py`：模型 JSON 机检。校验 82 行齐全与顺序、枚举白名单、自造判定词、页码引用、A1 双结论、Verified 与 Contents 同向、Checked 行里的待办动词、`N/A` 与 Verified 的非法组合、`N/A - explicit` 误用、授权文书缺页、同一事实的 Provided 一致性、查册过期、D3/D4 与 G22/G23 同源一致、尾部两表是否为空。生成 PDF 前必跑，有 error 不得出报告。
- `scripts/build_review_report.py`：模型 JSON → PDF 渲染器。行对象可带可选 `_edited` 映射
  （值为 `true` 的列名：provided/verified/contents/notes/item/code），这些单元格保持原字号字体，
  仅改为斜体 + 深蓝 `#17366B`；不传该字段时输出与原始报告完全一致（App 的"修改后报告 PDF"导出用它标记人工改动）。
- **中文兼容（强制）**：`register_fonts()` 无论 `lang` 都会注册 CJK 字体（微软雅黑 / 楷体），
  所有自由文本经 `cjk_safe()` 把中文片段切到 CJK 字体，被编辑单元格的中文用楷体 + 深蓝，
  英文仍为 Helvetica-Oblique。**不要假设英文报告里不会出现中文**——人工编辑随时会输入中文，
  Helvetica 没有 CJK 字形，会渲染成蓝色空方框。新增任何 `Paragraph` 文本都要走 `cjk_safe()`。

## 边界

- 本技能只做信息提取与台账汇总，不出具法律意见、不修改合同条款、不替代律师审核。
- 证书编号、金额、日期等以原始文件为准；查不到的信息标 Unclear/未确认，禁止虚构。
