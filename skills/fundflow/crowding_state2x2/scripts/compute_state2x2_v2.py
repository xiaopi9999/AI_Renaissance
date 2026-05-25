"""
资金拥挤度四象限状态诊断 — 基于 AI_Renaissance 项目数据接口

重构版本：使用 AkshareDataSource + tencent_technical 替代硬编码 parquet 数据。
替代方案：
  - Amount 缺失 → 用 Volume 替代（ADV_20 改为 20 日均量）
  - CirculatingMarketCap 缺失 → 用 ADV_20(Volume) × 240 代理
  - 资金流仅 ~120 日 → PCT_WINDOW 从 126 缩短至 90

用法:
    python compute_state2x2_v2.py --stock 600519 [--date 20260515] [--plot]

数据来源:
    - AkshareDataSource.get_stock_fund_flow() → 主力净流入序列
    - tencent_technical/fetch_kline.py → 收盘价、成交量序列
"""

import argparse
import json
import sys
import os
from datetime import datetime

import numpy as np
import pandas as pd

# ── 项目路径（脚本在 skills/fundflow/crowding_state2x2/scripts/ 下，需向上4级） ──
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

# ──────────────────────────────────────────────
# 常量（与 SKILL.md 第4节对齐，适配数据接口调整）
# ──────────────────────────────────────────────
CROWDING_HIGH = 0.70
CROWDING_LOW = 0.30
FLOW_Z_HIGH = 1.0
FLOW_Z_LOW = -1.0
CUMFLOW_WINDOW = 60
CUMFLOW_MIN = 30
Z_WINDOW = 60
Z_MIN = 30
PCT_WINDOW = 90       # 原 126，因 AkShare 资金流仅 ~120 日，缩短至 90
PCT_MIN = 30
ADV_WINDOW = 20
ADV_MIN = 10
ADV_PROXY_MULTIPLIER = 240  # ADV × 240 作为流通市值代理

STATE_MAP = {
    1: ("EarlyTrend", "低拥挤+高流入：早期趋势，建议跟随"),
    2: ("LateTrend", "高拥挤+高流入：晚期趋势，警惕反转"),
    3: ("Distribution", "低拥挤+高流出：出货阶段，中性等待"),
    4: ("Reversal", "高拥挤+高流出：反转信号，均值回归"),
}

DIRECTION_MAP = {1: "bullish", 2: "bearish", 3: "neutral", 4: "bullish", 0: "neutral"}


# ──────────────────────────────────────────────
# 数据加载（使用项目数据接口）
# ──────────────────────────────────────────────
def load_stock_data(stock_code: str):
    """
    从项目数据接口加载单只股票的合并数据。

    Returns:
        pd.DataFrame: 合并后的 DataFrame，包含:
            - Date (datetime)
            - NetAmountMain (float): 主力净流入（元）
            - Close (float): 收盘价
            - Volume (float): 成交量（手）
    """
    # ── 1. 资金流数据 ──
    from data_sources.akshare import AkshareDataSource
    ak_src = AkshareDataSource()
    flow_result = ak_src.get_stock_fund_flow(stock_code, limit=200)

    if flow_result["status"] != "success":
        raise RuntimeError(f"AkShare 资金流获取失败: {flow_result.get('error', 'unknown')}")

    flow_records = flow_result.get("recent", [])
    if not flow_records:
        raise RuntimeError(f"股票 {stock_code} 无资金流数据")

    df_flow = pd.DataFrame(flow_records)
    # AkShare 返回的日期格式为 "2026年05月15日"
    df_flow["Date"] = pd.to_datetime(df_flow["日期"].str.replace("年", "-").str.replace("月", "-").str.replace("日", ""))
    df_flow["NetAmountMain"] = pd.to_numeric(df_flow["主力净流入-净额"], errors="coerce")
    df_flow = df_flow[["Date", "NetAmountMain"]].dropna(subset=["NetAmountMain"])
    df_flow = df_flow.sort_values("Date").reset_index(drop=True)

    # ── 2. K线行情数据 ──
    import importlib.util
    kline_path = os.path.join(PROJECT_ROOT, "skills", "data", "tencent_technical", "scripts", "fetch_kline.py")
    spec = importlib.util.spec_from_file_location("fetch_kline", kline_path)
    kline_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kline_mod)

    # 获取足够长的 K 线（覆盖资金流 + 滚动窗口）
    kline_result = kline_mod.fetch_kline(stock_code, k_type="day", num=200)

    if kline_result["status"] != "success":
        raise RuntimeError(f"腾讯 K 线获取失败: {kline_result.get('error', 'unknown')}")

    kline_data = kline_result.get("kline", [])
    if not kline_data:
        raise RuntimeError(f"股票 {stock_code} 无 K 线数据")

    df_kline = pd.DataFrame(kline_data)
    df_kline["Date"] = pd.to_datetime(df_kline["date"])
    df_kline["Close"] = pd.to_numeric(df_kline["close"], errors="coerce")
    df_kline["Volume"] = pd.to_numeric(df_kline["volume"], errors="coerce")
    df_kline = df_kline[["Date", "Close", "Volume"]].dropna(subset=["Close", "Volume"])
    df_kline = df_kline.sort_values("Date").reset_index(drop=True)

    # ── 3. 合并 ──
    df = df_flow.merge(df_kline, on="Date", how="left")
    df = df.sort_values("Date").reset_index(drop=True)

    # 过滤掉没有 Close 的行（资金流有但 K 线缺失的日期）
    df = df.dropna(subset=["Close", "Volume"])

    return df


# ──────────────────────────────────────────────
# 指标计算（逻辑与原版一致，仅数据来源不同）
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


def compute_adv20_volume(df: pd.DataFrame) -> pd.Series:
    """计算 20 日均成交量（替代原 ADV_20 成交额）"""
    return df["Volume"].rolling(ADV_WINDOW, min_periods=ADV_MIN).mean()


def compute_crowding_pct(df: pd.DataFrame) -> pd.Series:
    """计算拥挤度历史分位（X轴）
    
    使用 ADV_20(Volume) × 240 作为流通市值代理。
    注意：原版使用 CirculatingMarketCap 或 ADV_20(Amount) × 240，
    此处使用 Volume 替代 Amount。
    """
    # 60日累计主力净流入
    cum_flow = df["NetAmountMain"].rolling(CUMFLOW_WINDOW, min_periods=CUMFLOW_MIN).sum()

    # 分母：ADV_20(Volume) × 240 作为流通市值代理
    # Volume 单位为手，乘 100 得股数，再乘一个价格因子近似
    # 更简洁的做法：直接用 ADV_20(Volume) × Close × 100 作为市值代理
    # 但为避免前复权偏差，使用 ADV_20(Volume) × 240 绝对值代理
    adv20_vol = compute_adv20_volume(df)
    float_mv_proxy = adv20_vol * ADV_PROXY_MULTIPLIER

    # 注意：NetAmountMain 单位是元，Volume 单位是手
    # 需要对齐量纲。NetAmountMain（元） / (Volume(手) × 240) 量纲不匹配
    # 正确做法：用 Close × Volume × 100 近似成交额（元），再 × 240
    # 或者：用 ADV_20(Close × Volume × 100) × 240
    # 简化版：float_mv_proxy = adv20(Close × Volume × 100) × 240
    # 但这样可能导致 CumFlowOverFloat 过小。
    # 最简方案：直接用 Volume 的 ADV_20 × 240 作为市值代理，NetAmountMain 需要归一化
    
    # 实际更合理的代理：
    # 近似成交额 = Close × Volume × 100
    approx_amount = df["Close"] * df["Volume"] * 100
    adv20_amount = approx_amount.rolling(ADV_WINDOW, min_periods=ADV_MIN).mean()
    float_mv_proxy = adv20_amount * ADV_PROXY_MULTIPLIER

    ratio = cum_flow / float_mv_proxy.replace(0, np.nan)

    # PCT_WINDOW 日历史分位
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
# 信号生成（与原版一致）
# ──────────────────────────────────────────────
def compute_confidence(state: int, flow_z: float, crowding_pct: float,
                       data_len: int, has_margin: bool = False,
                       has_dragon: bool = False) -> float:
    """根据象限、极端程度、数据充足性计算置信度"""
    if state == 0:
        return 0.3

    base = 0.50
    z_boost = min(abs(flow_z) - 1.0, 2.0) * 0.05
    pct_boost = 0.0
    if crowding_pct > CROWDING_HIGH:
        pct_boost = min(crowding_pct - CROWDING_HIGH, 0.20) * 0.25
    elif crowding_pct < CROWDING_LOW:
        pct_boost = min(CROWDING_LOW - crowding_pct, 0.20) * 0.25

    data_penalty = 0.0
    if data_len < 60:
        data_penalty = 0.10
    elif data_len < 90:
        data_penalty = 0.05

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


def build_signal(stock_code: str, df: pd.DataFrame, target_date: str = None) -> dict:
    """构建完整 Signal JSON"""
    if target_date:
        target_dt = pd.to_datetime(target_date)
        df = df[df["Date"] <= target_dt]

    if len(df) < 10:
        return _insufficient_data_signal(stock_code, f"数据不足: 仅{len(df)}行")

    # 计算指标
    flow_z = compute_flow_zscore(df)
    crowding_pct = compute_crowding_pct(df)

    # 取最新一行
    latest = df.iloc[-1]
    latest_flow_z = flow_z.iloc[-1]
    latest_crowding_pct = crowding_pct.iloc[-1]
    latest_date = latest["Date"]

    # 处理 NaN
    if pd.isna(latest_flow_z) or pd.isna(latest_crowding_pct):
        return _insufficient_data_signal(
            stock_code,
            f"指标为NaN: Flow_zscore={latest_flow_z}, CrowdingPct={latest_crowding_pct}"
        )

    # 四象限分类
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
    formatted_date = latest_date.strftime("%Y-%m-%d")

    # 构建详细数据
    adv20_vol = compute_adv20_volume(df).iloc[-1] if len(df) >= ADV_MIN else None
    cum_flow = df["NetAmountMain"].tail(CUMFLOW_WINDOW).sum()

    # CumFlowOverFloat 近似
    approx_amount_latest = latest["Close"] * latest["Volume"] * 100
    adv20_amount = approx_amount_latest * ADV_WINDOW  # 简化
    cum_flow_over_float = round(cum_flow / (adv20_amount * ADV_PROXY_MULTIPLIER / ADV_WINDOW), 6) if adv20_amount > 0 else None

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
    if adv20_vol is not None and not pd.isna(adv20_vol):
        detail["adv_20_volume"] = round(float(adv20_vol), 0)
    if not pd.isna(latest.get("NetAmountMain")):
        detail["net_amount_main_latest"] = float(latest["NetAmountMain"])
    if not pd.isna(latest.get("Close")):
        detail["close_price"] = float(latest["Close"])
    detail["data_source"] = "akshare_fundflow + tencent_kline"
    detail["circulating_market_cap_proxy"] = "ADV_20(amount_approx) × 240"

    # 构建证据
    evidence = [
        {
            "source_type": "fund_flow",
            "source_name": "AkshareDataSource.get_stock_fund_flow()",
            "date": formatted_date,
            "metric": "主力净流入z-score",
            "value": str(round(float(latest_flow_z), 2)),
            "comparison": "超过+1.0高流入阈值" if latest_flow_z >= FLOW_Z_HIGH else
                          ("低于-1.0高流出阈值" if latest_flow_z <= FLOW_Z_LOW else "处于-1到+1中间区域"),
            "note": f"{Z_WINDOW}日滚动z-score"
        },
        {
            "source_type": "fund_flow",
            "source_name": "AkshareDataSource + tencent_technical",
            "date": formatted_date,
            "metric": f"累计流入/流通市值代理 {PCT_WINDOW}日分位",
            "value": f"{round(float(latest_crowding_pct)*100, 1)}%",
            "comparison": "超过70%高拥挤阈值" if latest_crowding_pct >= CROWDING_HIGH else
                          ("低于30%低拥挤阈值" if latest_crowding_pct <= CROWDING_LOW else "处于30%-70%中间区域"),
            "note": f"近{CUMFLOW_WINDOW}日累计主力净流入/ADV_20×240，{PCT_WINDOW}日历史分位"
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
    uncertainties = [
        "融资余额数据暂缺，拥挤度维度覆盖不完整",
        "龙虎榜数据暂缺，无法验证游资参与度",
        f"流通市值使用 ADV_20×240 代理（非真实市值），PCT_WINDOW={PCT_WINDOW}（原126，因AkShare数据限制缩短）",
    ]
    if len(df) < 90:
        uncertainties.append(f"历史数据仅{len(df)}日（建议≥60），统计指标可靠性下降")

    needs_human_review = bool(state_code == 4 or (state_code != 0 and confidence < 0.4))

    reasoning = _build_reasoning(stock_code, state_code, state_label, state_desc,
                                  latest_flow_z, latest_crowding_pct, direction)
    signals = _build_signals(state_code, state_label, latest_flow_z, latest_crowding_pct)

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
            "output_version": "0.2",
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
    return signal, flow_z, crowding_pct, df


def _insufficient_data_signal(stock_code: str, reason: str) -> tuple:
    """数据不足时的降级信号"""
    signal = {
        "direction": "neutral",
        "confidence": 0.1,
        "reasoning": f"股票{stock_code}数据不足，无法完成四象限诊断: {reason}",
        "signals": [],
        "source": "crowding_state2x2",
        "signal_type": "fundflow",
        "stock_code": str(stock_code),
        "weight": 1.0,
        "meta": {
            "output_version": "0.2",
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
    return signal, None, None, None


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
        signals.append(STATE_MAP.get(state_code, ("", ""))[1])
    return signals


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
# 股票名称查询
# ──────────────────────────────────────────────
def get_stock_name(stock_code: str) -> str:
    """通过 BaoStock 查询股票名称，失败则返回空字符串"""
    try:
        import baostock as bs
        bs.login()
        # 补全 baostock 代码格式: sh.600519 / sz.000001
        prefix = "sh." if stock_code.startswith(("6", "9")) else "sz."
        rs = bs.query_stock_basic(code=prefix + stock_code)
        name = ""
        while rs.next():
            name = rs.get_row_data()[1]  # code_name
            break
        bs.logout()
        return name
    except Exception:
        return ""


# ──────────────────────────────────────────────
# 可视化
# ──────────────────────────────────────────────
def plot_state2x2(stock_code: str, flow_z: pd.Series, crowding_pct: pd.Series,
                  df: pd.DataFrame, signal: dict, output_path: str = None,
                  stock_name: str = ""):
    """绘制 State2x2 四象限图"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.font_manager import FontProperties

    # 中文字体
    font_path = "/System/Library/Fonts/Supplemental/PingFang.ttc"
    if not os.path.exists(font_path):
        font_path = "/System/Library/Fonts/STHeiti Light.ttc"
    zh_font = FontProperties(fname=font_path)

    fig, ax = plt.subplots(1, 1, figsize=(10, 8))

    # 背景色：四个象限
    ax.axhspan(FLOW_Z_HIGH, 3.5, xmin=0, xmax=CROWDING_LOW, alpha=0.12, color="#2ecc71", label="EarlyTrend")
    ax.axhspan(FLOW_Z_HIGH, 3.5, xmin=CROWDING_HIGH, xmax=1.0, alpha=0.12, color="#e74c3c", label="LateTrend")
    ax.axhspan(-3.5, FLOW_Z_LOW, xmin=0, xmax=CROWDING_LOW, alpha=0.12, color="#95a5a6", label="Distribution")
    ax.axhspan(-3.5, FLOW_Z_LOW, xmin=CROWDING_HIGH, xmax=1.0, alpha=0.12, color="#3498db", label="Reversal")

    # 阈值线
    ax.axhline(y=FLOW_Z_HIGH, color="#e67e22", linestyle="--", linewidth=1.0, alpha=0.7)
    ax.axhline(y=FLOW_Z_LOW, color="#e67e22", linestyle="--", linewidth=1.0, alpha=0.7)
    ax.axvline(x=CROWDING_HIGH, color="#e67e22", linestyle="--", linewidth=1.0, alpha=0.7)
    ax.axvline(x=CROWDING_LOW, color="#e67e22", linestyle="--", linewidth=1.0, alpha=0.7)

    # 散点轨迹
    valid_mask = flow_z.notna() & crowding_pct.notna()
    fz = flow_z[valid_mask].reset_index(drop=True)
    cp = crowding_pct[valid_mask].reset_index(drop=True)
    dates = df.loc[valid_mask, "Date"].reset_index(drop=True)

    if len(fz) > 1:
        # 历史轨迹（渐变透明度）
        n = len(fz)
        colors = plt.cm.Greys(np.linspace(0.15, 0.5, n))
        ax.scatter(cp, fz, c=colors, s=15, alpha=0.6, zorder=2)

    # 当前点（大红点）
    current_fz = fz.iloc[-1]
    current_cp = cp.iloc[-1]
    current_date = dates.iloc[-1].strftime("%Y-%m-%d")

    state_code = signal["meta"]["state2x2_detail"]["state_code"]
    state_colors = {0: "#7f8c8d", 1: "#2ecc71", 2: "#e74c3c", 3: "#95a5a6", 4: "#3498db"}
    marker_color = state_colors.get(state_code, "#7f8c8d")

    ax.scatter(current_cp, current_fz, c=marker_color, s=200, zorder=5,
               edgecolors="white", linewidth=2, marker="o")
    ax.annotate(
        f"{current_date}\nz={current_fz:+.2f}, pct={current_cp:.1%}",
        xy=(current_cp, current_fz),
        xytext=(15, 15), textcoords="offset points",
        fontsize=9, fontproperties=zh_font,
        arrowprops=dict(arrowstyle="->", color=marker_color, lw=1.5),
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor=marker_color, alpha=0.9)
    )

    # 象限标签
    label_props = dict(fontsize=11, fontproperties=zh_font, alpha=0.6, ha="center", va="center")
    ax.text(0.15, 2.0, "State 1\nEarlyTrend", color="#2ecc71", **label_props)
    ax.text(0.85, 2.0, "State 2\nLateTrend", color="#e74c3c", **label_props)
    ax.text(0.15, -2.0, "State 3\nDistribution", color="#7f8c8d", **label_props)
    ax.text(0.85, -2.0, "State 4\nReversal", color="#3498db", **label_props)
    ax.text(0.50, 0.0, "Neutral", color="#bdc3c7", fontsize=13, fontproperties=zh_font,
            alpha=0.5, ha="center", va="center")

    # 标题和轴标签
    state_label = signal["meta"]["state2x2_detail"].get("state_label") or "Neutral"
    direction = signal["direction"]
    dir_zh = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}.get(direction, "")
    display_name = f"{stock_name}({stock_code})" if stock_name else stock_code
    ax.set_title(
        f"{display_name} 资金拥挤度四象限诊断\n"
        f"当前状态: {state_label} ({dir_zh}) | {current_date}",
        fontproperties=zh_font, fontsize=14, pad=15
    )
    ax.set_xlabel(f"拥挤度分位 (CumFlowOverFloat {PCT_WINDOW}日历史分位)", fontproperties=zh_font, fontsize=11)
    ax.set_ylabel("资金流强度 (主力净流入 60日 z-score)", fontproperties=zh_font, fontsize=11)

    # 设置坐标范围
    ax.set_xlim(-0.05, 1.05)
    fz_range = fz.abs().max()
    y_lim = max(3.0, fz_range + 0.5)
    ax.set_ylim(-y_lim, y_lim)

    # 信息框
    info_text = (
        f"数据来源: AkShare资金流 + 腾讯K线\n"
        f"PCT_WINDOW={PCT_WINDOW} (适配AkShare~120日限制)\n"
        f"流通市值代理: ADV_20×240"
    )
    ax.text(0.02, 0.02, info_text, transform=ax.transAxes, fontsize=7.5,
            fontproperties=zh_font, verticalalignment="bottom",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

    ax.grid(True, alpha=0.2)
    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"图表已保存: {output_path}")
    plt.close(fig)


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="资金拥挤度四象限状态诊断（v2 - 项目数据接口）")
    parser.add_argument("--stock", required=True, help="股票代码，如 600519")
    parser.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD，默认取最新")
    parser.add_argument("--plot", action="store_true", help="生成四象限图")
    parser.add_argument("--output", default=None, help="图表输出路径")
    args = parser.parse_args()

    stock_code = args.stock

    # 查询股票名称
    stock_name = get_stock_name(stock_code)
    print(f"股票: {stock_name}({stock_code})" if stock_name else f"股票: {stock_code}")

    print(f"加载股票 {stock_code} 数据（项目数据接口）...")
    df = load_stock_data(stock_code)
    print(f"数据行数: {len(df)}，日期范围: {df['Date'].min().strftime('%Y-%m-%d')} ~ {df['Date'].max().strftime('%Y-%m-%d')}")

    if len(df) == 0:
        print(f"错误: 未找到股票 {stock_code} 的数据", file=sys.stderr)
        sys.exit(1)

    result = build_signal(stock_code, df, args.date)
    signal = result[0]
    flow_z = result[1]
    crowding_pct = result[2]

    # 输出 Signal JSON
    # 移除 flow_z/crowding_pct/df 以便 JSON 序列化
    print(json.dumps(signal, ensure_ascii=False, indent=2))

    # 生成图表
    if args.plot and flow_z is not None:
        # plot 需要截断后的 df 以对齐 flow_z/crowding_pct 的 index
        df_plot = df.copy()
        if args.date:
            df_plot = df_plot[df_plot["Date"] <= pd.to_datetime(args.date)]
        output_path = args.output or f"state2x2_{stock_code}.png"
        plot_state2x2(stock_code, flow_z, crowding_pct, df_plot, signal, output_path,
                      stock_name=stock_name)


if __name__ == "__main__":
    main()
