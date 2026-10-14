"""储量提取的**字段级**准确率校验 —— 拿解析器的输出逐字段对比人工核对的 ground truth。

为什么需要这个脚本，而不是"我肉眼看了一遍没问题"：
PDF 表格解析是本项目里**唯一一处输出无法被常识判断对错**的地方。
新闻检索差了能一眼看出来，价格算错了单位很明显，但
「Indicated 铜 5,400 Mt @ 0.39% = 22.0 Mt」这种数字，**看起来永远是对的**。
不逐字段对账，就不知道解析器到底行不行。

对比口径（三档，每个字段单独判）：
  * 矿石量  —— 统一折算到 Mt 再比
  * 品位    —— 保留原始单位直接比数值（不同矿种的品位口径不可换算）
  * 金属量  —— 统一折算到 oz 或 t 再比（看 ground truth 给的是哪个）
判据是**相对误差 ≤ 5%**：PDF 里数字本身就有四舍五入，卡太死会全是假失败。

用法：
    uv run python scripts/check_extraction.py            # 走真实 MCP stdio 协议
    uv run python scripts/check_extraction.py --direct   # 直接调解析函数（快，跳过协议层）
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

TOLERANCE = 0.05  # ±5% 相对误差


def _rel_err(got: float, want: float) -> float:
    if want == 0:
        return 0.0 if got == 0 else float("inf")
    return abs(got - want) / abs(want)


def _to_mt(value: float, unit: str) -> float:
    """把矿石量折算到 Mt。"""
    u = (unit or "").strip().lower()
    if u in ("mt", "million tonnes", "000' tonnes"):
        return value
    if u in ("kt", "000 tonnes", "000t", "thousand tonnes"):
        return value / 1000.0
    if u in ("t", "tonnes"):
        return value / 1e6
    return value


def _to_oz(value: float, unit: str) -> float:
    u = (unit or "").strip().lower()
    if u in ("moz",):
        return value * 1e6
    if u in ("koz",):
        return value * 1e3
    if u in ("oz",):
        return value
    return value


def _to_t(value: float, unit: str) -> float:
    u = (unit or "").strip().lower()
    if u.startswith("mlb"):
        return value * 453.59237  # 1 Mlb = 453.59237 t
    if u.startswith("kt"):
        return value * 1e3
    if u.startswith("mt"):
        return value * 1e6
    return value  # t / 其他按吨处理


def _match(gt_row: dict, extracted: list[dict], used: set[int]) -> dict | None:
    """给一条 ground truth 找它对应的解析行。

    先按 category 卡，再按 commodity 卡（ground truth 里可能没写 commodity，
    那就退化成"同类别里第一个没用过的"）。多余的解析行不算错 ——
    见 main() 里的"额外解析"统计。
    """
    cands = [
        r
        for r in extracted
        if r.get("category") == gt_row.get("category") and id(r) not in used
    ]
    gc = (gt_row.get("commodity") or "").lower()
    if gc:
        exact = [r for r in cands if (r.get("commodity") or "").lower() == gc]
        if exact:
            cands = exact
    return cands[0] if cands else None


def compare_report(report: dict, extracted: list[dict]) -> dict:
    """逐字段对比一份报告，返回统计结果。"""
    gt_rows = report["ground_truth"]["rows"]
    checks: list[dict] = []
    used: set[int] = set()

    for gt in gt_rows:
        row = _match(gt, extracted, used)
        if row is None:
            checks.append(
                {
                    "field": "整行",
                    "category": gt.get("category"),
                    "commodity": gt.get("commodity"),
                    "got": None,
                    "want": "-",
                    "err": None,
                    "ok": False,
                    "note": "未匹配到解析行",
                }
            )
            continue
        used.add(id(row))

        pairs = [
            (
                "矿石量(Mt)",
                _to_mt(row["ore_tonnage"]["normalized_value"], row["ore_tonnage"]["normalized_unit"]),
                _to_mt(gt["ore_tonnage"]["value"], gt["ore_tonnage"]["unit"]),
            ),
            ("品位", row["grade"]["value"], gt["grade"]["value"]),
        ]

        cont = gt["contained_metal"]
        if "value_oz" in cont:
            pairs.append(
                (
                    "金属量(oz)",
                    _to_oz(
                        row["contained_metal"]["normalized_value"],
                        row["contained_metal"]["normalized_unit"],
                    ),
                    cont["value_oz"],
                )
            )
        else:
            pairs.append(
                (
                    "金属量(t)",
                    _to_t(
                        row["contained_metal"]["normalized_value"],
                        row["contained_metal"]["normalized_unit"],
                    ),
                    cont.get("value_t", cont["value"]),
                )
            )

        for field, got, want in pairs:
            err = _rel_err(got, want)
            checks.append(
                {
                    "field": field,
                    "category": gt.get("category"),
                    "commodity": gt.get("commodity"),
                    "got": got,
                    "want": want,
                    "err": err,
                    "ok": err <= TOLERANCE,
                    "note": "",
                }
            )

    extra = [r for r in extracted if id(r) not in used]
    return {
        "report_id": report["report_id"],
        "checks": checks,
        "extra": [
            {
                "category": r.get("category"),
                "commodity": r.get("commodity"),
                "confidence": r.get("confidence"),
            }
            for r in extra
        ],
        "n_ok": sum(1 for c in checks if c["ok"]),
        "n_total": len(checks),
    }


# --------------------------------------------------------------------------
# 两种取数方式
# --------------------------------------------------------------------------
def extract_direct(report_id: str) -> list[dict]:
    """直接调解析函数，不经过 MCP 协议。"""
    from mining_daily.common import fixtures
    from mining_daily.common import resource_extract as rx

    report = fixtures.load_report(report_id)
    pdf_path = REPO / report["fixtures"]["pdf"]
    from pypdf import PdfReader

    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf_path)).pages)
    # extract_rows 返回 (rows, notes, meta) 三元组 —— 别只接第一个还当列表迭代
    rows, _notes, _meta = rx.extract_rows(text)
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    """把 `ExtractedRow` 归一成和 MCP 返回体**同构**的 dict。

    这一步不能偷懒用 `dataclasses.asdict()`：ExtractedRow 是**扁平**的
    （`tonnage_value` / `tonnage_mt` / `contained_normalized`…），
    而 MCP 返回的 ResourceRow 是**嵌套**的
    （`ore_tonnage.value` / `ore_tonnage.normalized_value`…）。
    直接 asdict 出来的形状对不上，--direct 模式会全线 KeyError，
    或者更糟——看着跑通了但比的根本不是同一批字段。

    所以这里逐字段照搬 pdf_server 里的映射，保证两种模式下的对比口径完全一致。
    """
    return {
        "category": row.category,
        "commodity": row.commodity,
        "ore_tonnage": {
            "value": row.tonnage_value,
            "unit": row.tonnage_unit,
            "normalized_value": row.tonnage_mt,
            "normalized_unit": "Mt",
        },
        "grade": {
            "value": row.grade_value,
            "unit": row.grade_unit,
            "normalized_value": row.grade_value,
            "normalized_unit": row.grade_unit,
        },
        "contained_metal": {
            "value": row.contained_value,
            "unit": row.contained_unit,
            "normalized_value": row.contained_normalized,
            "normalized_unit": row.contained_normalized_unit,
        },
        "source_line": row.source_line,
        "confidence": row.confidence,
    }


async def extract_via_mcp(report_ids: list[str]) -> dict[str, list[dict]]:
    """走真实 stdio 协议取数 —— 顺带验证 server 的序列化这一层。

    复用 Agent 用的 `MCPHub`（只挂 pdf 这一个 server），而不是自己手搓 ClientSession：
    返回体的解包规则（先看 `structuredContent`，再看 TextContent 里的 JSON）
    只该有一份实现，否则校验脚本和 Agent 可能连的不是同一套东西 ——
    那这个校验就失去了意义。
    """
    from mining_daily.agent.mcp_hub import SERVERS, MCPHub

    out: dict[str, list[dict]] = {}
    async with MCPHub({"pdf": SERVERS["pdf"]}) as hub:
        for rid in report_ids:
            payload = await hub.call("pdf", "extract_resources", {"pdf_url": f"fixture://{rid}"})
            out[rid] = payload.get("rows", [])
    return out


def main() -> int:
    # Windows 控制台默认 GBK，中文表格会崩。放在 main() 里而不是模块顶层：
    # 这样 pytest import 本模块复用 compare_report 时不会被这个包装干扰（会吃掉捕获输出）。
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="储量提取字段级准确率校验")
    parser.add_argument("--direct", action="store_true", help="跳过 MCP 协议，直接调解析函数")
    args = parser.parse_args()

    from mining_daily.common import fixtures

    reports = fixtures.load_reports()
    report_ids = [r["report_id"] for r in reports]

    if args.direct:
        extracted = {rid: extract_direct(rid) for rid in report_ids}
    else:
        extracted = asyncio.run(extract_via_mcp(report_ids))

    all_checks: list[dict] = []
    n_failed_reports = 0

    for report in reports:
        rid = report["report_id"]
        res = compare_report(report, extracted.get(rid, []))
        all_checks += res["checks"]

        status = "✅" if res["n_ok"] == res["n_total"] else "❌"
        if res["n_ok"] != res["n_total"]:
            n_failed_reports += 1
        print("=" * 92)
        print(f"{status} {rid}  ({report.get('company')} / {report.get('reporting_standard')})")
        print(f"   字段命中 {res['n_ok']}/{res['n_total']}")
        for c in res["checks"]:
            mark = "  ✅" if c["ok"] else "  ❌"
            desc = f"{c['category'] or '-'}"
            if c["commodity"]:
                desc += f" {c['commodity']}"
            if c["got"] is None:
                print(f"{mark} {desc:<22} {c['field']:<12} {c['note']}")
                continue
            err = f"{c['err'] * 100:6.2f}%"
            print(
                f"{mark} {desc:<22} {c['field']:<12} "
                f"got={c['got']:>16,.2f}  want={c['want']:>16,.2f}  err={err}"
            )
        if res["extra"]:
            extras = ", ".join(
                f"{e['category']}/{e['commodity']}(conf={e['confidence']})" for e in res["extra"]
            )
            print(f"   ℹ️  额外解析出未列入 ground truth 的行：{extras}")

    n_ok = sum(1 for c in all_checks if c["ok"])
    n_total = len(all_checks)
    acc = n_ok / n_total * 100 if n_total else 0.0

    print("=" * 92)
    print(f"字段级准确率：{n_ok}/{n_total} = {acc:.1f}%（容差 ±{TOLERANCE * 100:.0f}% 相对误差）")
    if n_failed_reports:
        print(f"有 {n_failed_reports} 份报告存在未通过字段")
    else:
        print("全部通过 ✅")

    return 0 if n_ok == n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
