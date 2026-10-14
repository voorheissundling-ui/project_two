"""储量提取的**字段级准确率闸门** —— 这是整个测试套件里最重要的一个文件。

为什么它必须存在：PDF 表格解析是本项目里**唯一一处输出无法被常识判断对错**的地方。
新闻检索差了能一眼看出来，价格算错单位很明显，但
「Indicated 铜 5,400 Mt @ 0.39% = 22.0 Mt」这种数字，**看起来永远是对的**。
没有逐字段对账，就没有任何证据说明解析器能用。

对比逻辑直接复用 `scripts/check_extraction.py` —— 不在测试里重写一遍，
否则「怎么算对」就有了两份定义，两份迟早会分叉，
而分叉的那天这个闸门就悄悄失效了。
"""

from __future__ import annotations

import pytest

from check_extraction import TOLERANCE, compare_report, extract_direct
from mining_daily.common import fixtures

REPORTS = fixtures.load_reports()
REPORT_IDS = sorted(r["report_id"] for r in REPORTS)


@pytest.fixture(scope="module")
def results() -> dict[str, dict]:
    """一次性解析全部报告，避免每个用例都重跑 PDF。"""
    out = {}
    for report in REPORTS:
        rows = extract_direct(report["report_id"])
        out[report["report_id"]] = compare_report(report, rows)
    return out


def test_all_reports_are_covered():
    assert len(REPORTS) == 4, "ground truth 报告数量变了，下面的用例需要同步更新"
    assert REPORT_IDS == [
        "barrick-mineral-resources-2024",
        "newmont-gold-resources-2025",
        "pilgangoora-mre-2015-historical",
        "pilgangoora-mre-2016-south",
    ]


@pytest.mark.parametrize("report_id", REPORT_IDS)
def test_no_missing_rows(report_id, results):
    """每一行 ground truth 都必须被解析出来。"""
    missing = [c for c in results[report_id]["checks"] if c["got"] is None]
    assert not missing, f"{report_id} 漏解析的行：{[c['category'] for c in missing]}"


@pytest.mark.parametrize("report_id", REPORT_IDS)
def test_all_fields_within_tolerance(report_id, results):
    """每个字段的相对误差都必须 ≤ 5%。"""
    bad = [c for c in results[report_id]["checks"] if not c["ok"]]
    assert not bad, "\n".join(
        f"  {c['category']}/{c['commodity']} {c['field']}: "
        f"got={c['got']}, want={c['want']}, err={c['err']:.2%}"
        for c in bad
    )


def test_overall_field_accuracy_is_100_percent(results):
    """总闸门：30 个字段全中。

    容差给了 ±5%（PDF 里的数字本身就有四舍五入），但实际最大误差只有 0.0003%，
    所以这里再卡一道**远比业务容差严的**闸门：任何字段偏超过 0.01% 就算回归。

    为什么不是严格等于 0：Pilbara 2015 的钽用 `Mlb` 计量，
    ground truth 存的是换算后**四舍五入到 2 位小数的吨数**（3.1 Mlb → 1406.14 t），
    而解析器算的是全精度 1406.1361… t。这点残差是 ground truth 的表示精度造成的，
    不是解析错误 —— 把它当成 0 会让这个断言变成一颗定时炸弹。
    """
    checks = [c for r in results.values() for c in r["checks"]]
    ok = [c for c in checks if c["ok"]]
    assert len(checks) == 30, f"字段总数变了：{len(checks)}"
    assert len(ok) == len(checks), (
        f"字段级准确率 {len(ok)}/{len(checks)}，未达 100%（容差 ±{TOLERANCE:.0%}）"
    )
    worst = max((c["err"] for c in checks), default=0.0)
    assert worst <= 1e-4, f"误差超过 0.01% 的回归水位：{worst:.6%}"


def test_pilbara_2015_extra_lithium_rows_are_reported(results):
    """Pilbara 2015 的 ground truth 只登记了钽，但解析器额外读出了锂。

    这不是 bug —— 那张 JORC 表本来就同时列了钽和锂，只是 ground truth
    只对钽做了人工核对。测试**显式固定这个事实**，这样将来有人补全
    ground truth 时，会看到这条断言并意识到需要同步更新，而不是默默漂移。
    """
    extra = results["pilgangoora-mre-2015-historical"]["extra"]
    commodities = sorted(e["commodity"] for e in extra)
    assert commodities == ["lithium", "lithium"]
    assert all(e["confidence"] == 1.0 for e in extra)
