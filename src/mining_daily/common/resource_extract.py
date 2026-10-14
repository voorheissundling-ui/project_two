"""把 PDF 文本层解析成结构化的资源量行（ResourceRow）。

这一层负责三件 tableparse 不管的事：
  1. **数字归位** —— 用「数字中心点最接近哪个列中心点」来判定，
     而不是用字符区间包含关系。原因很实在：表头分组标签的左右留白和
     数据行的数字留白**并不严格对齐**（Barrick 的分组标签从第 20 列开始，
     而数字从第 18 列开始），用包含判定会把 "450" 从头切掉。
  2. **单位归一** —— `000 tonnes` / `kt` / `Mt` / `t` 全部换算到 Mt；
     oz 族换算到 Moz，其余金属量换算到 t。原始值一律保留，人工要对账。
  3. **置信度与弃权** —— 每个必需字段都有一个打分，凑不齐就降置信度；
     整体置信度低于阈值时返回 abstain，而不是硬着头皮给一个错的数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from mining_daily.common import tableparse as tp

LB_TO_T = 453.59237  # 1 磅 = 453.59237 克

# 单位 -> (到 Mt 的换算系数, 显示单位)
TONNAGE_UNITS: dict[str, tuple[float, str]] = {
    "mt": (1.0, "Mt"),
    "kt": (0.001, "kt"),
    "000 tonnes": (0.001, "kt"),
    "000 t": (0.001, "kt"),
    "t": (1e-6, "t"),
    "tonnes": (1e-6, "t"),
}

GRADE_UNIT_CANON = {
    "g/tonne": "g/t",
    "g/t": "g/t",
    "ppm": "ppm",
    "%": "%",
}


@dataclass
class RawNote:
    """解析过程中的一条告警，最终会塞进 ExtractionInfo.warnings。"""

    level: str  # info | warn
    message: str


def _canon_grade_unit(raw_unit: str, metal: Optional[str], column_name: str = "") -> str:
    """把品位单位写成「数值 + 基准」的形式，例如 `% Li2O`、`g/t Au`。

    基准不能省：`1.36%` 到底是 Li2O 品位还是 Li 品位，含义差一倍多，
    丢掉基准的品位数字人工没法对账。
    """
    key = (raw_unit or "").strip().lower()
    base = GRADE_UNIT_CANON.get(key) or raw_unit.strip() or "%"
    basis = tp.grade_basis(column_name)
    if basis:
        return f"{base} {basis}"
    return f"{base} {metal.capitalize()}" if metal else base


def normalize_tonnage(value: float, unit: str) -> tuple[float, str]:
    key = (unit or "").strip().lower()
    factor, canon = TONNAGE_UNITS.get(key, (1.0, unit or "Mt"))
    return round(value * factor, 6), "Mt"


def normalize_contained(value: float, unit: str) -> tuple[float, str]:
    """金属量归一：oz 族 -> Moz；质量族 -> t。"""
    key = (unit or "").strip().lower()
    if "oz" in key or key in {"000", "000 oz"}:
        if key in {"000", "000 oz", "koz"}:
            return round(value / 1000.0, 6), "Moz"
        if key == "oz" or key == "000 ounces":
            return round(value / 1e6, 6), "Moz"
        return round(value, 6), "Moz"
    if key == "mlb":
        return round(value * LB_TO_T, 4), "t"
    if key == "mt":
        return round(value * 1e6, 4), "t"
    if key == "kt":
        return round(value * 1e3, 4), "t"
    # t / tonnes / 其它按吨处理
    return round(value, 4), "t"


def _display_contained_unit(value: float, unit: str) -> str:
    key = (unit or "").strip().lower()
    if "oz" in key or key in {"000", "000 oz"}:
        if key in {"000", "000 oz", "koz"}:
            return "koz"
        if key == "oz":
            return "oz"
        return "Moz"
    if key == "mlb":
        return "Mlb"
    if key == "mt":
        return "Mt"
    if key == "kt":
        return "kt"
    return "t"


def number_spans(line: str) -> list[tuple[int, int, str]]:
    """取括号外的数字及其字符区间。"""
    masked = list(line)
    for m in re.finditer(r"\([^()]*\)", line):
        for i in range(m.start(), m.end()):
            masked[i] = " "
    masked_line = "".join(masked)
    return [(m.start(), m.end(), m.group(1)) for m in tp.NUM_RE.finditer(masked_line)]


def _center(start: int, end: int) -> float:
    return (start + end) / 2.0


def _assign(
    numbers: list[tuple[int, int, str]], columns: list[tp.Column]
) -> dict[int, str]:
    """把每个数字分配给「中心点最近」的列，返回 {列下标: 数字}。

    两个细节，都是踩出来的：

    * **用最近中心，不用区间包含**。表头单元格与数据单元格的留白规则并不一致
      （Barrick 分组标签从第 20 列起，数字从第 18 列起），包含判定会把 "450" 切掉。
    * **ignore 列也要参与分配**。Newmont 的 `Newmont Share` 列虽然不用，
      但它占着 `100%` 这个数字 —— 若不参与分配，`100%` 会被吸到右边的吨位列，
      吨位就变成了 100。
    """
    out: dict[int, str] = {}
    if not columns:
        return out
    centers = [c.center for c in columns]
    for start, end, text in numbers:
        num_center = _center(start, end)
        idx = min(range(len(columns)), key=lambda i: abs(centers[i] - num_center))
        # 距离护栏：隔得太远的数字宁可不认，也不要错配到别的列
        if abs(centers[idx] - num_center) > 22:
            continue
        out.setdefault(idx, text)
    return out


def _f(token: str) -> Optional[float]:
    try:
        return float(token.replace(",", ""))
    except (ValueError, AttributeError):
        return None


@dataclass
class ExtractedRow:
    category: str
    commodity: Optional[str]
    tonnage_value: float
    tonnage_unit: str
    tonnage_mt: float
    grade_value: float
    grade_unit: str
    contained_value: float
    contained_unit: str
    contained_normalized: float
    contained_normalized_unit: str
    source_line: str
    confidence: float
    notes: list[str]


TOTAL_LABEL_RE = re.compile(r"^\s*total\b", re.IGNORECASE)

# 只有这两类才计分/进简报：Measured 与经济性无关，(M)+(I)、Total 是派生行。
CATEGORY_KEEP = ("Indicated", "Inferred")


def _rows_for_table(spec: tp.TableSpec, data_lines: list[str]) -> list[str]:
    """同一张表里既有逐矿体明细又有合计行时，**只取合计行**。

    理由很实在：给日报 Agent 用的资源量是「公司/项目口径」的总量。
    把逐矿体明细也交上去，同一个矿区的资源会被重复计入，
    简报里的「总资源量」会凭空翻倍。

    但**必须区分两种表**，否则会误伤：

      * Barrick 的行是矿体名（`Carlin surface` / `Total Gold`）—— 该收拢；
      * JORC 表的行本身就是类别（`Indicated` / `Inferred` / `Total`）——
        收拢就把用户要的类别明细丢了。这时 Total 行本来就会被类别过滤掉，
        不需要也不应该收拢。
    """
    has_category_row = any(
        tp.detect_category(tp.split_cells(ln)[0].text) in CATEGORY_KEEP
        for ln in data_lines
        if tp.split_cells(ln)
    )
    if has_category_row:
        return data_lines

    totals = [ln for ln in data_lines if TOTAL_LABEL_RE.match(ln)]
    return totals or data_lines


def extract_rows(text: str) -> tuple[list[ExtractedRow], list[RawNote], dict]:
    """解析全文，返回 (行, 告警, 诊断信息)。只输出 Indicated / Inferred 两类。"""
    lines = text.splitlines()
    notes: list[RawNote] = []
    out: list[ExtractedRow] = []
    diagnostics = {"tables_found": 0, "rows_seen": 0, "rows_kept": 0}

    blocks = tp.find_table_blocks(lines)
    diagnostics["tables_found"] = len(blocks)

    for header_lines, data_lines in blocks:
        spec = tp.build_table_spec(header_lines)
        for w in spec.warnings:
            notes.append(RawNote("warn", w))

        # 所有列（含 ignore）参与数字分配：ignore 列也占着数字位置，
        # 不让它参与的话，它旁边的数字会被吸进有效列里（见 _assign 的注释）
        all_columns = spec.all_columns
        if not all_columns:
            continue

        selected = _rows_for_table(spec, data_lines)
        if len(selected) < len(data_lines):
            notes.append(
                RawNote(
                    "info",
                    f"《{spec.title}》按合计口径取数（total 行），避免逐矿体明细重复计数",
                )
            )

        # 每个列下标属于哪个分组、什么角色，先算好
        col_group: dict[int, Optional[str]] = {}
        idx = 0
        for canon, cols in spec.groups:
            for _c in cols:
                col_group[idx] = canon
                idx += 1

        for line in selected:
            diagnostics["rows_seen"] += 1
            cells = tp.split_cells(line)
            if not cells:
                continue
            label = cells[0].text
            numbers = number_spans(line)
            if not numbers:
                continue

            mapped = _assign(numbers, all_columns)
            if not mapped:
                continue

            for group_name, columns in spec.groups:
                gidx = [i for i, c in enumerate(all_columns) if col_group.get(i) is group_name]
                # 分组表：类别来自分组表头；行列式表：类别来自行标签
                category = group_name if group_name else tp.detect_category(label)
                if category not in ("Indicated", "Inferred"):
                    continue

                by_role: dict[str, list[tuple[int, tp.Column]]] = {}
                for i in gidx:
                    col = all_columns[i]
                    if col.role != "ignore":
                        by_role.setdefault(col.role, []).append((i, col))

                tonn = (by_role.get("tonnage") or [None])[0]
                if tonn is None or tonn[0] not in mapped:
                    continue
                tonnage_raw = _f(mapped[tonn[0]])
                if tonnage_raw is None:
                    continue

                grade_cols = by_role.get("grade", [])
                cont_cols = by_role.get("contained", [])

                # 一行多金属（Pilbara 的 Li2O + Ta2O5）时每个金属各出一行
                row_metal = tp.metal_from_text(label)
                metals = [c.metal for _i, c in grade_cols] or [row_metal or spec.commodity]

                for metal in metals:
                    gi = next(
                        ((i, c) for i, c in grade_cols if c.metal == metal),
                        grade_cols[0] if grade_cols else None,
                    )
                    if gi is None or gi[0] not in mapped:
                        continue
                    grade_raw = _f(mapped[gi[0]])
                    if grade_raw is None:
                        continue

                    ci = next(
                        ((i, c) for i, c in cont_cols if c.metal == metal),
                        cont_cols[0] if len(cont_cols) == 1 else None,
                    )
                    cont_raw = _f(mapped[ci[0]]) if ci and ci[0] in mapped else None

                    row_notes: list[str] = []
                    confidence = 1.0

                    ton_mt, _ = normalize_tonnage(tonnage_raw, tonn[1].unit)
                    commodity = metal or row_metal or spec.commodity
                    grade_unit = _canon_grade_unit(gi[1].unit, commodity, gi[1].name)

                    if cont_raw is None:
                        confidence -= 0.35
                        row_notes.append("未能定位金属量列，该字段留空")
                        cont_norm, cont_norm_unit = 0.0, ""
                        cont_unit_display = ""
                    else:
                        cont_norm, cont_norm_unit = normalize_contained(cont_raw, ci[1].unit)
                        cont_unit_display = _display_contained_unit(cont_raw, ci[1].unit)

                    if not tonn[1].unit:
                        confidence -= 0.15
                        row_notes.append("吨位列没有单位，按 Mt 处理，需人工确认")

                    # 并排表里行标签往往写的是矿种（`Total Gold`）而不是类别，
                    # 只有行标签**真的写了另一个类别**时才降置信度。
                    row_cat = tp.detect_category(label)
                    if group_name and row_cat in ("Measured", "Indicated", "Inferred") and row_cat != group_name:
                        confidence -= 0.3
                        row_notes.append(f"行标题({row_cat})与分组表头({group_name})不一致")

                    out.append(
                        ExtractedRow(
                            category=category,
                            commodity=commodity,
                            tonnage_value=tonnage_raw,
                            tonnage_unit=tonn[1].unit or "Mt",
                            tonnage_mt=ton_mt,
                            grade_value=grade_raw,
                            grade_unit=grade_unit,
                            contained_value=cont_raw or 0.0,
                            contained_unit=cont_unit_display,
                            contained_normalized=cont_norm,
                            contained_normalized_unit=cont_norm_unit,
                            source_line=line.strip(),
                            confidence=round(max(confidence, 0.0), 2),
                            notes=row_notes,
                        )
                    )
                    diagnostics["rows_kept"] += 1

    if diagnostics["tables_found"] == 0:
        notes.append(RawNote("warn", "未在文本层中识别到任何资源量表格"))
    if diagnostics["rows_kept"] == 0:
        notes.append(
            RawNote("warn", "识别到表格但没有解析出 Indicated / Inferred 行，可能需要人工核对版式")
        )

    # 去重：同一行被多张表重复解析时会重复计数
    seen = set()
    deduped = []
    for row in out:
        key = (row.category, row.commodity, row.tonnage_mt, row.grade_value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return deduped, notes, diagnostics


def detect_scanned(text: str) -> bool:
    """文本层过短 = 大概率是扫描件。本项目**不接 OCR**，此时必须明说而不是硬猜。"""
    return len(text.strip()) < 200
