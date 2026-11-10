"""分析阶段模型：阶段顺序、标签，以及 agent → 阶段的映射。

同一份阶段表也用于前端步骤条。launcher 侧另有一份 agent 映射
（deploy/tradingagents/progress_hook.py，跑在 TradingAgents 的 venv 里，
不能 import 本包），测试会校验两边的阶段键完全一致。
"""

from __future__ import annotations

import json
from typing import Any

STAGES: list[tuple[str, str]] = [
    ("prepare", "准备"),
    ("analysts", "分析师团队"),
    ("research", "研究辩论"),
    ("trader", "交易计划"),
    ("risk", "风控辩论"),
    ("portfolio", "组合决策"),
    ("report", "整理报告"),
]

STAGE_LABELS = dict(STAGES)
STAGE_KEYS = [key for key, _ in STAGES]
STAGE_INDEX = {key: index for index, key in enumerate(STAGE_KEYS)}

# 分析师并行跑，其余按顺序推进。
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

AGENT_LABELS = {
    "market": "行情",
    "news": "新闻",
    "fundamentals": "基本面",
    "sentiment": "情绪",
    "bull": "多头",
    "bear": "空头",
    "research_manager": "研究主管",
    "trader": "交易员",
    "aggressive": "激进",
    "neutral": "中性",
    "conservative": "保守",
    "portfolio_manager": "组合经理",
}

ANALYST_KEYS = ["market", "news", "fundamentals", "sentiment"]

VALID_STATES = ("pending", "in_progress", "completed")


def stage_index(stage: str | None) -> int | None:
    if not stage:
        return None
    return STAGE_INDEX.get(stage)


def advance(current: str | None, new: str | None) -> str | None:
    """阶段只前进不后退。"""
    if not new:
        return current
    if stage_index(new) is None:
        return current
    if stage_index(current) is None:
        return new
    return new if STAGE_INDEX[new] > STAGE_INDEX[current] else current


def parse_detail(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def analyst_states(detail: dict[str, str]) -> list[tuple[str, str, str]]:
    """返回 (key, 标签, 状态)，只包含已选中的分析师。"""
    return [
        (key, AGENT_LABELS[key], detail[key])
        for key in ANALYST_KEYS
        if key in detail
    ]