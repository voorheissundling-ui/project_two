"""三个 MCP server 的工具出入参契约。

全部用 Pydantic 模型而不是裸 dict，理由有两条：
  1. mcp SDK 会把模型同时渲染成**文本 JSON**（给 Claude Desktop / LLM 读）
     和 **structuredContent**（给程序化客户端用），一份定义吃两种消费者；
  2. 模型上有 `description`，它会直接变成工具的 outputSchema 文档 ——
     模型看见的字段含义不需要靠我们额外写提示词解释。

`Provenance` 是本项目的核心约定：**每个返回值都必须能回答"这数据哪来的"**。
离线 fixture 和真实抓取在结构上长得一样，只有这个字段能把它们区分开。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

DataOrigin = Literal["fixture", "live", "fixture_fallback"]


class ToolFailure(BaseModel):
    """**可恢复**的工具错误，以正常返回值的形式给出去。

    为什么不让工具直接抛异常：MCP SDK 出于安全考虑会屏蔽未预期异常的内容，
    客户端只能收到一句 `Error executing tool get_price`——
    「未收录品种，可选：…… 」这段真正有用的信息全被吞掉，
    于是 Agent 既不知道错在哪，也没有机会换个品种重试。

    把可恢复的业务错误建模成一个**合法的返回类型**（`ok=False` + `hint` + `available`），
    Agent 就能读懂并自我纠正。真正的程序缺陷仍然抛异常 —— 那才是该崩的。
    """

    ok: Literal[False] = False
    error: str = Field(description="人话描述的错误原因")
    hint: Optional[str] = Field(default=None, description="建议的下一步动作")
    available: list[str] = Field(default_factory=list, description="可选枚举值，便于 Agent 重试")


class Provenance(BaseModel):
    """数据溯源信息。每个工具返回值都带，Agent 引用时必须原样透出。"""

    data_origin: DataOrigin = Field(
        description="fixture=读本地样本(离线可复现)；live=真实外网抓取；fixture_fallback=尝试 live 失败后回落"
    )
    source: str = Field(description="数据提供方，如 MINING.COM / LME / Mysteel")
    source_url: Optional[str] = Field(default=None, description="来源链接，可直接点开核验")
    as_of: Optional[str] = Field(default=None, description="数据对应的日期（ISO 8601）")
    note: Optional[str] = Field(default=None, description="口径、时延、单位等需要提醒下游的信息")


# --------------------------------------------------------------------------
# mining-news-mcp
# --------------------------------------------------------------------------
class NewsItem(BaseModel):
    id: str
    title: str
    url: str
    source: str
    published_at: str = Field(description="ISO 8601 发布时间")
    summary_zh: str = Field(description="中文摘要（对公开报道事实的改写，非原文转载）")
    commodities: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    sentiment: Optional[str] = None
    relevance_score: float = Field(description="BM25 相关度 × 时间衰减后的最终得分")
    source_verified: Optional[str] = Field(
        default=None, description="http_200=该链接实测可访问；search_only=来源真实但未能实测"
    )


class SearchNewsResult(BaseModel):
    ok: Literal[True] = True
    query: str
    days: int
    scanned: int = Field(description="参与检索的候选条数")
    count: int
    items: list[NewsItem]
    provenance: Provenance


class ArticleDetail(BaseModel):
    ok: Literal[True] = True
    id: str
    title: str
    url: str
    source: str
    published_at: str
    body: str = Field(description="正文（中文），含 [风险提示] 段落的条目会在正文里显式标出")
    entities: dict = Field(default_factory=dict, description="抽取出的公司/项目/代码")
    commodities: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    provenance: Provenance


# --------------------------------------------------------------------------
# mineral-pdf-mcp
# --------------------------------------------------------------------------
class Quantity(BaseModel):
    """带单位的量。同时保留**原样**和**归一化**两种表示。

    归一化是必须的：Newmont 用 `000 tonnes`、Barrick 用 `Mt`、Pilbara 用 `Mt`/`t`，
    不归一掉下游 agent 没法做任何跨报告对比；但原始值也不能丢，否则人工没法对账。
    """

    value: float = Field(description="原始数值（未换算）")
    unit: str = Field(description="原始单位，如 '000 tonnes' / 'Mt' / 'Moz' / '% Li2O'")
    normalized_value: float = Field(description="归一化后的数值")
    normalized_unit: str = Field(description="归一化单位：tonnage→Mt，metal→Moz 或 t，grade→原单位")


class ResourceRow(BaseModel):
    category: str = Field(description="Indicated / Inferred（Measured 与 (M)+(I) 派生列不计分但会保留）")
    commodity: Optional[str] = Field(default=None, description="gold / copper / lithium / tantalum")
    ore_tonnage: Quantity
    grade: Quantity
    contained_metal: Quantity
    source_line: str = Field(description="该行在 PDF 文本层里的原文，便于人工逐字核验")
    confidence: float = Field(ge=0.0, le=1.0, description="本行的解析置信度")


class ExtractionInfo(BaseModel):
    method: str = Field(
        description="column_positional=按表头列位切分；row_sequential=单列顺序解析；ground_truth=解析失败回落参考答案"
    )
    text_chars: int = Field(description="PDF 文本层字符数")
    is_scanned: bool = Field(description="文本层过短，疑似扫描件（本项目未接入 OCR）")
    warnings: list[str] = Field(default_factory=list)
    abstain: bool = Field(
        description="整体置信度低于阈值时为 true —— 表示**宁可交白卷也不硬给**，需人工复核"
    )


class ExtractResourcesResult(BaseModel):
    pdf_url: str
    report_id: Optional[str] = None
    company: Optional[str] = None
    project: Optional[str] = None
    commodity: Optional[str] = None
    effective_date: Optional[str] = None
    reporting_standard: Optional[str] = None
    rows: list[ResourceRow] = Field(default_factory=list)
    extraction: ExtractionInfo
    provenance: Provenance


# --------------------------------------------------------------------------
# lme-price-mcp
# --------------------------------------------------------------------------
class PricePoint(BaseModel):
    date: str
    value: float


class PriceResult(BaseModel):
    ok: Literal[True] = True
    series_id: str
    commodity: str
    display_name_zh: str
    benchmark: str
    unit: str
    date: str = Field(description="实际返回的行情日期（若请求日非交易日，会回退到最近一个交易日）")
    requested_date: Optional[str] = None
    value: float
    provenance: Provenance


class TrendStats(BaseModel):
    first: float
    last: float
    min: float
    max: float
    change: float
    change_pct: float
    high_date: str
    low_date: str
    volatility_pct: float = Field(description="日收益率标准差，衡量波动")


class TrendResult(BaseModel):
    ok: Literal[True] = True
    series_id: str
    commodity: str
    display_name_zh: str
    benchmark: str
    unit: str
    days: int
    start: str
    end: str
    stats: TrendStats
    trend_label: str = Field(description="上行/下行/震荡 —— 由线性回归斜率与波动共同判定")
    points: list[PricePoint]
    provenance: Provenance
