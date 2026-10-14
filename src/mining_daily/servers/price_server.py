"""lme-price-mcp —— 大宗商品价格行情 MCP server。

暴露两个工具：
  get_price(commodity, date)      —— 取某个交易日的价格
  get_trend(commodity, days)      —— 取近 N 天走势 + 统计 + 趋势判定

两个真实世界的坑，这个 server 必须替上游挡掉：

1. **非交易日**。用户问"10 月 5 日锂价"，那天是国庆假期，根本没有报价。
   直接返回 0 或报错都不对 —— 正确做法是**回退到最近一个交易日**，并把实际日期
   放进 `date` 字段、把请求日期放进 `requested_date`，让 Agent 能说清"你要的是 X，我给的是 Y"。

2. **单位口径**。LME 是 USD/t、SHFE 是 CNY/t、COMEX 铜是美分/磅、SC6 是 USD/t CIF、
   铁矿石是 USD/干吨。单位必须**跟着数值一起走**，绝对不能在服务端偷偷换算 ——
   一旦换算，下游就再也无法区分"这个 110990 是人民币还是美元"。
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import pstdev
from typing import Optional

from mcp.server.mcpserver import MCPServer

from mining_daily.common import fixtures
from mining_daily.common.logging_setup import configure_logging
from mining_daily.common.schemas import (
    PricePoint,
    PriceResult,
    Provenance,
    ToolFailure,
    TrendResult,
    TrendStats,
)
from mining_daily.config import settings

server = MCPServer(
    name="lme-price-mcp",
    version="0.1.0",
    instructions=(
        "大宗商品行情服务，覆盖 LME 铜/锌/镍、SHFE 沪铜、电池级碳酸锂、锂辉石精矿 SC6、"
        "铁矿石 62% 澳粉。所有返回值都带 unit 字段，引用价格时**必须连单位一起引用**。"
    ),
)


def _resolve_series(commodity: str) -> tuple[Optional[dict], Optional[ToolFailure]]:
    """解析品种。查不到时**返回可读的失败体**而不是抛异常。

    见 ToolFailure 的说明：SDK 会屏蔽未预期异常的正文，抛异常会让 Agent
    只看到一句「Error executing tool get_price」，既不知原因也没法换品种重试。
    """
    series_id = fixtures.resolve_series_id(commodity)
    if series_id is None:
        options = fixtures.load_price_series()
        return None, ToolFailure(
            error=f"未收录品种「{commodity}」。",
            hint="请改用下列品种名（中英文别名均可：锂/碳酸锂/lithium_carbonate、铜/沪铜/LME铜 等）重试。",
            available=[f"{sid}（{s['display_name_zh']}）" for sid, s in options.items()],
        )
    return fixtures.load_price_series()[series_id], None


def _provenance(series: dict, as_of: str) -> Provenance:
    src = series.get("source", {})
    return Provenance(
        data_origin="fixture" if settings.mode == "fixture" else "live",
        source=src.get("name", "unknown"),
        source_url=src.get("url"),
        as_of=as_of,
        note=(
            f"{series.get('data_origin_note', '')} 单位：{series['unit']}。"
            f"数据获取约束：{src.get('access', '')}"
        ).strip(),
    )


def _pick_on_or_before(points: list[dict], target: date) -> Optional[dict]:
    """取 <= target 的最近一个交易日。非交易日自动回退。"""
    candidates = [p for p in points if date.fromisoformat(p["date"]) <= target]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p["date"])


def _trend_label(points: list[dict]) -> str:
    """用最小二乘斜率（按日归一）判定趋势方向，并用波动率做阈值。

    单纯比较首尾值会被单日跳空骗到；用回归斜率更稳。
    阈值取「日均变化超过价格均值的 0.15%」才认定趋势，否则算震荡。
    """
    if len(points) < 5:
        return "样本不足"
    values = [p["value"] for p in points]
    n = len(values)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n)) or 1.0
    slope_per_day = num / den
    threshold = mean_y * 0.0015
    if slope_per_day > threshold:
        return "上行"
    if slope_per_day < -threshold:
        return "下行"
    return "震荡"


@server.tool()
def get_price(commodity: str, date_str: Optional[str] = None) -> PriceResult | ToolFailure:
    """取指定品种在某个交易日的价格。

    若请求日不是交易日（周末/节假日），自动回退到**最近一个交易日**，
    此时返回体的 `date` 是实际取值日、`requested_date` 是你要的日子。

    Args:
        commodity: 品种。支持中文与英文别名：锂/碳酸锂/碳酸锂现货/lithium_carbonate、
            spodumene/锂辉石/SC6、copper/铜/LME铜、沪铜/copper_shfe、zinc/锌、nickel/镍、铁矿石/iron_ore。
        date_str: 目标日期 YYYY-MM-DD。留空表示取数据集中最新一笔。
    """
    series, failure = _resolve_series(commodity)
    if failure:
        return failure
    points = series["points"]

    if date_str:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            return ToolFailure(
                error=f"日期格式应为 YYYY-MM-DD，收到「{date_str}」。",
                hint="请改用 2026-10-13 这样的写法重试。",
            )
    else:
        target = date.fromisoformat(points[-1]["date"])

    picked = _pick_on_or_before(points, target)
    if picked is None:
        return ToolFailure(
            error=(
                f"{series['display_name_zh']} 在 {target} 及之前没有数据，"
                f"序列起始于 {points[0]['date']}。"
            ),
            hint="请把日期调到晚于序列起始日，或不传 date_str 取最新一笔。",
        )

    return PriceResult(
        series_id=series["series_id"],
        commodity=series["commodity"],
        display_name_zh=series["display_name_zh"],
        benchmark=series["benchmark"],
        unit=series["unit"],
        date=picked["date"],
        requested_date=target.isoformat() if date_str else None,
        value=picked["value"],
        provenance=_provenance(series, picked["date"]),
    )


@server.tool()
def get_trend(commodity: str, days: int = 30) -> TrendResult | ToolFailure:
    """取某品种近 N 天的价格走势、区间统计与趋势判定。

    Args:
        commodity: 品种，同 get_price。
        days: 回溯天数（自然日），默认 30。窗口按**数据集最新日期**往回推。
    """
    series, failure = _resolve_series(commodity)
    if failure:
        return failure
    all_points = series["points"]
    if days <= 0:
        return ToolFailure(error=f"days 必须为正整数，收到 {days}。", hint="例如 days=30。")

    end = date.fromisoformat(all_points[-1]["date"])
    start = end - timedelta(days=days)
    points = [p for p in all_points if date.fromisoformat(p["date"]) >= start]
    if len(points) < 2:
        points = all_points[-max(days, 2) :]

    values = [p["value"] for p in points]
    changes = [
        (values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))
    ]
    change = values[-1] - values[0]
    high = max(points, key=lambda p: p["value"])
    low = min(points, key=lambda p: p["value"])

    stats = TrendStats(
        first=values[0],
        last=values[-1],
        min=low["value"],
        max=high["value"],
        change=round(change, 2),
        change_pct=round(change / values[0] * 100, 2),
        high_date=high["date"],
        low_date=low["date"],
        volatility_pct=round(pstdev(changes) * 100, 3) if len(changes) > 1 else 0.0,
    )

    return TrendResult(
        series_id=series["series_id"],
        commodity=series["commodity"],
        display_name_zh=series["display_name_zh"],
        benchmark=series["benchmark"],
        unit=series["unit"],
        days=days,
        start=points[0]["date"],
        end=points[-1]["date"],
        stats=stats,
        trend_label=_trend_label(points),
        points=[PricePoint(date=p["date"], value=p["value"]) for p in points],
        provenance=_provenance(series, points[-1]["date"]),
    )


def main() -> None:
    configure_logging()  # 日志只走 stderr，不污染 stdio 上的 JSON-RPC
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
