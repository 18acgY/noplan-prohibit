"""预审逻辑：严格照搬《次日交易许可单》A38 公式的五段判断。

公式来源（xlsx 单元格）：
  B17 总分 = SUM(B16,D16,F16,H16)
  H18 上涨空间 = F20/B20-1
  D21 止损距离 = ABS(B20-D20)/ABS(B20)
  F21 风险 = ABS(B20-D20)*H20
  H21 盈亏比 = (F20-B20)/(B20-D20)
  H22 预算检查 = IF(F22<=D22,"预算内","超预算")
  A38 预审 = 日期闸门 → 资料完整 → 纪律红灯 → 买入参数门槛 → 评分<60 → 通过
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

PASS = "pass"
NO_HEAVY = "no_heavy"
PARAM_FAIL = "param_fail"
DISCIPLINE_RED = "discipline_red"
INCOMPLETE = "incomplete"
DATE_INVALID = "date_invalid"

BUY_ACTIONS = ("开仓", "加仓")

MSG_DATE = "未提前一个交易日填写/日期不合规：禁止交易"
MSG_INCOMPLETE = "资料不完整：禁止交易"
MSG_DISCIPLINE = "纪律红灯：禁止交易"
MSG_PARAM = "买入/加仓参数未达个人门槛：禁止交易"
MSG_NO_HEAVY = "预审通过但禁止重仓：仅轻仓，严格按单执行"
MSG_PASS = "预审通过：仅按单执行"

EMOTION_LABELS = {
    "emo_1": "是否因踏空/眼红/恐惧/想回本？",
    "emo_2": "是否只凭资金流/传闻/他人推荐？",
    "emo_3": "是否临时起意、思考不足？",
    "emo_4": "是否没有退出规则？",
    "emo_5": "是否准备盘中追价/改计划？",
    "emo_6": "是否超出资金/仓位/风险预算？",
    "emo_7": "是否未核实重要公告/环境？",
    "emo_8": "买入理由是否经不起反方检验？",
}

# 顺序与 A38 公式的必填清单一致
REQUIRED_FIELDS: list[tuple[str, str]] = [
    ("action", "计划动作"),
    ("code", "证券代码"),
    ("name", "证券名称"),
    ("market", "市场/板块"),
    ("core_logic", "核心逻辑（一句话说明）"),
    ("evidence", "客观证据与数据"),
    ("counter_argument", "反方观点"),
    ("score_market", "大盘评分"),
    ("score_sector", "板块评分"),
    ("score_stock", "个股评分"),
    ("score_corr", "组合相关性评分"),
    ("buy_price", "买入价格"),
    ("stop_price", "启动低点（止损点）"),
    ("qty", "计划数量"),
    ("trigger_cond", "触发条件"),
    ("cancel", "取消条件"),
    ("exit_plan", "预设退出条件"),
    *[(k, v) for k, v in EMOTION_LABELS.items()],
    ("admit_signal", "承认错误的客观信号"),
]

NUMERIC_FIELDS = (
    "score_market",
    "score_sector",
    "score_stock",
    "score_corr",
    "buy_price",
    "stop_price",
    "target_price",
    "qty",
    "account_total",
    "risk_cap",
)


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def num(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    if _is_blank(value):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def compute(data: Mapping[str, Any]) -> dict[str, Any]:
    """返回表单上要展示的全部派生值（与 Excel 公式一一对应）。"""
    buy = num(data, "buy_price")
    stop = num(data, "stop_price")
    target = num(data, "target_price")
    qty = num(data, "qty")
    risk_cap = num(data, "risk_cap")

    total_score = sum(num(data, k) or 0.0 for k in ("score_market", "score_sector", "score_stock", "score_corr"))

    trade_amount = buy * qty if buy is not None and qty is not None else None
    stop_distance = abs(buy - stop) / abs(buy) if buy not in (None, 0) and stop is not None else None
    risk_amount = abs(buy - stop) * qty if buy is not None and stop is not None and qty is not None else None
    upside = target / buy - 1 if buy not in (None, 0) and target is not None else None
    reward_risk = (
        (target - buy) / (buy - stop)
        if buy is not None and stop is not None and target is not None and buy != stop
        else None
    )
    budget_ok = risk_amount <= risk_cap if risk_amount is not None and risk_cap is not None else None

    return {
        "total_score": total_score,
        "trade_amount": trade_amount,
        "stop_distance": stop_distance,
        "risk_amount": risk_amount,
        "planned_risk": risk_amount,
        "upside": upside,
        "reward_risk": reward_risk,
        "budget_ok": budget_ok,
    }


@dataclass(frozen=True)
class Thresholds:
    upside_min: float = 0.15
    reward_risk_min: float = 2.0
    score_min: float = 60.0
    stop_distance_min: float | None = None
    stop_distance_max: float | None = None


@dataclass
class ReviewResult:
    status: str
    message: str
    reasons: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    derived: dict[str, Any] = field(default_factory=dict)


def _fmt_money(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def review(
    data: Mapping[str, Any],
    thresholds: Thresholds | None = None,
    *,
    date_gate: bool = False,
    prev_trading_day_confirmed: str | None = None,
) -> ReviewResult:
    t = thresholds or Thresholds()
    d = compute(data)

    if date_gate:
        fill_date = str(data.get("fill_date") or "").strip()
        plan_date = str(data.get("plan_date") or "").strip()
        bad = (
            not fill_date
            or not plan_date
            or fill_date >= plan_date
            or (prev_trading_day_confirmed or "").strip() != "是"
        )
        if bad:
            return ReviewResult(
                status=DATE_INVALID,
                message=MSG_DATE,
                reasons=["填单日期必须早于计划交易日，且前一交易日确认必须为“是”。"],
                derived=d,
            )

    missing = [label for key, label in REQUIRED_FIELDS if _is_blank(data.get(key))]
    if missing:
        return ReviewResult(
            status=INCOMPLETE,
            message=MSG_INCOMPLETE,
            reasons=["缺少：" + "、".join(missing)],
            missing_fields=missing,
            derived=d,
        )

    red = [label for key, label in EMOTION_LABELS.items() if str(data.get(key)).strip() == "是"]
    if red:
        return ReviewResult(
            status=DISCIPLINE_RED,
            message=MSG_DISCIPLINE,
            reasons=["情绪/纪律红灯：" + "、".join(red)],
            derived=d,
        )

    if str(data.get("action")).strip() in BUY_ACTIONS:
        problems: list[str] = []
        buy, stop = num(data, "buy_price"), num(data, "stop_price")
        target, qty = num(data, "target_price"), num(data, "qty")
        account, cap = num(data, "account_total"), num(data, "risk_cap")

        if target is None:
            problems.append("主压力位（目标价）未填写")
        if account is None:
            problems.append("账户总资产未填写")
        if cap is None:
            problems.append("单笔风险上限未填写")
        if buy is not None and stop is not None and stop >= buy:
            problems.append("启动低点（止损点）必须低于买入价")
        if buy is not None and target is not None and target <= buy:
            problems.append("主压力位必须高于买入价")
        if d["upside"] is not None and d["upside"] < t.upside_min:
            problems.append(f"上涨空间 {_fmt_pct(d['upside'])} 低于门槛 {_fmt_pct(t.upside_min)}")
        if d["reward_risk"] is not None and d["reward_risk"] <= t.reward_risk_min:
            problems.append(f"盈亏比 {d['reward_risk']:.2f} 必须大于 {t.reward_risk_min:g}")
        if d["reward_risk"] is None and buy is not None and stop is not None and buy == stop:
            problems.append("买入价与止损价相同，无法计算盈亏比")
        if d["budget_ok"] is False:
            problems.append(
                f"超预算：计划风险额 {_fmt_money(d['risk_amount'])} 元超过单笔风险上限 {_fmt_money(cap)} 元"
            )
        if problems:
            return ReviewResult(
                status=PARAM_FAIL,
                message=MSG_PARAM,
                reasons=problems,
                derived=d,
            )

    if d["total_score"] < t.score_min:
        return ReviewResult(
            status=NO_HEAVY,
            message=MSG_NO_HEAVY,
            reasons=[f"总分 {d['total_score']:g} 低于 {t.score_min:g} 分，仅允许轻仓。"],
            derived=d,
        )

    return ReviewResult(status=PASS, message=MSG_PASS, derived=d)