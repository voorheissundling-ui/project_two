"""DeepSeek 客户端（走 OpenAI 兼容协议）+ 无 key 时的确定性兜底。

设计取舍：**LLM 是增强，不是依赖**。

一份日报如果因为没配 API key 就完全跑不出来，那它没资格叫「工程」。
所以这里把 LLM 的能力边界划清楚：

  * LLM 负责 **query 规划**（把「Pilbara 锂矿」拆成检索词/品种/报告）和
    **叙述生成**（把结构化数据写成中文简报）；
  * **取数、算统计、判趋势、挑风险**全部由确定性代码完成 ——
    这些是硬事实，不该由模型即兴发挥。

没配 key 时 `LLMUnavailable` 会被上层捕获，改用模板生成简报：
数字一样准，只是文风更机械。这样 `RUN.md` 里「5 分钟跑通」才成立。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from mining_daily.config import settings


class LLMUnavailable(RuntimeError):
    """没有可用凭据（或显式选离线）。上层应据此走模板兜底，而不是报错退出。"""


@dataclass
class LLMResult:
    text: str
    model: str
    used_live: bool


class ChatLLM:
    """极简 Chat 封装：只要「一次 system+user，返回文本」这一种用法。

    不做 function calling —— 见模块头注释，工具编排是确定性的，
    让模型去「决定调哪个工具」只会引入不确定性而没有收益。
    """

    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self._client = None

    @property
    def available(self) -> bool:
        if self.provider == "offline":
            return False
        if self.provider == "deepseek":
            return bool(settings.deepseek_api_key)
        if self.provider == "openai":
            return bool(settings.openai_api_key)
        return False

    def _ensure_client(self):
        if self._client is not None:
            return self._client

        from openai import OpenAI

        if self.provider == "deepseek":
            self._client = OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=settings.request_timeout * 3,
            )
            self.model = settings.deepseek_model
        elif self.provider == "openai":
            self._client = OpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout=settings.request_timeout * 3,
            )
            self.model = settings.openai_model
        else:
            raise LLMUnavailable(f"provider={self.provider} 不支持真实调用")
        return self._client

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult:
        if not self.available:
            raise LLMUnavailable(
                "未配置 API key（或 llm_provider=offline）。"
                "设置 MINING_DAILY_DEEPSEEK_API_KEY 后即可启用真实生成。"
            )

        client = self._ensure_client()
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = client.chat.completions.create(**kwargs)
        return LLMResult(text=resp.choices[0].message.content or "", model=self.model, used_live=True)


def extract_json(text: str) -> Optional[dict]:
    """从模型输出里抠 JSON。

    模型即便在 json_mode 下也常给 ```json 围栏，或者前后带一句解释。
    直接 json.loads 会炸，所以退一步用第一个 `{` 到最后一个 `}` 兜住。
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
