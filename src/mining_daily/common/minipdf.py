"""极简 PDF 生成器 —— 只用标准库，用来造 fixture 用的文本型 PDF。

为什么不用 fpdf2 / reportlab：
  1. 它们会拖进 Pillow 这种几十 MB 的轮子，而这个工程只需要「把等宽文本写进 PDF」；
  2. fixture 生成脚本要能在面试官机器上一条命令跑通，**依赖越少越好**；
  3. 可控性更强 —— 我们能精确决定文本层里字符的间距，从而模拟真实申报文件
     被 pypdf 抽取后的形态（列错位、前导空格保留）。

生成的是 PDF 1.4，使用标准 14 号字体里的 Courier（无需内嵌字体文件）。
正文用 `BT /F1 <size> Tf ... Td (text) Tj ET` 逐行输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field

A4_WIDTH = 595.28
A4_HEIGHT = 841.89

FONT_SIZE_DEFAULT = 8.0
LEADING_RATIO = 1.38  # 行距 / 字号。低于 ~1.2 时 pypdf 会把相邻两行判定成同一行。


def _leading(size: float) -> float:
    """行距。必须 > 字号的 1.2 倍，否则 PDF 文本层里两行会重叠，
    pypdf 抽取时会把它们粘成一行、列对齐全丢 —— 这是真实排版也会踩的坑。"""
    return max(size * LEADING_RATIO, 5.0)


def _escape(text: str) -> str:
    """PDF 字符串里的 \\ ( ) 必须转义；非 Latin-1 字符直接替换掉。

    我们刻意只输出 ASCII：真实申报文件（ASX/SEC）本来就是英文，
    而且标准 14 号字体不带中文，硬塞会出现乱码方块。
    """
    out = []
    for ch in text:
        if ch in "\\()":
            out.append("\\" + ch)
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


@dataclass
class SimplePdf:
    """把一串 (text, font_name, font_size) 行渲染成单页（或自动分页）PDF。"""

    title: str = "Document"
    font: str = "Courier"
    page_width: float = A4_WIDTH
    page_height: float = A4_HEIGHT
    margin_x: float = 34.0
    margin_top: float = 46.0
    margin_bottom: float = 40.0
    lines: list[tuple[str, str, float]] = field(default_factory=list)

    def add_line(self, text: str, *, bold: bool = False, size: float = FONT_SIZE_DEFAULT) -> None:
        self.lines.append((text, "F2" if bold else "F1", size))

    def add_blank(self, count: int = 1) -> None:
        for _ in range(count):
            self.lines.append(("", "F1", FONT_SIZE_DEFAULT))

    # -- 内部：把行按页切分 -------------------------------------------------
    def _paginate(self) -> list[list[tuple[str, str, float]]]:
        pages: list[list[tuple[str, str, float]]] = []
        current: list[tuple[str, str, float]] = []
        y = self.margin_top  # 从版心顶部往下累加
        for text, fname, size in self.lines:
            leading = _leading(size)
            if y + leading > self.page_height - self.margin_bottom:
                pages.append(current)
                current = []
                y = self.margin_top
            current.append((text, fname, size))
            y += leading
        pages.append(current)
        return pages

    def _content_stream(self, page_lines: list[tuple[str, str, float]]) -> str:
        parts = ["BT"]
        y = self.page_height - self.margin_top
        for text, fname, size in page_lines:
            leading = _leading(size)
            parts.append(f"/{fname} {size} Tf")
            parts.append(f"1 0 0 1 {self.margin_x:.2f} {y:.2f} Tm")
            parts.append(f"({_escape(text)}) Tj")
            y -= leading
        parts.append("ET")
        return "\n".join(parts)

    def render(self) -> bytes:
        pages = self._paginate()
        page_count = len(pages)

        # 对象编号：1=Catalog, 2=Pages, 3/4=Font, 5..=Page/Contents 交替
        objects: list[bytes] = []

        def add(obj: str) -> int:
            objects.append(obj.encode("latin-1", errors="replace"))
            return len(objects)

        # 预留 1、2 号
        objects.append(b"")
        objects.append(b"")

        font_regular = add(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{self.font} "
            f"/Encoding /WinAnsiEncoding >>"
        )
        font_bold = add(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{self.font}-Bold "
            f"/Encoding /WinAnsiEncoding >>"
        )

        page_obj_nums: list[int] = []
        for page_lines in pages:
            stream = self._content_stream(page_lines)
            stream_bytes = stream.encode("latin-1", errors="replace")
            content_num = add(
                f"<< /Length {len(stream_bytes)} >>\nstream\n{stream}\nendstream"
            )
            page_num = add(
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {self.page_width:.2f} {self.page_height:.2f}] "
                f"/Resources << /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R >> >> "
                f"/Contents {content_num} 0 R >>"
            )
            page_obj_nums.append(page_num)

        kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
        objects[1] = f"<< /Type /Pages /Count {page_count} /Kids [{kids}] >>".encode()
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

        # 组装
        buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0] * (len(objects) + 1)
        for i, obj in enumerate(objects, start=1):
            offsets[i] = len(buf)
            buf += f"{i} 0 obj\n".encode()
            buf += obj
            buf += b"\nendobj\n"

        xref_pos = len(buf)
        buf += f"xref\n0 {len(objects) + 1}\n".encode()
        buf += b"0000000000 65535 f \n"
        for i in range(1, len(objects) + 1):
            buf += f"{offsets[i]:010d} 00000 n \n".encode()
        buf += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R "
            f"/Info << /Title ({_escape(self.title)}) >> >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode()
        return bytes(buf)
