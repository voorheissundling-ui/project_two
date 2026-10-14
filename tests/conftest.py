"""pytest 公共配置。

把 `src/` 和 `scripts/` 挂上 `sys.path`：
  * `src/`      —— 让 `import mining_daily` 在没装包时也能用（pytest 直接跑的兜底）
  * `scripts/`  —— 让测试能复用 `check_extraction.py` 里的对比逻辑，
                   而不是把「怎么算对」这件事在脚本和测试里各写一遍
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

for extra in (REPO / "src", REPO / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
