"""生成 `data/prices/*.json` —— 价格时间序列 fixture。

诚实性设计（这条是重点，别删）：
真实世界里拿不到免费的历史日频序列（LME 历史要付费授权、SMM 历史 1999 元起）。
所以我们不**假装**整条序列都是原始行情，而是：

  1. `anchors` 里逐条列出**公开可查证的真实报价**（附日期与出处）；
  2. 锚点之间的点用**确定性**插值 + 带种子的均值回归噪声补齐，
     同一天跑多少次结果完全一样（seeded），保证评测可复现；
  3. 落盘时写 `data_origin = "captured_anchor_interpolated"` 和 `anchor_count`，
     下游（含 Agent 的 prompt）**能一眼看出哪部分是实锚、哪部分是插值**。

锚点数据出处见 `data/_sources/price_research_2026-10-14.md`。

用法：
    uv run python scripts/build_price_fixtures.py
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "prices"

# 中国市场节假日（只需覆盖序列区间；国庆 + 中秋）
CN_HOLIDAYS = {
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3),
    date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
    date(2026, 6, 19),  # 端午
    date(2026, 9, 25), date(2026, 9, 26),  # 中秋
}


def d(s: str) -> date:
    y, m, dd = (int(x) for x in s.split("-"))
    return date(y, m, dd)


def trading_days(start: date, end: date, market: str) -> list[date]:
    """按市场过滤交易日。所有品种统一按工作日处理，中国市场额外剔除节假日。"""
    out = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and not (market == "CN" and cur in CN_HOLIDAYS):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def interpolate(anchors: list[tuple[date, float]], days: list[date]) -> list[float]:
    """分段线性插值；区间外做平推（不外推，避免造出不存在的趋势）。"""
    anchors = sorted(anchors)
    out: list[float] = []
    for day in days:
        if day <= anchors[0][0]:
            out.append(anchors[0][1])
            continue
        if day >= anchors[-1][0]:
            out.append(anchors[-1][1])
            continue
        for (d0, v0), (d1, v1) in zip(anchors, anchors[1:]):
            if d0 <= day <= d1:
                span = (d1 - d0).days or 1
                w = (day - d0).days / span
                out.append(v0 + (v1 - v0) * w)
                break
    return out


def add_noise(values: list[float], seed: int, rel_sigma: float) -> list[float]:
    """带种子的均值回归噪声：让曲线像行情，但每次生成结果完全一致。

    用一阶自回归（AR(1)）而不是白噪声，因为真实行情是**有惯性**的——
    白噪声会得到锯齿状曲线，一眼假。
    """
    rng = random.Random(seed)
    out: list[float] = []
    dev = 0.0
    for v in values:
        dev = 0.85 * dev + rng.gauss(0.0, rel_sigma)
        dev = max(-0.06, min(0.06, dev))  # 限幅，别把锚点带偏太远
        out.append(v * (1.0 + dev))
    return out


def snap_to_anchors(days: list[date], values: list[float], anchors) -> list[float]:
    """把锚点当天的值**精确置回**真实数字——插值可以有误差，真实报价不行。"""
    idx = {day: i for i, day in enumerate(days)}
    out = list(values)
    for day, val in anchors:
        if day in idx:
            out[idx[day]] = float(val)
    return out


def round_to(values: list[float], step: float) -> list[float]:
    return [round(v / step) * step for v in values]


SERIES = [
    {
        "series_id": "lithium_carbonate_cn",
        "commodity": "lithium_carbonate",
        "display_name_zh": "电池级碳酸锂（中国现货均价）",
        "benchmark": "Battery-grade lithium carbonate, China spot",
        "unit": "CNY/t",
        "currency": "CNY",
        "market": "CN",
        "round_step": 50,
        "seed": 20260513,
        "rel_sigma": 0.010,
        "source": {
            "name": "SMM 上海有色网",
            "url": "https://www.smm.cn/",
            "access": "登录墙，历史序列付费（现货历史 ¥1999 起）",
            "note": "akshare 的现货接口源自生意社，是另一套口径，不可与本序列混用",
        },
        "anchors": [
            ("2026-04-01", 165000, "2026-04 中旬区间回溯，报道口径"),
            ("2026-05-13", 200500, "年内高点，现货均价（证券时报/Mysteel 报道）"),
            ("2026-07-01", 152000, "7 月回落区间中值"),
            ("2026-08-14", 135000, "8 月区间中值"),
            ("2026-10-13", 111650, "SMM 报价 106,700–116,600 的均价"),
        ],
    },
    {
        "series_id": "spodumene_sc6_cif_cn",
        "commodity": "spodumene_concentrate",
        "display_name_zh": "锂辉石精矿 SC6（CIF 中国）",
        "benchmark": "Spodumene concentrate 6% Li2O, CIF China",
        "unit": "USD/t",
        "currency": "USD",
        "market": "GLOBAL",
        "round_step": 5,
        "seed": 20260701,
        "rel_sigma": 0.012,
        "source": {
            "name": "SMM / InfoLink（转引天齐锂业半年报）",
            "url": "https://steel.smm.cn/news/13936/103991010",
            "access": "公开报道摘要可得，历史序列付费",
        },
        "anchors": [
            ("2026-04-01", 2450, "4 月区间中值"),
            ("2026-05-20", 2800, "年内高点，SMM/InfoLink 报 2,780–2,840 的均价"),
            ("2026-07-15", 2050, "7 月回落至约 2,000–2,100"),
            ("2026-10-13", 1950, "10 月成本线附近"),
        ],
    },
    {
        "series_id": "lme_copper_cash",
        "commodity": "copper",
        "display_name_zh": "LME 铜（现货官方报价）",
        "benchmark": "LME Copper Cash Official Price",
        "unit": "USD/t",
        "currency": "USD",
        "market": "GLOBAL",
        "round_step": 1,
        "seed": 20261012,
        "rel_sigma": 0.008,
        "source": {
            "name": "LME Official Prices",
            "url": "https://www.lme.com/en/market-data/reports-and-data/lme-official-prices",
            "access": "免费但为次一交易日延迟；历史序列需付费授权",
        },
        "anchors": [
            ("2026-04-01", 13100, "二季度初区间"),
            ("2026-09-08", 14617, "3M 合约报历史新高（报道口径）"),
            ("2026-10-08", 14525, "LME 官方报价现货买价"),
            ("2026-10-12", 14822, "LME 官方报价现货结算价"),
        ],
    },
    {
        "series_id": "lme_zinc_cash",
        "commodity": "zinc",
        "display_name_zh": "LME 锌（现货官方报价）",
        "benchmark": "LME Zinc Cash Official Price",
        "unit": "USD/t",
        "currency": "USD",
        "market": "GLOBAL",
        "round_step": 1,
        "seed": 20260301,
        "rel_sigma": 0.009,
        "source": {
            "name": "LME Official Prices",
            "url": "https://www.lme.com/en/market-data/reports-and-data/lme-official-prices",
            "access": "次一交易日延迟",
        },
        "anchors": [
            ("2026-04-01", 3420, "二季度初区间"),
            ("2026-07-01", 3650, "三季度初区间"),
            ("2026-09-15", 3790, "9 月中旬区间"),
            ("2026-10-12", 3811, "LME 官方报价现货结算价"),
        ],
    },
    {
        "series_id": "lme_nickel_cash",
        "commodity": "nickel",
        "display_name_zh": "LME 镍（现货官方报价）",
        "benchmark": "LME Nickel Cash Official Price",
        "unit": "USD/t",
        "currency": "USD",
        "market": "GLOBAL",
        "round_step": 5,
        "seed": 20261013,
        "rel_sigma": 0.011,
        "source": {
            "name": "LME Official Prices",
            "url": "https://www.lme.com/en/market-data/reports-and-data/lme-official-prices",
            "access": "次一交易日延迟",
        },
        "anchors": [
            ("2026-04-01", 16200, "二季度初区间"),
            ("2026-07-01", 15900, "三季度初区间"),
            ("2026-09-15", 15600, "9 月中旬区间"),
            ("2026-10-12", 15515, "LME 官方报价现货结算价"),
        ],
    },
    {
        "series_id": "shfe_copper_main",
        "commodity": "copper_shfe",
        "display_name_zh": "沪铜主力合约（SHFE）",
        "benchmark": "SHFE Copper Main Contract (CU)",
        "unit": "CNY/t",
        "currency": "CNY",
        "market": "CN",
        "round_step": 10,
        "seed": 20261011,
        "rel_sigma": 0.007,
        "source": {
            "name": "上海期货交易所",
            "url": "https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/",
            "access": "日度报表免费；实时行情需付费授权",
        },
        "anchors": [
            ("2026-04-01", 98200, "二季度初区间"),
            ("2026-07-01", 104500, "三季度初区间"),
            ("2026-10-12", 110630, "CU2611 收盘"),
            ("2026-10-13", 110990, "CU2611 收盘（结算 110,770）"),
        ],
    },
    {
        "series_id": "iron_ore_62_mysteel",
        "commodity": "iron_ore",
        "display_name_zh": "铁矿石 62% 澳粉指数（Mysteel，美元/干吨）",
        "benchmark": "Mysteel 62% Australian Fines, USD/dmt",
        "unit": "USD/dmt",
        "currency": "USD",
        "market": "CN",
        "round_step": 0.01,
        "seed": 20260901,
        "rel_sigma": 0.006,
        "source": {
            "name": "Mysteel 我的钢铁网",
            "url": "https://tks.mysteel.com/",
            "access": "网页可读；历史序列与 API 付费（IOSCO 认证指数）",
            "note": "与 Platts 62% 指数是两家机构的两个指数，数值接近但不等同",
        },
        "anchors": [
            ("2026-07-01", 96.20, "三季度初区间"),
            ("2026-09-30", 98.27, "Mysteel 9 月月度汇总"),
            ("2026-10-09", 91.60, "Mysteel 日报（月环比 -10.68%，年同比 -12.76%）"),
            ("2026-10-12", 90.83, "62% Fe fines 基准（天津港到岸）"),
            ("2026-10-14", 91.70, "Mysteel 日报（-0.75）"),
        ],
    },
]


def build_series(spec: dict) -> dict:
    anchors = [(d(ds), float(v)) for ds, v, _ref in spec["anchors"]]
    anchor_refs = [
        {"date": ds, "value": v, "reference": ref} for ds, v, ref in spec["anchors"]
    ]
    start, end = anchors[0][0], anchors[-1][0]

    days = trading_days(start, end, spec["market"])
    base = interpolate(anchors, days)
    noisy = add_noise(base, spec["seed"], spec["rel_sigma"])
    noisy = snap_to_anchors(days, noisy, anchors)
    values = round_to(noisy, spec["round_step"])

    points = [{"date": day.isoformat(), "value": v} for day, v in zip(days, values)]
    vals = [p["value"] for p in points]
    return {
        "series_id": spec["series_id"],
        "commodity": spec["commodity"],
        "display_name_zh": spec["display_name_zh"],
        "benchmark": spec["benchmark"],
        "unit": spec["unit"],
        "currency": spec["currency"],
        "frequency": "daily",
        "period": {"start": points[0]["date"], "end": points[-1]["date"]},
        "data_origin": "captured_anchor_interpolated",
        "data_origin_note": (
            "锚点(anchors)为公开可查证的真实报价；锚点之间的点由确定性插值+带种子噪声生成，"
            "仅用于演示与回归，不可用于交易决策。"
        ),
        "anchor_count": len(anchors),
        "anchors": anchor_refs,
        "source": spec["source"],
        "stats": {
            "first": vals[0],
            "last": vals[-1],
            "min": min(vals),
            "max": max(vals),
            "change_pct": round((vals[-1] - vals[0]) / vals[0] * 100, 2),
        },
        "points": points,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for spec in SERIES:
        payload = build_series(spec)
        path = OUT_DIR / f"{spec['series_id']}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        s = payload["stats"]
        print(
            f"{spec['series_id']:26s} {len(payload['points']):3d} pts "
            f"{payload['period']['start']}..{payload['period']['end']}  "
            f"last={s['last']} {payload['unit']}  chg={s['change_pct']:+.2f}%"
        )


if __name__ == "__main__":
    main()
