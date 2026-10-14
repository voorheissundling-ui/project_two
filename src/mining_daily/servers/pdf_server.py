"""mineral-pdf-mcp —— 矿产储量报告（NI 43-101 / JORC）结构化提取 MCP server。

暴露一个工具：
  extract_resources(pdf_url)   —— 取 Indicated / Inferred 两类的
                                  矿石量 / 品位 / 金属量，全部带单位

这个 server 存在的意义，是把「PDF 里那张表」变成 Agent 能直接引用的结构化字段。
但它最该被记住的是一条**弃权原则**：

    宁可不给答案，也不要给一个错的答案。

矿产储量是结论性数字，下游可能直接写进投资备忘录。解析器只认出半张表、
或者拿到的是扫描件（没有文本层）时，硬凑出来的数字比「查不到」危害大得多。
所以：

  * 文本层过短 → 判定为扫描件，abstain=True，明确说「本项目不接 OCR」；
  * 一行里缺少必需字段（矿石量/品位） → 该行直接丢弃，不做插值也不猜；
  * 整体平均置信度低于阈值 → abstain=True，附上告警让人工复核。

每个返回值都带 `source_line`（该行在 PDF 文本层里的原文），
人工可以逐字核验数字到底是哪来的 —— 这是「可审计」的最低成本实现。
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer

from mining_daily.common import fixtures, resource_extract as rx
from mining_daily.common.schemas import (
    ExtractionInfo,
    ExtractResourcesResult,
    Provenance,
    Quantity,
    ResourceRow,
)
from mining_daily.common.logging_setup import configure_logging
from mining_daily.config import settings

server = MCPServer(
    name="mineral-pdf-mcp",
    version="0.1.0",
    instructions=(
        "矿产储量报告解析服务。输入 PDF 链接，返回 Indicated / Inferred 两类的"
        "矿石量、品位、金属量。所有量都带 unit 字段，且同时给出归一化后的数值。"
        "如果返回 extraction.abstain=true，说明解析器没把握，"
        "**不要引用 rows 里的数字**，直接告诉用户需要人工核对。"
    ),
)

# 整体置信度阈值。低于它就弃权 —— 一个字段错比不给更糟。
ABSTAIN_CONFIDENCE = 0.55

_METHOD = "column_positional"


def _match_report_by_pdf(path) -> Optional[dict]:
    """按 PDF 文件名反查它属于哪份报告，用来补齐公司/项目/生效日期。"""
    for rep in fixtures.load_reports():
        pdf_rel = rep.get("fixtures", {}).get("pdf", "")
        if pdf_rel and pdf_rel.replace("\\", "/").endswith(path.name):
            return rep
    return None


def _extract_text(pdf_path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _to_quantity(value: float, unit: str, normalized: float, norm_unit: str) -> Quantity:
    return Quantity(
        value=value, unit=unit, normalized_value=normalized, normalized_unit=norm_unit
    )


@server.tool()
def extract_resources(pdf_url: str) -> ExtractResourcesResult:
    """从矿产储量报告 PDF 中提取 Indicated / Inferred 资源量。

    只返回这两类，因为 Measured 与 (M)+(I) 是派生/非经济性口径；
    Total 行是逐矿体的合计，重复计入会让总量翻倍。

    Args:
        pdf_url: PDF 链接。支持 `fixture://<report_id>` 别名、
            仓库相对路径，或真实 http(s) 链接（按文件名匹配离线镜像）。
    """
    path = fixtures.resolve_pdf_path(pdf_url)
    report = None
    if path is not None:
        report = _match_report_by_pdf(path)

    base_provenance = Provenance(
        data_origin="fixture" if settings.mode == "fixture" else "live",
        source=(report or {}).get("company", "mineral report"),
        source_url=(report or {}).get("source_url", pdf_url),
        as_of=(report or {}).get("effective_date"),
        note="fixture 版 PDF 依据公开披露重建，正文含数据出处说明；仅用于离线演示。",
    )

    if path is None or not path.exists():
        return ExtractResourcesResult(
            pdf_url=pdf_url,
            report_id=(report or {}).get("report_id"),
            extraction=ExtractionInfo(
                method=_METHOD,
                text_chars=0,
                is_scanned=False,
                warnings=[f"找不到该 PDF：{pdf_url}。可用 fixture://<report_id> 或仓库相对路径。"],
                abstain=True,
            ),
            provenance=base_provenance,
        )

    text = _extract_text(path)
    warnings: list[str] = []

    if rx.detect_scanned(text):
        return ExtractResourcesResult(
            pdf_url=pdf_url,
            report_id=(report or {}).get("report_id"),
            company=(report or {}).get("company"),
            project=(report or {}).get("project"),
            commodity=(report or {}).get("commodity"),
            effective_date=(report or {}).get("effective_date"),
            reporting_standard=(report or {}).get("reporting_standard"),
            rows=[],
            extraction=ExtractionInfo(
                method=_METHOD,
                text_chars=len(text.strip()),
                is_scanned=True,
                warnings=[
                    "PDF 没有可用文本层（疑似扫描件）。本项目未接入 OCR，"
                    "不做图像识别，避免给出未经核验的数字。"
                ],
                abstain=True,
            ),
            provenance=base_provenance,
        )

    rows, notes, diagnostics = rx.extract_rows(text)
    warnings.extend(f"{n.level}: {n.message}" for n in notes)

    confidences = [r.confidence for r in rows]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    abstain = (not rows) or avg_conf < ABSTAIN_CONFIDENCE

    if not rows:
        warnings.append(
            "没有解析出任何 Indicated / Inferred 行。可能是版式未覆盖，"
            "请人工核对，不要凭报告标题推断数字。"
        )

    out_rows = [
        ResourceRow(
            category=r.category,
            commodity=r.commodity,
            ore_tonnage=_to_quantity(r.tonnage_value, r.tonnage_unit, r.tonnage_mt, "Mt"),
            grade=_to_quantity(r.grade_value, r.grade_unit, r.grade_value, r.grade_unit),
            contained_metal=_to_quantity(
                r.contained_value,
                r.contained_unit,
                r.contained_normalized,
                r.contained_normalized_unit,
            ),
            source_line=r.source_line,
            confidence=r.confidence,
        )
        for r in rows
    ]

    if warnings:
        base_provenance.note = (
            f"{base_provenance.note} 解析诊断：{'；'.join(warnings)}"
        )

    return ExtractResourcesResult(
        pdf_url=pdf_url,
        report_id=(report or {}).get("report_id"),
        company=(report or {}).get("company"),
        project=(report or {}).get("project"),
        commodity=(report or {}).get("commodity"),
        effective_date=(report or {}).get("effective_date"),
        reporting_standard=(report or {}).get("reporting_standard"),
        rows=out_rows,
        extraction=ExtractionInfo(
            method=_METHOD,
            text_chars=len(text.strip()),
            is_scanned=False,
            warnings=warnings,
            abstain=abstain,
        ),
        provenance=base_provenance,
    )


def main() -> None:
    configure_logging()  # 日志只走 stderr，不污染 stdio 上的 JSON-RPC
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
