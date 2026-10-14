"""表格解析的单元测试。

**这些用例不是凭空想出来的，每一个都对应一次真实的调试事故。**
PDF 表格解析最容易犯的错是「假设单位只在括号里、并且长得像 Mt」，
而真实报告的排版恰恰在这两点上花样百出。所以这些断言锁的是**当时的踩坑点**，
删掉任何一条都意味着那个 bug 有可能回归。
"""

from __future__ import annotations

import pytest

from mining_daily.common import tableparse as tp


class TestClassifyColumn:
    def test_g_per_tonne_is_grade_not_tonnage(self):
        """`Grade (g/tonne)` 必须判成 grade。

        这是最经典的一个坑：`tonnes?\\b` 正则会匹配到 `g/tonne` 里的 "tonne"，
        于是品位列被当成吨位列，整张表的数字全部错位。
        修法是**单位括号优先于关键词**：括号里是 `g/tonne`，先按单位判掉。
        """
        kind, unit, _metal = tp.classify_column("Grade (g/tonne)")
        assert kind == "grade", "g/tonne 被误判成 tonnage —— 关键词匹配又抢在单位前面了"
        assert unit == "g/tonne"

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Tonnes (Mt)", "tonnage"),
            ("Tonnage (Mt)", "tonnage"),
            ("Ore Tonnes (kt)", "tonnage"),
            ("Contained Metal (Moz)", "contained"),
            ("Contained Gold (koz)", "contained"),
            ("Grade (g/t)", "grade"),
            ("Grade (ppm)", "grade"),
            ("Grade (% Li2O)", "grade"),
        ],
    )
    def test_column_kinds(self, name, expected):
        assert tp.classify_column(name)[0] == expected

    @pytest.mark.parametrize(
        "name",
        ["Share (%)", "Recovery (%)", "JORC Category", "Deposit", "District"],
    )
    def test_non_quantitative_columns_are_ignored(self, name):
        """Share/Recovery 这类列必须整列忽略。

        它们也是数字列，如果不排除，列位分配时会把 `100%` 这类值
        抢到吨位/品位列上（数字中心最近的那个列会赢），导致整行读错。
        """
        assert tp.classify_column(name)[0] == "ignore"


class TestExpandCells:
    def test_keyword_run_is_split(self):
        """`Tonnes Grade Contained` 要拆成三个单元格。

        三个列名之间**只有一个空格**，而粗切只认「两个以上空格」（那是列间距），
        于是整个表头被并成一格，列名成了 `Tonnes Grade Contained (Mt)` ——
        三列都含 "Contained"，全被判成金属量列。
        修法是识别「整格由关键词构成」时按关键词再切一刀。
        """
        cells = tp.expand_cells("Tonnes Grade Contained  (Mt)  (g/t)  (Moz)")
        texts = [c.text for c in cells]
        assert texts[:3] == ["Tonnes", "Grade", "Contained"], texts
        assert texts[3:] == ["(Mt)", "(g/t)", "(Moz)"], texts

    def test_cell_with_unit_paren_is_not_split(self):
        """`Contained Li2O (t)` 不能被拆。

        它虽然也含关键词，但**整格并不都由关键词组成**（还有单位括号），
        拆开会把列名和单位拆散。_split_keyword_run 的守卫条件就是这条。
        """
        cells = tp.expand_cells("Contained Li2O (t)")
        assert [c.text for c in cells] == ["Contained Li2O (t)"]

    def test_normal_multi_space_line_unchanged(self):
        cells = tp.expand_cells("Indicated    4,800    1.01    150")
        assert [c.text for c in cells] == ["Indicated", "4,800", "1.01", "150"]


class TestHeaderScanning:
    def test_all_caps_group_header_is_kept(self):
        """Barrick 那个全大写的分组表头不能被当成水印丢掉。

        水印的启发式规则是「整行大写且长度 > 40」，但 Barrick 的分组行
        `MEASURED (M)    INDICATED (I)    (M)+(I)    INFERRED` 同样全大写、
        长度也超 40。区分点是**水印行没有连续空格**（是排版时硬塞进去的一整串），
        而表头行靠列间距对齐、必然含两个以上连续空格。
        误杀这一行会让上下两层的分组信息全部丢失，只剩一根光秃秃的单位行。
        """
        line = "MEASURED (M)    INDICATED (I)    (M)+(I)    INFERRED"
        assert tp._is_header_like(line), "分组表头被当成水印丢掉了"

    def test_watermark_is_rejected(self):
        line = "THIS DOCUMENT IS UNCLASSIFIED AND PROPRIETARY TO THE COMPANY"
        assert not tp._is_header_like(line)

    def test_detect_category(self):
        assert tp.detect_category("Indicated") == "Indicated"
        assert tp.detect_category("Inferred Resource") == "Inferred"
        assert tp.detect_category("Total Gold") == "Total"
        assert tp.detect_category("Pilbara Minerals Limited") is None


class TestDataRowDetection:
    @pytest.mark.parametrize(
        "line",
        [
            "Indicated          4,800     1.01      150",
            "Inferred            1,400     0.90       41",
        ],
    )
    def test_data_rows_accepted(self, line):
        assert tp.is_data_row(line)

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "Notes: 1. Mineral resources are not mineral reserves",
            "Altura Mining Limited, February 2016 (0.4% Li2O cut-off)",
            "Indicated",  # 只有一个词，没有数字
        ],
    )
    def test_non_data_rows_rejected(self, line):
        assert not tp.is_data_row(line), f"不该被当成数据行：{line!r}"
