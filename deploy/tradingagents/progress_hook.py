"""把 TradingAgents CLI 的 agent 状态写成 JSONL，供 worker 转发到网站。

这个文件跑在 TradingAgents 的 venv 里，不能 import 应用代码，所以
agent → 阶段 的映射在这里另存一份；cache/tests/test_progress_hook.py 会
校验它与 app/progress.py 的阶段表一致。

为什么这样挂钩：TA 的进度只存在于进程内部（rich Live 全屏刷新，重定向到
文件后看不到阶段），但 CLI 每次状态变化都会调
``message_buffer.update_agent_status(...)``，我们在同进程里包一层即可。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 阶段键必须与 app/progress.py 的 STAGES 一致（prepare / report 由两端各自发）。
STAGE_BY_AGENT = {
    "Market Analyst": "analysts",
    "News Analyst": "analysts",
    "Fundamentals Analyst": "analysts",
    "Sentiment Analyst": "analysts",
    "Bull Researcher": "research",
    "Bear Researcher": "research",
    "Research Manager": "research",
    "Trader": "trader",
    "Aggressive Analyst": "risk",
    "Neutral Analyst": "risk",
    "Conservative Analyst": "risk",
    "Portfolio Manager": "portfolio",
}

AGENT_KEYS = {
    "Market Analyst": "market",
    "News Analyst": "news",
    "Fundamentals Analyst": "fundamentals",
    "Sentiment Analyst": "sentiment",
    "Bull Researcher": "bull",
    "Bear Researcher": "bear",
    "Research Manager": "research_manager",
    "Trader": "trader",
    "Aggressive Analyst": "aggressive",
    "Neutral Analyst": "neutral",
    "Conservative Analyst": "conservative",
    "Portfolio Manager": "portfolio_manager",
}


def _write(path: Path, event: dict[str, Any]) -> None:
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()


def emit_stage(path: str | os.PathLike[str] | None, stage: str) -> None:
    """写一条阶段事件；进度失败绝不能影响分析本身。"""
    if not path:
        return
    try:
        _write(Path(path), {"stage": stage})
    except OSError:
        pass


def emit_agent(path: str | os.PathLike[str] | None, agent: str, state: str) -> None:
    if not path or agent not in STAGE_BY_AGENT:
        return
    try:
        _write(
            Path(path),
            {"stage": STAGE_BY_AGENT[agent], "agent": agent, "state": state},
        )
    except OSError:
        pass


def install(message_buffer: Any, path: str | os.PathLike[str] | None) -> None:
    """包一层 message_buffer.update_agent_status；path 为 None 时什么都不做。"""
    if not path:
        return
    original = message_buffer.update_agent_status

    def update_agent_status(agent: str, state: str) -> None:
        original(agent, state)
        emit_agent(path, agent, state)

    message_buffer.update_agent_status = update_agent_status