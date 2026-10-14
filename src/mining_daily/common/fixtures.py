"""fixture 读取层 —— 所有 server 取数的唯一入口。

存在的意义是**把「数据从哪来」这件事收口到一处**：server 只管调 `load_news()`，
不关心背后是本地样本还是真实抓取。将来接 live 模式，只需要在这里换实现，
三个 server 一行都不用改。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from mining_daily.config import settings


class FixtureMissing(RuntimeError):
    """fixture 目录不存在或为空 —— 提示用户先跑生成脚本。"""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_dir(path: Path, build_hint: str) -> Path:
    if not path.is_dir() or not any(path.glob("*.json")):
        raise FixtureMissing(
            f"fixture 数据不存在：{path}\n先运行生成脚本：uv run python {build_hint}"
        )
    return path


# --------------------------------------------------------------------------
# 新闻
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_news() -> list[dict]:
    news_dir = _require_dir(settings.news_dir, "scripts/build_news_fixtures.py")
    payload = _load_json(news_dir / "news.json")
    return payload.get("items", [])


@lru_cache(maxsize=1)
def load_news_meta() -> dict:
    news_dir = _require_dir(settings.news_dir, "scripts/build_news_fixtures.py")
    payload = _load_json(news_dir / "news.json")
    return payload.get("_meta", {})


# --------------------------------------------------------------------------
# 储量报告
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_reports() -> list[dict]:
    reports_dir = _require_dir(settings.reports_dir, "scripts/build_report_fixtures.py")
    out = []
    for path in sorted(reports_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        out.append(_load_json(path))
    return out


def load_report(report_id: str) -> Optional[dict]:
    for rep in load_reports():
        if rep.get("report_id") == report_id:
            return rep
    return None


def resolve_pdf_path(pdf_url: str) -> Optional[Path]:
    """把 pdf_url 解析成磁盘上的 PDF 路径。

    支持三种写法，覆盖面从「Agent 拿到的真实 URL」到「本地 fixture 别名」：
      1. `fixture://<report_id>`  —— fixture 别名，Agent 可以先 list 再按别名取
      2. 仓库相对路径 `data/reports/pdfs/xxx.pdf`
      3. 真实 http(s) URL        —— 用它的最后一段文件名去 fixture 目录里找
                                  （live 模式下这里会改成真的下载，见 capture_live.py）

    第 3 条是有意为之：这样 Agent 从新闻里读到一条真实的 PDF 链接时，
    只要我们离线镜像过同一个文件名，就能**原样传 URL** 而不用改代码。
    """
    if pdf_url.startswith("fixture://"):
        rep = load_report(pdf_url.removeprefix("fixture://"))
        if rep:
            return settings.data_dir.parent / rep["fixtures"]["pdf"]
        return None

    candidate = Path(pdf_url)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    rel = settings.data_dir.parent / pdf_url
    if rel.exists():
        return rel

    # http(s) URL 或裸文件名 -> 按文件名在 pdf 目录里找
    name = pdf_url.rstrip("/").split("/")[-1].split("?")[0]
    if name:
        pdf_dir = settings.reports_dir / "pdfs"
        if pdf_dir.is_dir():
            for p in pdf_dir.iterdir():
                if p.name.lower() == name.lower():
                    return p
    return None


# --------------------------------------------------------------------------
# 价格
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_price_series() -> dict[str, dict]:
    prices_dir = _require_dir(settings.prices_dir, "scripts/build_price_fixtures.py")
    out: dict[str, dict] = {}
    for path in sorted(prices_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        payload = _load_json(path)
        out[payload["series_id"]] = payload
    return out


# 用户口语 -> series_id 的别名表。做这个是因为「锂价」到底指碳酸锂还是锂辉石，
# 靠字符串相似度猜不准，必须显式枚举主要叫法。
COMMODITY_ALIASES: dict[str, str] = {
    "lithium_carbonate": "lithium_carbonate_cn",
    "lithium_carbonate_cn": "lithium_carbonate_cn",
    "碳酸锂": "lithium_carbonate_cn",
    "电碳": "lithium_carbonate_cn",
    "lithium": "lithium_carbonate_cn",
    "锂": "lithium_carbonate_cn",
    "锂价": "lithium_carbonate_cn",
    "spodumene": "spodumene_sc6_cif_cn",
    "spodumene_sc6": "spodumene_sc6_cif_cn",
    "sc6": "spodumene_sc6_cif_cn",
    "锂辉石": "spodumene_sc6_cif_cn",
    "锂精矿": "spodumene_sc6_cif_cn",
    "copper": "lme_copper_cash",
    "铜": "lme_copper_cash",
    "lme_copper": "lme_copper_cash",
    "copper_shfe": "shfe_copper_main",
    "沪铜": "shfe_copper_main",
    "zinc": "lme_zinc_cash",
    "锌": "lme_zinc_cash",
    "nickel": "lme_nickel_cash",
    "镍": "lme_nickel_cash",
    "iron_ore": "iron_ore_62_mysteel",
    "铁矿石": "iron_ore_62_mysteel",
    "铁矿": "iron_ore_62_mysteel",
}


def resolve_series_id(commodity: str) -> Optional[str]:
    """把用户/模型给的各种叫法映射到 series_id；找不到再退回模糊匹配。"""
    key = commodity.strip()
    if key in load_price_series():
        return key
    if key.lower() in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[key.lower()]
    if key in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[key]

    low = key.lower()
    for sid, series in load_price_series().items():
        haystack = " ".join(
            [
                sid,
                series.get("commodity", ""),
                series.get("display_name_zh", ""),
                series.get("benchmark", ""),
            ]
        ).lower()
        if low in haystack:
            return sid
    return None


def clear_caches() -> None:
    """测试里切换 data_dir 后需要清缓存。"""
    load_news.cache_clear()
    load_news_meta.cache_clear()
    load_reports.cache_clear()
    load_price_series.cache_clear()
