"""全局配置。

数据模式的开关设计成三档，但**当前只有 ``fixture`` 真正可用**：

- ``fixture``：只读 ``data/`` 下的本地样本，**零联网**。默认值，拉下来就能跑。
- ``live``   ：只走真实外网源（RSS / 行情接口 / HTTP 下载 PDF）。
- ``hybrid`` ：先试真实源，失败/超时自动回落 fixture，并在返回体的 ``data_origin`` 里标注。

后两档是**接口预留**：`data_origin` 字段、对真实 URL 的解析路径、三个 server 的
取数收口都已经铺好了，但真实抓取函数还没写（原因见 DATA_NOTES.md「已知局限」）。

所以这里有一条**硬校验**：设置成 ``live``/``hybrid`` 会**直接报错启动失败**，
而不是静默地继续读 fixture。理由很简单 ——
如果能跑，server 就会把 fixture 数据标成 ``data_origin="live"``，
**一个标错来源的数据比一个明确报错的服务危险得多**：
下游（包括 LLM）会据此认为这是实时行情，而它其实是我们自己插值造出来的。

失败要快，而且要说得清楚。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/mining_daily/config.py -> src/mining_daily -> src -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[2]

DataMode = Literal["fixture", "live", "hybrid"]

UNIMPLEMENTED_MODES = ("live", "hybrid")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINING_DAILY_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 数据模式 ----------
    mode: DataMode = "fixture"
    data_dir: Path = REPO_ROOT / "data"
    cache_dir: Path = REPO_ROOT / ".cache"
    timezone: str = "Asia/Shanghai"

    # ---------- 网络 ----------
    request_timeout: float = 20.0
    max_retries: int = 3
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 mining-daily/0.1"
    )

    # ---------- LLM（Agent client 用）----------
    llm_provider: Literal["deepseek", "openai", "offline"] = "deepseek"
    llm_model: Optional[str] = None
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096

    deepseek_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("MINING_DAILY_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    )
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    openai_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("MINING_DAILY_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("MINING_DAILY_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )
    openai_model: str = "gpt-4o-mini"

    # ---------- MCP 进程 ----------
    mcp_startup_timeout: float = 30.0
    mcp_tool_timeout: float = 60.0
    # server 名 -> 启动命令。默认用当前解释器直接起模块，避免依赖 PATH 里的 uv。
    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"

    @model_validator(mode="after")
    def _reject_unimplemented_modes(self) -> "Settings":
        """live/hybrid 会启动失败 —— 详见模块头注释，这不是 bug 是刻意的。"""
        if self.mode in UNIMPLEMENTED_MODES:
            raise ValueError(
                f"MINING_DAILY_MODE={self.mode} 尚未实现：真实抓取代码还没写，"
                "继续跑下去只会把 fixture 数据标成 data_origin='live'，"
                "比直接报错危险得多。\n"
                "请用 MINING_DAILY_MODE=fixture（默认），"
                "或在 DATA_NOTES.md「已知局限」里了解当前边界。"
            )
        return self

    @property
    def news_dir(self) -> Path:
        return self.data_dir / "news"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def prices_dir(self) -> Path:
        return self.data_dir / "prices"


settings = Settings()
