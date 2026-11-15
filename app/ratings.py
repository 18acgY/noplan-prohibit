"""评级的中文展示层。

TradingAgents 输出的五档评级是机构里的“仓位语言”，直接摆一个
Underweight 在页面上，用户看不出它对应什么动作。这里只做翻译和解释，
不改变分析结果，也不构成投资建议。
"""

from __future__ import annotations

from typing import Any

RATINGS: dict[str, dict[str, str]] = {
    "Buy": {
        "zh": "买入",
        "action": "建议建仓或加仓",
        "detail": "模型明确看好，可建立新仓或增加仓位",
        "tone": "ok",
    },
    "Overweight": {
        "zh": "加仓（超配）",
        "action": "建议逐步增加仓位",
        "detail": "模型偏看好，分批加仓，不是一次性满仓",
        "tone": "ok",
    },
    "Hold": {
        "zh": "持有（观望）",
        "action": "维持现有仓位，不加不减",
        "detail": "证据均衡或方向不明；持有不等于看好",
        "tone": "warn",
    },
    "Underweight": {
        "zh": "减仓（低配）",
        "action": "建议降低仓位、部分止盈",
        "detail": "模型偏谨慎，把仓位减到标准仓位以下；不等于清仓",
        "tone": "bad",
    },
    "Sell": {
        "zh": "卖出（清仓）",
        "action": "建议清仓或不建仓",
        "detail": "模型看空，建议退出仓位或避免新建",
        "tone": "bad",
    },
}

# REPORT/REVIEW 之类的兜底：模型没给出可读评级时，不硬翻译。
UNKNOWN = {
    "zh": "未给出明确评级",
    "action": "先看完整报告，不要按这条卡片下单",
    "detail": "报告里没有可读的评级，可能是格式异常",
    "tone": "muted",
}


def info(rating: Any) -> dict[str, str]:
    """Return the display entry for a rating, tolerant of unknown values."""
    if not rating:
        return UNKNOWN
    return RATINGS.get(str(rating).strip(), {
        "zh": str(rating),
        "action": "模型给出了未收录的评级",
        "detail": "以完整报告为准",
        "tone": "muted",
    })


def legend() -> list[tuple[str, dict[str, str]]]:
    """Five-tier legend in strongest-to-weakest order."""
    order = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
    return [(key, RATINGS[key]) for key in order]