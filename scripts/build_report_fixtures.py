"""生成 `data/reports/*.json` + `data/reports/pdfs/*.pdf` —— NI 43-101 / JORC 储量 fixture。

为什么要真的生成 PDF，而不是直接塞一个 JSON 当"解析结果"：
`mineral-pdf-mcp` 的核心工具是 `extract_resources(pdf_url)`，它的难点恰恰在
**PDF 文本层的表格还原**（列错位、千分位、上标脚注、`(M)+(I)` 派生列）。
如果我们跳过 PDF 直接返回 JSON，这个工具就退化成查表，题目里「PDF 解析」这一栏等于没做。

诚实性：这三份 PDF 是**依据公开披露数字重建的**，不是原始申报文件本身。
每份 PDF 首页都印了 `FIXTURE / RECONSTRUCTED` 水印，JSON 里写
`data_origin = "reconstructed_from_public_disclosure"`，出处见 data/_sources/。

用法（纯标准库，无需额外依赖）：
    uv run python scripts/build_report_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mining_daily.common.minipdf import SimplePdf  # noqa: E402

OUT_DIR = REPO_ROOT / "data" / "reports"
PDF_DIR = OUT_DIR / "pdfs"

WATERMARK = (
    "FIXTURE DOCUMENT - RECONSTRUCTED FROM PUBLIC DISCLOSURES FOR OFFLINE DEMO. "
    "NOT THE ORIGINAL FILING."
)

# 行前缀 -> (是否加粗, 字号)
LINE_STYLES = {
    "# ": (True, 13.0),
    "## ": (True, 10.0),
    "T|": (False, 7.2),
    "F|": (False, 6.2),
}


def write_pdf(filename: str, lines: list[str]) -> Path:
    """用 Courier 等宽字体排版：等宽是刻意的 —— 它让列间距在文本层里原样保留，
    更接近真实申报文件（Word/InDesign 导出）被 pypdf 抽出来的样子。"""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf = SimplePdf(title=filename)
    pdf.add_line(WATERMARK, size=6.4)
    pdf.add_blank()

    for line in lines:
        if line == "":
            pdf.add_blank()
            continue
        for prefix, (bold, size) in LINE_STYLES.items():
            if line.startswith(prefix):
                pdf.add_line(line[len(prefix):], bold=bold, size=size)
                break
        else:
            pdf.add_line(line, size=7.6)

    path = PDF_DIR / filename
    path.write_bytes(pdf.render())
    return path


# --------------------------------------------------------------------------
# 1) Newmont —— Gold Resources as at December 31, 2025（NI 43-101 表式）
#    真实数字来源：Newmont 2026-02-19 新闻稿 / 10-K
# --------------------------------------------------------------------------
NEWMONT_LINES = [
    "# Newmont Corporation",
    "# 2025 Mineral Resources Statement",
    "",
    "## Gold Resources(1)(2) - December 31, 2025",
    "",
    "T|Deposits/Districts            Newmont    Tonnage      Grade     Ounces(3)   Metallurgical",
    "T|                              Share   (000 tonnes) (g/tonne)     (000)     Recovery(3) (%)",
    "T|----------------------------------------------------------------------------------------",
    "T|Measured Resource              100%        548,800      0.53       9,300          -",
    "T|Indicated Resource             100%      4,717,000      0.59      90,100          -",
    "T|Measured and Indicated         100%      5,265,900      0.59      99,400          -",
    "T|Inferred Resource              100%      3,967,800      0.60      70,600         78",
    "",
    "F|(1) Resources are reported exclusive of reserves. Amounts presented may not",
    "F|    recalculate in total due to rounding.",
    "F|(2) At December 31, 2025 and 2024, gold resources at sites where Newmont is the",
    "F|    operator were estimated at a gold price of $2,300 and $2,000 per ounce,",
    "F|    respectively. Tonnage amounts are rounded to the nearest 100,000.",
    "F|(3) Ounces are estimates of metal contained in ore tonnages and do not include",
    "F|    allowances for processing losses. Ounces may not recalculate as they are",
    "F|    rounded to the nearest 100,000.",
    "",
    "## Notes on Estimation Methodology",
    "",
    "Mineral Resources are not Mineral Reserves and do not have demonstrated economic",
    "viability. The estimate of Mineral Resources may be materially affected by",
    "environmental, permitting, legal, title, taxation, socio-political, marketing,",
    "or other relevant issues.",
]

# --------------------------------------------------------------------------
# 2) Barrick —— Mineral Resources as at December 31, 2024（NI 43-101，金 + 铜）
#    真实数字来源：Barrick Annual Report 2024, s25.q4cdn.com PDF
# --------------------------------------------------------------------------
BARRICK_LINES = [
    "# Barrick Gold Corporation",
    "# Annual Mineral Resources Statement",
    "",
    "## Gold Mineral Resources(1)(3)(5) - As at December 31, 2024",
    "",
    "T|                    MEASURED (M)         INDICATED (I)          (M)+(I)            INFERRED",
    "T|Deposits         Tonnes  Grade  Contained  Tonnes Grade Contained  Tonnes Grade Contained  Tonnes Grade Contained",
    "T|                   (Mt)  (g/t)   ozs(Moz)    (Mt) (g/t)  ozs(Moz)    (Mt) (g/t)  ozs(Moz)    (Mt) (g/t)  ozs(Moz)",
    "T|------------------------------------------------------------------------------------------------------",
    "T|Carlin surface     8.8   1.29     0.37       96  2.06       6.4     105  2.00       6.8      29   1.3       1.2",
    "T|Bulyanhulu u/g       -      -        -       28  7.16       6.5      28  7.16       6.5      11   7.2       2.5",
    "T|Cortez (61.5%)       -      -        -      140  2.45      11.0     140  2.45      11.0      46   2.3       3.4",
    "T|------------------------------------------------------------------------------------------------------",
    "T|Total Gold        450   1.68       24     4,800  1.01     150.0   5,250  1.01     174.0   1,400   0.9      41.0",
    "",
    "## Copper Mineral Resources(1)(3)(5) - As at December 31, 2024",
    "",
    "T|                    MEASURED (M)         INDICATED (I)                              INFERRED",
    "T|                 Tonnes  Grade Contained  Tonnes Grade Contained                 Tonnes Grade Contained",
    "T|                   (Mt)  (% Cu)    (Mt)    (Mt) (% Cu)      (Mt)                   (Mt) (% Cu)      (Mt)",
    "T|------------------------------------------------------------------------------------------------------",
    "T|Total Copper       600   0.38      2.3    5,400  0.39      22.0                   1,300  0.30       3.9",
    "",
    "F|Estimated in accordance with National Instrument 43-101 - Standards of Disclosure",
    "F|for Mineral Projects as required by Canadian securities regulatory authorities.",
    "F|(1) Mineral Resources are reported inclusive of Mineral Reserves.",
    "F|(3) Tonnage and contained metal are rounded to two significant figures; Measured",
    "F|    and Indicated grades are reported to two decimal places, Inferred grades to",
    "F|    one decimal place. Totals may not sum correctly due to rounding.",
    "F|(5) Resource estimates use a gold price of $1,900/oz and a copper price of $4.00/lb.",
]

# --------------------------------------------------------------------------
# 3) Pilbara Minerals —— Pilgangoora（JORC 2012，不是 NI 43-101）
#    两张表：2025 年现行 MRE（M+I / Total）+ 2016 年 Pilgangoora South 历史估计
#    （后者是唯一能查到真实 Indicated/Inferred 拆分的一张）
# --------------------------------------------------------------------------
PILBARA_LINES = [
    "# Pilbara Minerals Limited (ASX:PLS)",
    "# Pilgangoora Lithium-Tantalum Project",
    "# Mineral Resource Estimate",
    "",
    "## Table 1 - Pilgangoora Mineral Resource Estimate, as at 31 March 2025",
    "",
    "T|                     Tonnes      Li2O      Ta2O5     Fe2O3      Contained    Contained",
    "T|JORC Category          (Mt)       (%)      (ppm)       (%)    Li2O (Mt)  Ta2O5 (Mlb)",
    "T|---------------------------------------------------------------------------------",
    "T|Measured + Indicated  376      1.29        119      0.59          4.8           99",
    "T|Total (M+I+Inferred)  446      1.28        122      0.59          5.7          120",
    "",
    "F|Note: the 2025 estimate was reported with a 0.3% Li2O cut-off grade and is based on",
    "F|104,672 m of drilling across 364 holes. Contained lithium increased 23% year on",
    "F|year; tonnes increased 10% (+39 Mt) and grade improved from 1.15% to 1.28% Li2O.",
    "F|Resource stated in accordance with the JORC Code (2012 Edition).",
    "F|Pilbara Minerals reports under JORC and does not publish an NI 43-101 technical",
    "F|report in respect of the Pilgangoora Project.",
    "",
    "## Table 2 - Pilgangoora South Mineral Resource Estimate, historical",
    "##           Altura Mining Limited, February 2016 (0.4% Li2O cut-off)",
    "",
    "T|JORC Category        Tonnes (Mt)    Li2O (%)    Contained Li2O (t)",
    "T|-----------------------------------------------------------------",
    "T|Indicated                26.7        1.05              280,000",
    "T|Inferred                  9.0        1.02               92,000",
    "T|Total                    35.7        1.05              372,000",
    "",
    "F|Historical estimate prepared by Altura Mining Limited prior to the acquisition of",
    "F|Pilgangoora South by Pilbara Minerals in 2021. Not reported under NI 43-101.",
]

# --------------------------------------------------------------------------
# 4) Pilbara Minerals 2015 历史估计 —— 真正的 Indicated/Inferred 拆分（含 Ta2O5）
#    用来给解析器一个"锂钽双金属 + 吨/磅混合单位"的硬骨头
# --------------------------------------------------------------------------
PILBARA_2015_LINES = [
    "# Pilbara Minerals Limited (ASX:PLS)",
    "# Pilgangoora Tantalum-Lithium Project - Historical Mineral Resource",
    "# Estimate as at June 2015",
    "",
    "T|JORC Category  Tonnes(Mt)  Ta2O5(ppm)  Contained Ta2O5(Mlb)  Li2O(%)  Contained Li2O(t)",
    "T|------------------------------------------------------------------------------",
    "T|Indicated           6.0         233                  3.1      1.36          64,300",
    "T|Inferred           17.8         208                  8.2      1.10         172,800",
    "T|Total              23.8         214                 11.3      1.16         237,100",
    "",
    "F|Historical estimate, superseded by subsequent drilling. Retained for reference.",
]


REPORTS = [
    {
        "report_id": "newmont-gold-resources-2025",
        "pdf": "newmont_gold_resources_2025.pdf",
        "lines": NEWMONT_LINES,
        "meta": {
            "company": "Newmont Corporation",
            "ticker": "NYSE:NEM",
            "project": "Consolidated (all operations)",
            "commodity": "gold",
            "reporting_standard": "NI 43-101",
            "title": "2025 Mineral Resources Statement - Gold Resources",
            "effective_date": "2025-12-31",
            "published_date": "2026-02-19",
            "source_url": "https://www.businesswire.com/news/home/20260219894602/en/",
            "data_origin": "reconstructed_from_public_disclosure",
        },
        "expected": [
            {
                "category": "Indicated",
                "ore_tonnage": {"value": 4717000, "unit": "kt", "value_mt": 4717.0},
                "grade": {"value": 0.59, "unit": "g/t Au"},
                "contained_metal": {"value": 90100, "unit": "koz", "value_oz": 90100000.0},
            },
            {
                "category": "Inferred",
                "ore_tonnage": {"value": 3967800, "unit": "kt", "value_mt": 3967.8},
                "grade": {"value": 0.60, "unit": "g/t Au"},
                "contained_metal": {"value": 70600, "unit": "koz", "value_oz": 70600000.0},
            },
        ],
    },
    {
        "report_id": "barrick-mineral-resources-2024",
        "pdf": "barrick_mineral_resources_2024.pdf",
        "lines": BARRICK_LINES,
        "meta": {
            "company": "Barrick Gold Corporation",
            "ticker": "NYSE:GOLD",
            "project": "Consolidated (all operations)",
            "commodity": "gold,copper",
            "reporting_standard": "NI 43-101",
            "title": "Mineral Resources as at December 31, 2024",
            "effective_date": "2024-12-31",
            "published_date": "2025-02-06",
            "source_url": "https://s25.q4cdn.com/322814910/files/doc_financial/annual_reports/2024/Barrick_Annual_Report_2024.pdf",
            "data_origin": "reconstructed_from_public_disclosure",
        },
        "expected": [
            {
                "category": "Indicated",
                "commodity": "gold",
                "ore_tonnage": {"value": 4800, "unit": "Mt", "value_mt": 4800.0},
                "grade": {"value": 1.01, "unit": "g/t Au"},
                "contained_metal": {"value": 150.0, "unit": "Moz", "value_oz": 150000000.0},
            },
            {
                "category": "Inferred",
                "commodity": "gold",
                "ore_tonnage": {"value": 1400, "unit": "Mt", "value_mt": 1400.0},
                "grade": {"value": 0.9, "unit": "g/t Au"},
                "contained_metal": {"value": 41.0, "unit": "Moz", "value_oz": 41000000.0},
            },
            {
                "category": "Indicated",
                "commodity": "copper",
                "ore_tonnage": {"value": 5400, "unit": "Mt", "value_mt": 5400.0},
                "grade": {"value": 0.39, "unit": "% Cu"},
                "contained_metal": {"value": 22.0, "unit": "Mt", "value_t": 22000000.0},
            },
            {
                "category": "Inferred",
                "commodity": "copper",
                "ore_tonnage": {"value": 1300, "unit": "Mt", "value_mt": 1300.0},
                "grade": {"value": 0.30, "unit": "% Cu"},
                "contained_metal": {"value": 3.9, "unit": "Mt", "value_t": 3900000.0},
            },
        ],
    },
    {
        "report_id": "pilgangoora-mre-2016-south",
        "pdf": "pilgangoora_mre_2016_south.pdf",
        "lines": PILBARA_LINES,
        "meta": {
            "company": "Pilbara Minerals Limited",
            "ticker": "ASX:PLS",
            "project": "Pilgangoora South (historical, ex-Altura Mining)",
            "commodity": "lithium",
            "reporting_standard": "JORC 2012",
            "title": "Pilgangoora Mineral Resource Estimate",
            "effective_date": "2016-02-29",
            "published_date": "2016-02-29",
            "source_url": "https://media.abnnewswire.net/media/en/docs/80928-ASX-AJM-733384.pdf",
            "data_origin": "reconstructed_from_public_disclosure",
            "note": "Pilbara Minerals 按 JORC 披露，不发布 NI 43-101 技术报告。",
        },
        "expected": [
            {
                "category": "Indicated",
                "commodity": "lithium",
                "ore_tonnage": {"value": 26.7, "unit": "Mt", "value_mt": 26.7},
                "grade": {"value": 1.05, "unit": "% Li2O"},
                "contained_metal": {"value": 280000, "unit": "t Li2O", "value_t": 280000.0},
            },
            {
                "category": "Inferred",
                "commodity": "lithium",
                "ore_tonnage": {"value": 9.0, "unit": "Mt", "value_mt": 9.0},
                "grade": {"value": 1.02, "unit": "% Li2O"},
                "contained_metal": {"value": 92000, "unit": "t Li2O", "value_t": 92000.0},
            },
        ],
    },
    {
        "report_id": "pilgangoora-mre-2015-historical",
        "pdf": "pilgangoora_mre_2015_historical.pdf",
        "lines": PILBARA_2015_LINES,
        "meta": {
            "company": "Pilbara Minerals Limited",
            "ticker": "ASX:PLS",
            "project": "Pilgangoora (historical 2015 estimate)",
            "commodity": "tantalum,lithium",
            "reporting_standard": "JORC 2012",
            "title": "Historical Mineral Resource Estimate as at June 2015",
            "effective_date": "2015-06-30",
            "published_date": "2015-06-02",
            "source_url": "http://pilbaraminerals.com.au/sites/pilbaraminerals.com.au/files/beerandco-research-report20151006.pdf",
            "data_origin": "reconstructed_from_public_disclosure",
            "note": "含 Ta2O5（Mlb）与 Li2O（t）双金属混合单位，专门用来压测解析器的单位归一。",
        },
        "expected": [
            {
                "category": "Indicated",
                "commodity": "tantalum",
                "ore_tonnage": {"value": 6.0, "unit": "Mt", "value_mt": 6.0},
                "grade": {"value": 233, "unit": "ppm Ta2O5"},
                "contained_metal": {"value": 3.1, "unit": "Mlb Ta2O5", "value_t": 1406.14},
            },
            {
                "category": "Inferred",
                "commodity": "tantalum",
                "ore_tonnage": {"value": 17.8, "unit": "Mt", "value_mt": 17.8},
                "grade": {"value": 208, "unit": "ppm Ta2O5"},
                "contained_metal": {"value": 8.2, "unit": "Mlb Ta2O5", "value_t": 3719.46},
            },
        ],
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for rep in REPORTS:
        pdf_path = write_pdf(rep["pdf"], rep["lines"])
        payload = {
            "report_id": rep["report_id"],
            **rep["meta"],
            "fixtures": {
                "pdf": f"data/reports/pdfs/{rep['pdf']}",
                "pdf_url_alias": f"fixture://{rep['report_id']}",
            },
            "ground_truth": {
                "description": (
                    "本题只对 Indicated Resources 与 Inferred Resources 两类的"
                    "矿石量 / 品位 / 金属量计分；Measured、(M)+(I)、Total 行不计分。"
                ),
                "tolerance": "±5%（相对误差）",
                "rows": rep["expected"],
            },
        }
        out = OUT_DIR / f"{rep['report_id']}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{rep['report_id']:38s} pdf={pdf_path.stat().st_size:>6d}B  rows={len(rep['expected'])}")


if __name__ == "__main__":
    main()
