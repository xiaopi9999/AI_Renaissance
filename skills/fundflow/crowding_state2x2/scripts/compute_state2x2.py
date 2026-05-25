"""
资金拥挤度四象限状态诊断 — 可执行脚本

基于 SKILL.md 中定义的逻辑，读取资金组 parquet 数据，
对指定股票计算 Flow_zscore / CompositeCrowding_Pct / State2x2，
输出标准 Signal JSON。

用法:
    python compute_state2x2.py --stock 600519 [--date 20251231]

数据路径默认指向资金组数据协作目录，可通过 --data-dir 覆盖。
"""

import argparse
import json
import sys
import os
from datetime import datetime

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────
# 常量（与 SKILL.md 第4节对齐）
# ──────────────────────────────────────────────
CROWDING_HIGH = 0.70
CROWDING_LOW = 0.30
FLOW_Z_HIGH = 1.0
FLOW_Z_LOW = -1.0
CUMFLOW_WINDOW = 60
CUMFLOW_MIN = 30
Z_WINDOW = 60
Z_MIN = 30
PCT_WINDOW = 126
PCT_MIN = 30
ADV_WINDOW = 20
ADV_MIN = 10

STATE_MAP = {
    1: ("EarlyTrend", "低拥挤+高流入：早期趋势，建议跟随"),
    2: ("LateTrend", "高拥挤+高流入：晚期趋势，警惕反转"),
    3: ("Distribution", "低拥挤+高流出：出货阶段，中性等待"),
    4: ("Reversal", "高拥挤+高流出：反转信号，均值回归"),
}

DIRECTION_MAP = {1: "bullish", 2: "bearish", 3: "neutral", 4: "bullish", 0: "neutral"}

DATA_DIR = "/Users/yaogaga/Documents/资金组数据代码协作/Data/"


# ──────────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────────
def load_stock_data(stock_code: int, data_dir: str = DATA_DIR):
    """加载单只股票的合并数据"""
    mf_path = os.path.join(data_dir, "money_flow_df.parquet")
    dd_path = os.path.join(data_dir, "daily_df.parquet")
    pf_path = os.path.join(data_dir, "prop_df.parquet")

    # 只读取目标股票的数据以节省内存
    mf = pd.read_parquet(mf_path, filters=[("Stock", "==", stock_code)])
    dd = pd.read_parquet(dd_path, filters=[("Stock", "==", stock_code)])
    pf = pd.read_parquet(pf_path, filters=[("Stock", "==", stock_code)])

    # 合并
    df = mf.merge(dd, on=["Date", "Stock"], how="left")
    df = df.merge(pf, on=["Date", "Stock"], how="left")
    df = df.sort_values("Date").reset_index(drop=True)

    return df


# ──────────────────────────────────────────────
# 指标计算
# ──────────────────────────────────────────────
def rolling_zscore(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    """滚动 z-score"""
    mean = s.rolling(window, min_periods=min_periods).mean()
    std = s.rolling(window, min_periods=min_periods).std()
    return (s - mean) / std.replace(0, np.nan)


def rolling_percentile(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    """滚动历史分位（0~1）"""
    return s.rolling(window, min_periods=min_periods).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )


def compute_flow_zscore(df: pd.DataFrame) -> pd.Series:
    """计算资金流 z-score（Y轴）"""
    return rolling_zscore(df["NetAmountMain"], Z_WINDOW, Z_MIN)


def compute_adv20(df: pd.DataFrame) -> pd.Series:
    """计算 20 日均成交额"""
    return df["Amount"].rolling(ADV_WINDOW, min_periods=ADV_MIN).mean()


def compute_crowding_pct(df: pd.DataFrame) -> pd.Series:
    """计算拥挤度历史分位（X轴）"""
    # 60日累计主力净流入
    cum_flow = df["NetAmountMain"].rolling(CUMFLOW_WINDOW, min_periods=CUMFLOW_MIN).sum()

    # 分母：优先用 CirculatingMarketCap，缺失时用 ADV_20 × 240 代理
    float_mv = df["CirculatingMarketCap"].copy()
    if float_mv.isna().all():
        adv20 = compute_adv20(df)
        float_mv = adv20 * 240

    ratio = cum_flow / float_mv.replace(0, np.nan)

    # 126日历史分位
    return rolling_percentile(ratio, PCT_WINDOW, PCT_MIN)


def classify_state(flow_z: float, crowding_pct: float) -> int:
    """四象限分类"""
    high_flow = flow_z >= FLOW_Z_HIGH
    low_flow = flow_z <= FLOW_Z_LOW
    high_crowd = crowding_pct >= CROWDING_HIGH
    low_crowd = crowding_pct <= CROWDING_LOW

    if low_crowd and high_flow:
        return 1  # EarlyTrend
    elif high_crowd and high_flow:
        return 2  # LateTrend
    elif low_crowd and low_flow:
        return 3  # Distribution
    elif high_crowd and low_flow:
        return 4  # Reversal
    else:
        return 0  # Neutral


# ──────────────────────────────────────────────
# 信号生成
# ──────────────────────────────────────────────
def compute_confidence(state: int, flow_z: float, crowding_pct: float,
                       data_len: int, has_margin: bool = False,
                       has_dragon: bool = False) -> float:
    """根据象限、极端程度、数据充足性计算置信度"""
    if state == 0:
        return 0.3

    # 基础置信度
    base = 0.50

    # 极端程度加成
    z_boost = min(abs(flow_z) - 1.0, 2.0) * 0.05  # z>1 每多0.2加0.05, 上限+0.10
    pct_boost = 0.0
    if crowding_pct > CROWDING_HIGH:
        pct_boost = min(crowding_pct - CROWDING_HIGH, 0.20) * 0.25  # 上限+0.05
    elif crowding_pct < CROWDING_LOW:
        pct_boost = min(CROWDING_LOW - crowding_pct, 0.20) * 0.25

    # 数据充足性减成
    data_penalty = 0.0
    if data_len < 60:
        data_penalty = 0.10
    elif data_len < 90:
        data_penalty = 0.05

    # 单指标减成（缺融资余额和龙虎榜）
    sub_indicator_penalty = 0.0
    if not has_margin and not has_dragon:
        sub_indicator_penalty = 0.10

    confidence = base + z_boost + pct_boost - data_penalty - sub_indicator_penalty
    return round(max(0.2, min(0.9, confidence)), 2)


def compute_risk_level(state: int, flow_z: float, crowding_pct: float) -> str:
    """计算风险等级"""
    if state == 0 or state == 1:
        return "low"
    elif state == 2:
        if abs(flow_z) > 2.0 and crowding_pct > 0.90:
            return "high"
        return "medium"
    elif state == 3:
        return "medium"
    elif state == 4:
        if abs(flow_z) > 2.0 and crowding_pct > 0.90:
            return "high"
        return "high" if crowding_pct > 0.85 else "medium"
    return "low"


def build_signal(stock_code: str, df: pd.DataFrame, target_date: int = None) -> dict:
    """构建完整 Signal JSON"""
    if target_date:
        df = df[df["Date"] <= target_date]

    if len(df) < 10:
        return _insufficient_data_signal(stock_code, f"数据不足: 仅{len(df)}行")

    # 计算指标
    flow_z = compute_flow_zscore(df)
    crowding_pct = compute_crowding_pct(df)

    # 取最新一行
    latest = df.iloc[-1]
    latest_flow_z = flow_z.iloc[-1]
    latest_crowding_pct = crowding_pct.iloc[-1]
    latest_date = int(latest["Date"])

    # 处理 NaN
    if pd.isna(latest_flow_z) or pd.isna(latest_crowding_pct):
        return _insufficient_data_signal(
            stock_code,
            f"指标为NaN: Flow_zscore={latest_flow_z}, CrowdingPct={latest_crowding_pct}"
        )

    # 检查停牌/ST
    is_paused = latest.get("IsPaused", 0)
    is_st = latest.get("IsST", 0)
    if is_paused == 1 or is_st == 1:
        state_code = 0
    else:
        state_code = classify_state(latest_flow_z, latest_crowding_pct)

    # 象限信息
    state_label = STATE_MAP.get(state_code, (None, None))[0]
    state_desc = STATE_MAP.get(state_code, (None, None))[1]
    direction = DIRECTION_MAP[state_code]
    confidence = compute_confidence(
        state_code, latest_flow_z, latest_crowding_pct,
        data_len=len(df), has_margin=False, has_dragon=False
    )
    risk_level = compute_risk_level(state_code, latest_flow_z, latest_crowding_pct)

    # 交互项
    flow_x_crowding = round(latest_flow_z * latest_crowding_pct, 4)

    # 日期格式化
    date_str = str(latest_date)
    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    # 构建 reasoning
    reasoning = _build_reasoning(stock_code, state_code, state_label, state_desc,
                                  latest_flow_z, latest_crowding_pct, direction)

    # 构建 signals
    signals = _build_signals(state_code, state_label, latest_flow_z, latest_crowding_pct)

    # 构建详细数据
    adv20 = compute_adv20(df).iloc[-1] if len(df) >= ADV_MIN else None
    cum_flow = df["NetAmountMain"].tail(CUMFLOW_WINDOW).sum()
    float_mv = latest.get("CirculatingMarketCap", np.nan)

    cum_flow_over_float = None
    if not pd.isna(float_mv) and float_mv > 0:
        cum_flow_over_float = round(cum_flow / float_mv, 6)

    detail = {
        "flow_zscore": round(float(latest_flow_z), 4),
        "composite_crowding_pct": round(float(latest_crowding_pct), 4),
        "state_code": state_code,
        "state_label": state_label,
        "state_desc": state_desc,
        "flow_x_crowding": flow_x_crowding,
    }
    if cum_flow_over_float is not None:
        detail["cum_flow_over_float"] = cum_flow_over_float
    if adv20 is not None and not pd.isna(adv20):
        detail["adv_20"] = round(float(adv20), 0)
    if not pd.isna(latest.get("NetAmountMain")):
        detail["net_amount_main_latest"] = float(latest["NetAmountMain"])
    if not pd.isna(latest.get("Close")):
        detail["close_price"] = float(latest["Close"])
    if not pd.isna(float_mv):
        detail["circulating_market_cap"] = float(float_mv)

    # 构建证据
    evidence = [
        {
            "source_type": "fund_flow",
            "source_name": "资金组 money_flow_df",
            "date": formatted_date,
            "metric": "主力净流入z-score",
            "value": str(round(float(latest_flow_z), 2)),
            "comparison": "超过+1.0高流入阈值" if latest_flow_z >= FLOW_Z_HIGH else
                          ("低于-1.0高流出阈值" if latest_flow_z <= FLOW_Z_LOW else "处于-1到+1中间区域"),
            "note": f"{Z_WINDOW}日滚动z-score"
        },
        {
            "source_type": "fund_flow",
            "source_name": "资金组 money_flow_df + prop_df",
            "date": formatted_date,
            "metric": "累计流入/流通市值 126日分位",
            "value": f"{round(float(latest_crowding_pct)*100, 1)}%",
            "comparison": "超过70%高拥挤阈值" if latest_crowding_pct >= CROWDING_HIGH else
                          ("低于30%低拥挤阈值" if latest_crowding_pct <= CROWDING_LOW else "处于30%-70%中间区域"),
            "note": f"近{CUMFLOW_WINDOW}日累计主力净流入占流通市值比，{PCT_WINDOW}日历史分位"
        }
    ]

    # 构建关键发现
    key_findings = [
        f"四象限状态: {state_label or 'Neutral'}（{state_desc or '中性区，资金面无极端信号'}）",
        f"拥挤度分位{round(float(latest_crowding_pct)*100, 1)}%，主力净流入z-score={round(float(latest_flow_z), 2)}",
    ]
    if state_code != 0:
        key_findings.append(f"交互项 Flow_x_Crowding={flow_x_crowding}")

    # 不确定性
    uncertainties = ["融资余额数据暂缺，拥挤度维度覆盖不完整",
                     "龙虎榜数据暂缺，无法验证游资参与度"]
    if len(df) < 90:
        uncertainties.append(f"历史数据仅{len(df)}日（建议≥60），统计指标可靠性下降")

    needs_human_review = bool(state_code == 4 or (state_code != 0 and confidence < 0.4))

    signal = {
        "direction": direction,
        "confidence": confidence,
        "reasoning": reasoning,
        "signals": signals,
        "source": "crowding_state2x2",
        "signal_type": "fundflow",
        "stock_code": str(stock_code),
        "weight": 1.0,
        "meta": {
            "output_version": "0.1",
            "skill_name": "crowding_state2x2",
            "owner_group": "专家3组（资金）",
            "target": "资金拥挤度四象限诊断",
            "period": formatted_date,
            "time_horizon": "short",
            "risk_level": risk_level,
            "key_findings": key_findings,
            "evidence": evidence,
            "risk_notes": _build_risk_notes(state_code, latest_flow_z, latest_crowding_pct),
            "uncertainties": uncertainties,
            "needs_human_review": needs_human_review,
            "state2x2_detail": detail,
        }
    }
    return signal


def _insufficient_data_signal(stock_code: str, reason: str) -> dict:
    """数据不足时的降级信号"""
    return {
        "direction": "neutral",
        "confidence": 0.1,
        "reasoning": f"股票{stock_code}数据不足，无法完成四象限诊断: {reason}",
        "signals": [],
        "source": "crowding_state2x2",
        "signal_type": "fundflow",
        "stock_code": str(stock_code),
        "weight": 1.0,
        "meta": {
            "output_version": "0.1",
            "skill_name": "crowding_state2x2",
            "owner_group": "专家3组（资金）",
            "target": "资金拥挤度四象限诊断",
            "period": "",
            "time_horizon": "short",
            "risk_level": "low",
            "key_findings": [f"数据不足，无法诊断: {reason}"],
            "evidence": [],
            "risk_notes": [],
            "uncertainties": [reason],
            "needs_human_review": True,
            "state2x2_detail": {},
        }
    }


def _build_reasoning(stock_code, state_code, state_label, state_desc,
                     flow_z, crowding_pct, direction):
    """构建 reasoning 文本"""
    z_str = f"{flow_z:+.2f}"
    pct_str = f"{crowding_pct*100:.1f}%"

    if state_code == 0:
        return (f"股票{stock_code}当前处于Neutral（中性区）："
                f"拥挤度分位{pct_str}（中间区域），主力净流入z-score={z_str}（中间区域），"
                f"均未触达极端阈值，无明确方向信号。")

    dir_text = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}[direction]
    return (f"股票{stock_code}当前处于State{state_code}-{state_label}"
            f"（{state_desc}）："
            f"拥挤度分位{pct_str}，主力净流入z-score={z_str}。"
            f"信号方向{dir_text}。")


def _build_signals(state_code, state_label, flow_z, crowding_pct):
    """构建 signals 列表"""
    signals = [
        f"拥挤度分位{crowding_pct*100:.1f}%，"
        + ("超过70%高阈值" if crowding_pct >= CROWDING_HIGH else
           "低于30%低阈值" if crowding_pct <= CROWDING_LOW else
           "处于中间区域"),
        f"主力净流入z-score={flow_z:+.2f}，"
        + ("超过+1.0阈值" if flow_z >= FLOW_Z_HIGH else
           "低于-1.0阈值" if flow_z <= FLOW_Z_LOW else
           "处于中间区域"),
        f"四象限状态: {state_label or 'Neutral'}",
    ]
    if state_code != 0:
        signals.append(state_code_desc_brief(state_code))
    return signals


def state_code_desc_brief(state_code: int) -> str:
    """象限简短描述"""
    return {
        1: "低拥挤+高流入，早期趋势，建议跟随",
        2: "高拥挤+高流入，晚期趋势，警惕反转",
        3: "低拥挤+高流出，出货阶段，中性等待",
        4: "高拥挤+高流出，反转信号，均值回归",
    }.get(state_code, "")


def _build_risk_notes(state_code, flow_z, crowding_pct):
    """构建风险提示"""
    notes = []
    if state_code == 2:
        notes.append("LateTrend状态不意味着立即反转，趋势可能持续一段时间")
        notes.append("拥挤度仅基于资金流/市值比单一维度，缺少融资余额和龙虎榜交叉验证")
    elif state_code == 4:
        notes.append("Reversal信号不意味着立即反弹，资金流出可能持续")
        notes.append("高拥挤出清过程可能伴随剧烈波动")
    elif state_code == 3:
        notes.append("Distribution状态可能持续较长时间，需等待资金面改善信号")
    elif state_code == 1:
        notes.append("EarlyTrend需确认资金流入的持续性，单日异动不构成趋势")
    return notes


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="资金拥挤度四象限状态诊断")
    parser.add_argument("--stock", required=True, help="股票代码，如 600519")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD，默认取最新")
    parser.add_argument("--data-dir", default=DATA_DIR, help="数据目录路径")
    args = parser.parse_args()

    stock_code = int(args.stock)
    target_date = int(args.date) if args.date else None

    print(f"加载股票 {stock_code} 数据...")
    df = load_stock_data(stock_code, args.data_dir)
    print(f"数据行数: {len(df)}")

    if len(df) == 0:
        print(f"错误: 未找到股票 {stock_code} 的数据", file=sys.stderr)
        sys.exit(1)

    signal = build_signal(stock_code, df, target_date)
    print(json.dumps(signal, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
