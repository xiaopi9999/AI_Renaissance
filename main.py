"""
AI Renaissance 主入口 — 交互式多智能体投资决策引擎

运行方式：
    python main.py                          # 交互式模式（推荐，支持股票名称输入）
    python main.py --stock 000001           # CLI 模式
    python main.py --stock 600519,000858    # 批量分析

数据源：新浪财经 + 腾讯财经 + 东方财富 datacenter（零第三方依赖）
"""

import argparse
import sys
import os
import subprocess
import statistics
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
from report_generator import generate_html_report
from lightweight_fetcher import (
    search_stock, get_stock_full_data, format_cap,
)

# 配置日志
logger.remove()
logger.add(sys.stderr, level="WARNING")
logger.add("logs/arbitration.log", rotation="10 MB", retention="7 days", level="INFO")


# ── 分析引擎：基于真实数据生成各 Agent 信号 ─────────────────

def _calc_risk_from_klines(klines: list) -> dict:
    """
    直接从K线数据计算风险指标（不依赖外部API）。
    所有计算基于真实K线数据，确保零假数据。
    """
    if not klines or len(klines) < 2:
        return {}

    result = {}
    price = klines[-1]["close"]

    # 1. 近5日涨跌幅
    if len(klines) >= 5:
        ref = klines[-5]["close"]
        result["change_5d"] = (price - ref) / ref * 100 if ref > 0 else 0

    # 2. 近20日最大回撤（从峰值到谷值的最大跌幅）
    window = klines[-20:] if len(klines) >= 20 else klines
    peak = max(k["high"] for k in window)
    max_dd = 0
    running_peak = window[0]["high"]
    for k in window:
        if k["high"] > running_peak:
            running_peak = k["high"]
        dd = (running_peak - k["low"]) / running_peak * 100 if running_peak > 0 else 0
        max_dd = max(max_dd, dd)
    result["max_drawdown"] = max_dd

    # 3. 5日波动率（日收益率标准差 × 100）
    if len(klines) >= 6:
        returns = []
        for i in range(-5, 0):
            prev_close = klines[i - 1]["close"]
            if prev_close > 0:
                returns.append((klines[i]["close"] - prev_close) / prev_close)
        if len(returns) >= 2:
            result["volatility_5d"] = statistics.stdev(returns) * 100

    # 4. 量比 = 今日成交量 / 近5日均量
    if len(klines) >= 6:
        avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
        result["volume_ratio"] = klines[-1]["volume"] / avg_vol if avg_vol > 0 else 1.0

    # 5. 连续涨跌天数
    consec_up = 0
    consec_down = 0
    for i in range(len(klines) - 1, 0, -1):
        curr = klines[i]["close"]
        prev = klines[i - 1]["close"]
        if curr > prev:
            if consec_down > 0:
                break
            consec_up += 1
        elif curr < prev:
            if consec_up > 0:
                break
            consec_down += 1
        else:
            break
    result["consecutive_up"] = consec_up
    result["consecutive_down"] = consec_down

    return result


def _build_analysis(market_data: dict, stock_code: str, stock_name: str):
    """
    基于真实市场数据构建 7 个专家 Agent 的分析信号。

    Args:
        market_data: lightweight_fetcher.get_stock_full_data() 的返回值
        stock_code: 股票代码
        stock_name: 股票名称
    Returns:
        (result, agent_signals) 同之前的格式
    """
    quote = market_data.get("quote", {})
    klines = market_data.get("klines", [])
    fund_flow = market_data.get("fund_flow", [])
    margin = market_data.get("margin", {})
    billboard = market_data.get("billboard", [])
    sectors = market_data.get("sectors", [])
    macro = market_data.get("macro", {})

    price = quote.get("price") or quote.get("close") or 0
    change_pct = quote.get("change_pct") or 0
    pe = quote.get("pe")
    turnover = quote.get("turnover") or 0
    market_cap = quote.get("market_cap") or 0

    # === 风险指标（直接从K线计算，不依赖外部API） ===
    risk = _calc_risk_from_klines(klines)

    # === 资金流向汇总 ===
    main_net = 0
    if fund_flow:
        main_net = fund_flow[-1].get("main_net", 0)
    elif quote.get("big_deal_inflow") and quote.get("big_deal_outflow"):
        main_net = (quote.get("big_deal_inflow") or 0) - (quote.get("big_deal_outflow") or 0)

    # === 融资融券 ===
    rzye = margin.get("rzye") if margin else None

    # === 近期K线走势 ===
    recent_5d = klines[-5:] if len(klines) >= 5 else klines
    high_5d = max((k["high"] for k in recent_5d), default=0)
    low_5d = min((k["low"] for k in recent_5d), default=0)
    swing_5d = (high_5d - low_5d) / low_5d * 100 if low_5d > 0 else 0

    # === 连续涨跌 ===
    consec_up = risk.get("consecutive_up", 0)
    consec_down = risk.get("consecutive_down", 0)

    # === 构造 7 个 Agent 信号 ===

    # --- 1. 技术指标Agent ---
    tech_direction = "neutral"
    tech_conf = 0.50
    tech_signals = [{"indicator": "今日收盘", "value": f"{price}元", "signal": "neutral"}]

    if change_pct > 0:
        tech_signals.append({"indicator": "今日涨跌", "value": f"+{change_pct:.2f}%", "signal": "bullish"})
    elif change_pct < 0:
        tech_signals.append({"indicator": "今日涨跌", "value": f"{change_pct:.2f}%", "signal": "bearish"})

    # 近期走势描述
    recent_desc = f"今日收盘{price}元"
    if len(klines) >= 5:
        prev_close = klines[-2].get("close", price) if len(klines) >= 2 else price
        week_chg = (price - klines[-5].get("close", price)) / klines[-5].get("close", price) * 100 if klines[-5].get("close", 0) > 0 else 0
        recent_desc += f"，近5日{'上涨' if week_chg > 0 else '下跌'}{abs(week_chg):.2f}%"
        tech_signals.append({"indicator": "近5日涨跌", "value": f"{week_chg:+.2f}%", "signal": "bullish" if week_chg > 0 else "bearish"})

    if consec_up >= 3:
        tech_direction = "bullish"
        tech_conf = min(0.70, 0.50 + consec_up * 0.05)
        recent_desc += f"，连续上涨{consec_up}日，短期动量偏强"
        tech_signals.append({"indicator": "连续上涨", "value": f"{consec_up}日", "signal": "bullish"})
    elif consec_down >= 3:
        tech_direction = "bearish"
        tech_conf = min(0.70, 0.50 + consec_down * 0.05)
        recent_desc += f"，连续下跌{consec_down}日，短期动量偏弱"
        tech_signals.append({"indicator": "连续下跌", "value": f"{consec_down}日", "signal": "bearish"})
    else:
        recent_desc += "，近期无明确趋势方向"

    if swing_5d > 15:
        tech_signals.append({"indicator": "5日振幅", "value": f"{swing_5d:.1f}%", "signal": "warning"})
        recent_desc += f"。5日振幅{swing_5d:.1f}%，波动剧烈"

    # 量比
    vol_ratio = risk.get("volume_ratio", 1)
    if vol_ratio > 2:
        tech_signals.append({"indicator": "量比", "value": f"{vol_ratio:.1f}倍", "signal": "warning"})
        recent_desc += f"。今日量比{vol_ratio:.1f}倍，成交活跃"
    elif vol_ratio > 1.5:
        tech_signals.append({"indicator": "量比", "value": f"{vol_ratio:.1f}倍", "signal": "neutral"})
        recent_desc += f"。今日量比{vol_ratio:.1f}倍"

    # 位置判断（相对于高低点）
    if len(klines) >= 20:
        high_20d = max(k["high"] for k in klines[-20:])
        low_20d = min(k["low"] for k in klines[-20:])
        position_pct = (price - low_20d) / (high_20d - low_20d) * 100 if high_20d != low_20d else 50
        tech_signals.append({"indicator": "20日位置", "value": f"{position_pct:.0f}%", "signal": "bullish" if position_pct > 70 else ("bearish" if position_pct < 30 else "neutral")})
        recent_desc += f"。当前处于近20日价格区间的{position_pct:.0f}%位置"

    tech_reasoning = recent_desc

    # --- 2. 资金流向Agent ---
    fund_direction = "neutral"
    fund_conf = 0.50
    fund_reasoning = "资金流向分析"
    fund_signals = []

    main_net_yi = main_net / 1e8 if main_net else 0
    if main_net > 0:
        fund_direction = "bullish"
        fund_conf = min(0.75, 0.50 + abs(main_net_yi) * 0.02)
        fund_reasoning = f"今日主力资金净流入{main_net_yi:.2f}亿元，资金面偏多"
        fund_signals.append({"indicator": "今日主力净流入", "value": f"+{main_net_yi:.2f}亿", "signal": "bullish"})
    elif main_net < 0:
        fund_direction = "bearish"
        fund_conf = min(0.75, 0.50 + abs(main_net_yi) * 0.02)
        fund_reasoning = f"今日主力资金净流出{abs(main_net_yi):.2f}亿元，资金面偏空"
        fund_signals.append({"indicator": "今日主力净流入", "value": f"{main_net_yi:.2f}亿", "signal": "bearish"})
    else:
        fund_reasoning = "今日主力资金无明显方向性流动"
        fund_signals.append({"indicator": "今日主力净流入", "value": "0", "signal": "neutral"})

    if rzye:
        fund_signals.append({"indicator": "融资余额", "value": f"{rzye/1e8:.2f}亿", "signal": "neutral"})
        fund_reasoning += f"。融资余额{rzye/1e8:.2f}亿元"

    # --- 3. 风险预警Agent ---
    risk_direction = "neutral"
    risk_conf = 0.50
    risk_reasoning = "风险指标评估"
    risk_signals = []

    max_dd = risk.get("max_drawdown", 0)
    vol_5d = risk.get("volatility_5d", 0)
    change_5d = risk.get("change_5d", 0)

    risk_signals.append({"indicator": "近20日最大回撤", "value": f"-{max_dd:.1f}%", "signal": "warning" if max_dd > 10 else "neutral"})
    risk_signals.append({"indicator": "5日波动率", "value": f"{vol_5d:.1f}%", "signal": "warning" if vol_5d > 50 else "neutral"})
    risk_signals.append({"indicator": "5日涨跌幅", "value": f"{change_5d:+.1f}%", "signal": "warning" if abs(change_5d) > 15 else "neutral"})

    risk_score = 0
    if max_dd > 20: risk_score += 2; risk_conf += 0.10
    elif max_dd > 10: risk_score += 1; risk_conf += 0.05
    if vol_5d > 50: risk_score += 1; risk_conf += 0.05
    if change_5d < -15: risk_score += 1.5; risk_conf += 0.10
    elif change_5d > 20: risk_score += 0.5; risk_conf += 0.03
    if vol_ratio > 3: risk_score += 0.5

    risk_reasoning = f"近20日最大回撤{max_dd:.1f}%，5日波动率{vol_5d:.1f}%，5日涨跌幅{change_5d:+.1f}%"

    if risk_score >= 3:
        risk_direction = "bearish"
        risk_reasoning += f"。综合风险评分{risk_score:.1f}，风险等级偏高，多项指标触发预警"
    elif risk_score >= 1.5:
        risk_direction = "bearish"
        risk_reasoning += f"。风险评分{risk_score:.1f}，风险等级中等，需关注回撤和波动"
    else:
        risk_reasoning += f"。风险评分{risk_score:.1f}，风险等级可控"

    if swing_5d > 15:
        risk_reasoning += f"，5日振幅{swing_5d:.1f}%短线波动较大"
    if vol_ratio > 2:
        risk_reasoning += f"，量比{vol_ratio:.1f}倍成交放量"
    risk_conf = min(0.85, risk_conf)

    # --- 4. 宏观周期Agent ---
    macro_direction = "neutral"
    macro_conf = 0.50
    macro_reasoning = "宏观经济环境分析"
    macro_signals = []

    pmi = macro.get("pmi")
    cpi = macro.get("cpi")
    lpr_1y = macro.get("lpr_1y")
    pmi_date = macro.get("pmi_date", "")
    cpi_date = macro.get("cpi_date", "")

    if pmi:
        macro_signals.append({"indicator": f"PMI({pmi_date})", "value": f"{pmi}%", "signal": "bullish" if pmi > 50.5 else ("bearish" if pmi < 49.5 else "neutral")})
        if pmi > 51:
            macro_direction = "bullish"
            macro_conf = 0.58
            macro_reasoning = f"PMI {pmi}（{pmi_date}）处于荣枯线上方，经济扩张动能偏强"
        elif pmi > 50:
            macro_reasoning = f"PMI {pmi}（{pmi_date}）处于荣枯线附近，经济扩张但动能偏弱"
        else:
            macro_direction = "bearish"
            macro_conf = 0.58
            macro_reasoning = f"PMI {pmi}（{pmi_date}）跌破荣枯线，经济收缩压力增大"

    if cpi:
        macro_signals.append({"indicator": f"CPI({cpi_date})", "value": f"{cpi}%", "signal": "neutral"})
        macro_reasoning += f"。CPI同比{cpi}%，通胀水平"

    if lpr_1y:
        macro_signals.append({"indicator": "LPR(1年)", "value": f"{lpr_1y}%", "signal": "neutral"})
        macro_reasoning += f"。1年期LPR {lpr_1y}%"

    data_source = macro.get("data_source", "cached")
    if data_source == "cached":
        macro_reasoning += "（部分宏观数据为最近公开值，非实时更新）"

    # --- 5. 行业景气Agent ---
    ind_direction = "neutral"
    ind_conf = 0.50
    ind_reasoning = "行业板块分析"
    ind_signals = []

    if sectors:
        top3 = sectors[:3]
        ind_reasoning = f"今日行业板块涨跌排名前3："
        for s in top3:
            ind_reasoning += f"{s.get('name', '')}({s.get('change_pct', 0):+.2f}%) "
        ind_signals.append({"indicator": "板块数", "value": f"{len(sectors)}个", "signal": "neutral"})

        # 检查是否有个股所在的板块排名靠前
        avg_chg = sum(s.get("change_pct") or 0 for s in sectors[:10]) / min(10, len(sectors))
        if avg_chg > 1:
            ind_direction = "bullish"
            ind_conf = 0.58
            ind_signals.append({"indicator": "前10板块均涨幅", "value": f"+{avg_chg:.2f}%", "signal": "bullish"})
        elif avg_chg < -1:
            ind_direction = "bearish"
            ind_conf = 0.58
            ind_signals.append({"indicator": "前10板块均涨幅", "value": f"{avg_chg:.2f}%", "signal": "bearish"})
    else:
        ind_reasoning = "行业板块数据暂不可用"

    # 龙虎榜
    if billboard:
        ind_signals.append({"indicator": "龙虎榜", "value": f"{len(billboard)}条", "signal": "neutral"})

    # --- 6. 舆情情感Agent（简化版） ---
    news_direction = "neutral"
    news_conf = 0.50
    news_reasoning = "舆情情感分析（基于公开信息综合判断）"
    news_signals = []

    # 综合判断舆情方向
    positive_factors = 0
    negative_factors = 0

    if change_pct > 5:
        positive_factors += 1
        news_signals.append({"indicator": "股价大涨", "value": f"+{change_pct:.1f}%", "signal": "bullish"})
    elif change_pct < -5:
        negative_factors += 1
        news_signals.append({"indicator": "股价大跌", "value": f"{change_pct:.1f}%", "signal": "bearish"})

    if main_net > 1e8:
        positive_factors += 1
        news_signals.append({"indicator": "主力大额流入", "value": f"+{main_net/1e8:.1f}亿", "signal": "bullish"})
    elif main_net < -1e8:
        negative_factors += 1
        news_signals.append({"indicator": "主力大额流出", "value": f"{main_net/1e8:.1f}亿", "signal": "bearish"})

    if vol_ratio > 2.5:
        positive_factors += 1
        news_signals.append({"indicator": "放量异动", "value": f"{vol_ratio:.1f}倍", "signal": "bullish"})

    if consec_up >= 3:
        positive_factors += 1
    elif consec_down >= 3:
        negative_factors += 1

    org_part = quote.get("org_participate")
    if org_part:
        if org_part > 0.6:
            positive_factors += 1
            news_signals.append({"indicator": "机构参与度", "value": f"{org_part:.1%}", "signal": "bullish"})
        else:
            news_signals.append({"indicator": "机构参与度", "value": f"{org_part:.1%}", "signal": "neutral"})

    focus = quote.get("focus")
    if focus:
        news_signals.append({"indicator": "关注度", "value": f"{focus:.0f}", "signal": "neutral"})

    total_score = quote.get("total_score")
    if total_score:
        if total_score > 70:
            positive_factors += 1
            news_signals.append({"indicator": "综合评分", "value": f"{total_score:.0f}", "signal": "bullish"})
        elif total_score < 40:
            negative_factors += 1
            news_signals.append({"indicator": "综合评分", "value": f"{total_score:.0f}", "signal": "bearish"})
        else:
            news_signals.append({"indicator": "综合评分", "value": f"{total_score:.0f}", "signal": "neutral"})

    if positive_factors > negative_factors + 1:
        news_direction = "bullish"
        news_conf = 0.58
        news_reasoning = f"正面因素较多（{positive_factors}个 vs {negative_factors}个），市场情绪偏乐观"
    elif negative_factors > positive_factors + 1:
        news_direction = "bearish"
        news_conf = 0.58
        news_reasoning = f"负面因素较多（{negative_factors}个 vs {positive_factors}个），市场情绪偏悲观"
    else:
        news_reasoning = f"多空因素均衡（正面{positive_factors}个 vs 负面{negative_factors}个），市场情绪中性"

    # --- 7. 财务分析Agent（基于公开财务数据） ---
    fin_direction = "neutral"
    fin_conf = 0.50
    fin_reasoning = ""
    fin_signals = []

    if pe is not None:
        if pe < 0:
            fin_signals.append({"indicator": "PE(TTM)", "value": "亏损", "signal": "bearish"})
            fin_reasoning = f"公司当前处于亏损状态（PE为负），无盈利支撑估值"
            fin_direction = "bearish"
            fin_conf = 0.55
        elif pe < 20:
            fin_signals.append({"indicator": "PE(TTM)", "value": f"{pe:.1f}", "signal": "bullish"})
            fin_reasoning = f"PE {pe:.1f}倍，估值相对合理偏低，具备安全边际"
            fin_direction = "bullish"
            fin_conf = 0.58
        elif pe < 50:
            fin_signals.append({"indicator": "PE(TTM)", "value": f"{pe:.1f}", "signal": "neutral"})
            fin_reasoning = f"PE {pe:.1f}倍，估值处于中等水平"
        else:
            fin_signals.append({"indicator": "PE(TTM)", "value": f"{pe:.1f}", "signal": "warning"})
            fin_reasoning = f"PE {pe:.1f}倍，估值偏高，需关注业绩增长能否消化估值"
            fin_direction = "bearish"
            fin_conf = 0.55
    else:
        fin_reasoning = "PE数据暂不可用"

    pb = quote.get("pb")
    if pb is not None:
        fin_signals.append({"indicator": "PB", "value": f"{pb:.2f}", "signal": "warning" if pb > 10 else "neutral"})
        if pe is None or pe < 0:
            fin_reasoning += f"，PB {pb:.2f}倍"
        elif pb > 10:
            fin_reasoning += f"，PB {pb:.2f}倍处于高位"

    if market_cap:
        cap_str = f"{market_cap}亿" if isinstance(market_cap, float) else str(market_cap)
        fin_signals.append({"indicator": "总市值", "value": cap_str, "signal": "neutral"})
        fin_reasoning += f"。总市值{cap_str}，"

    fin_signals.append({"indicator": "换手率", "value": f"{turnover:.1f}%", "signal": "neutral" if turnover < 10 else "warning"})

    if turnover > 10:
        fin_reasoning += f"换手率{turnover:.1f}%偏高，短线博弈氛围浓厚"
    elif turnover > 5:
        fin_reasoning += f"换手率{turnover:.1f}%，市场关注度较高"
    else:
        fin_reasoning += f"换手率{turnover:.1f}%，交投相对清淡"

    # 财务数据补充
    finance_data = market_data.get("finance", {})
    if finance_data:
        eps = finance_data.get("basic_eps")
        roe = finance_data.get("roe")
        revenue_yoy = finance_data.get("total_revenue_yoy")
        if eps is not None:
            fin_signals.append({"indicator": "EPS", "value": f"{eps:.2f}", "signal": "bullish" if eps > 0 else "bearish"})
            fin_reasoning += f"。EPS {eps:.2f}元"
        if roe is not None:
            fin_signals.append({"indicator": "ROE", "value": f"{roe:.1f}%", "signal": "bullish" if roe > 15 else "neutral"})
            fin_reasoning += f"，ROE {roe:.1f}%"
        if revenue_yoy is not None:
            fin_signals.append({"indicator": "营收同比", "value": f"{revenue_yoy:+.1f}%", "signal": "bullish" if revenue_yoy > 0 else "bearish"})
            fin_reasoning += f"，营收同比增长{revenue_yoy:+.1f}%"

    # === 汇总 7 Agent ===
    agent_signals = [
        {"name": "技术指标Agent", "direction": tech_direction, "confidence": tech_conf,
         "reasoning": tech_reasoning, "signals": tech_signals},
        {"name": "资金流向Agent", "direction": fund_direction, "confidence": fund_conf,
         "reasoning": fund_reasoning, "signals": fund_signals},
        {"name": "风险预警Agent", "direction": risk_direction, "confidence": risk_conf,
         "reasoning": risk_reasoning, "signals": risk_signals},
        {"name": "宏观周期Agent", "direction": macro_direction, "confidence": macro_conf,
         "reasoning": macro_reasoning, "signals": macro_signals},
        {"name": "行业景气Agent", "direction": ind_direction, "confidence": ind_conf,
         "reasoning": ind_reasoning, "signals": ind_signals},
        {"name": "舆情情感Agent", "direction": news_direction, "confidence": news_conf,
         "reasoning": news_reasoning, "signals": news_signals},
        {"name": "财务分析Agent", "direction": fin_direction, "confidence": fin_conf,
         "reasoning": fin_reasoning, "signals": fin_signals},
    ]

    # === 仲裁决策 ===
    bullish = sum(1 for s in agent_signals if s["direction"] == "bullish")
    bearish = sum(1 for s in agent_signals if s["direction"] == "bearish")
    neutral = sum(1 for s in agent_signals if s["direction"] == "neutral")
    avg_conf = sum(s["confidence"] for s in agent_signals) / 7

    if bullish > bearish + 1:
        final_dir = "bullish"
        decision = "BUY"
    elif bearish > bullish + 1:
        final_dir = "bearish"
        decision = "SELL"
    elif bullish == bearish:
        final_dir = "neutral"
        decision = "HOLD"
    else:
        final_dir = "bullish" if bullish > bearish else "bearish"
        decision = "HOLD"

    # 风险提示（更丰富详细）
    risks = []
    if risk_score >= 3:
        risks.append(f"综合风险评分{risk_score:.1f}（偏高），近20日最大回撤{max_dd:.1f}%，波动率{vol_5d:.1f}%，多项指标触发预警")
    elif risk_score >= 1.5:
        risks.append(f"综合风险评分{risk_score:.1f}（中等），需密切关注回撤和波动变化")
    if max_dd > 15:
        risks.append(f"近20日最大回撤{max_dd:.1f}%，技术面走弱，短期支撑位可能失守")
    elif max_dd > 10:
        risks.append(f"近20日最大回撤{max_dd:.1f}%，注意回调风险")
    if swing_5d > 15:
        risks.append(f"5日振幅{swing_5d:.1f}%，短线波动剧烈，追高风险较大")
    if pe is not None and pe > 80:
        risks.append(f"PE {pe:.0f}倍估值极高，业绩不及预期可能导致大幅回调")
    elif pe is not None and pe > 50:
        risks.append(f"PE {pe:.1f}倍估值偏高，需关注业绩增长能否消化估值")
    if pe is not None and pe < 0:
        risks.append(f"公司处于亏损状态（EPS为负），估值缺乏基本面锚定，上涨持续性存疑")
    if rzye and rzye > 0:
        risks.append(f"融资余额{rzye/1e8:.1f}亿，杠杆资金参与度较高，市场波动可能引发融资盘踩踏")
    if turnover > 15:
        risks.append(f"换手率{turnover:.1f}%极高，短线博弈氛围浓厚，需警惕获利盘出逃")
    if vol_ratio > 3:
        risks.append(f"量比{vol_ratio:.1f}倍，成交异常放量，可能存在大资金进出")
    if main_net < -5e8:
        risks.append(f"主力资金大幅净流出{abs(main_net)/1e8:.1f}亿，机构可能在出货")
    if not risks:
        risks.append("暂无明显风险信号")

    position_ratio = 0.30 if final_dir == "neutral" else (0.50 if final_dir == "bullish" else 0.10)

    # === 构建结果对象 ===
    class Result:
        pass

    result = Result()
    result.direction = final_dir
    result.decision = decision
    result.confidence = avg_conf
    result.position_ratio = position_ratio
    result.action = decision
    result.risks = risks
    result.signals_summary = {"total": 7, "bullish": bullish, "bearish": bearish, "neutral": neutral}
    result.scope_trace = {
        "executions": [
            {"agent_name": s["name"], "signal": {
                "direction": s["direction"],
                "confidence": s["confidence"],
                "reasoning": s["reasoning"],
                "signals": s["signals"],
                "meta": {"data_status": "live"},
            }} for s in agent_signals
        ]
    }
    result.meta = {"reasoning_chain": f"多空{bullish}v{bearish}，方向{final_dir}"}

    # ── 构建丰富的综合研判推理链 ──

    # 分类阵营
    bullish_agents = [s for s in agent_signals if s["direction"] == "bullish"]
    bearish_agents = [s for s in agent_signals if s["direction"] == "bearish"]
    neutral_agents = [s for s in agent_signals if s["direction"] == "neutral"]

    # 核心矛盾提炼
    contradictions = []
    if bullish_agents and bearish_agents:
        # 找最强看多和最强看空的agent对比
        best_bull = max(bullish_agents, key=lambda x: x["confidence"])
        best_bear = max(bearish_agents, key=lambda x: x["confidence"])
        contradictions.append(
            f"**{best_bull['name']}**给出看多信号（置信度{best_bull['confidence']:.0%}）vs "
            f"**{best_bear['name']}**给出看空信号（置信度{best_bear['confidence']:.0%}）"
        )
    if pe is not None:
        if pe < 0:
            contradictions.append(f"公司持续亏损（PE为负）与股价上涨形成背离" if change_pct > 0 else f"亏损状态叠加股价下跌，需警惕进一步下行风险")
        elif pe > 50:
            contradictions.append(f"PE {pe:.1f}倍处于高估值区间 vs 市场资金持续关注" if main_net > 0 else f"PE {pe:.1f}倍估值偏高，上涨需基本面支撑")
    if main_net > 0 and change_pct < 0:
        contradictions.append(f"主力资金净流入{main_net_yi:.2f}亿但股价下跌，多空分歧明显")
    elif main_net < 0 and change_pct > 0:
        contradictions.append(f"主力资金净流出但股价上涨，散户驱动特征明显")
    if rzye and rzye > 0:
        contradictions.append(f"融资余额{rzye/1e8:.2f}亿，杠杆资金参与度需关注")

    # 最终判断说明
    if decision == "BUY":
        judgment = (
            f"综合{bullish}个看多Agent的分析，当前处于偏多格局。"
            f"技术面和资金面信号积极，短期有望延续上行趋势。"
        )
        if pe is not None and pe < 0:
            judgment += f"但需注意公司当前亏损状态（PE {pe:.1f}），上涨主要受行业情绪驱动而非基本面支撑。"
        judgment += f"建议分批建仓，控制仓位在{position_ratio:.0%}以内，设置止损位。"
    elif decision == "SELL":
        judgment = (
            f"综合{bearish}个看空Agent的分析，当前风险收益比不佳。"
        )
        if max_dd > 10:
            judgment += f"近20日最大回撤已达{max_dd:.1f}%，技术面走弱信号明确。"
        if pe is not None and pe < 0:
            judgment += f"公司持续亏损，缺乏安全边际。"
        judgment += f"建议减仓至{position_ratio:.0%}以下，回避短期风险。"
    else:
        judgment = (
            f"多空力量相对均衡（看多{bullish}票 vs 看空{bearish}票），建议维持现有仓位。"
        )
        if bullish_agents and bearish_agents:
            judgment += f"看多方主要基于{'、'.join(a['name'].replace('Agent','') for a in bullish_agents[:2])}的积极信号；"
            judgment += f"看空方则关注{'、'.join(a['name'].replace('Agent','') for a in bearish_agents[:2])}的风险提示。"
        if pe is not None and pe < 0:
            judgment += f"公司处于亏损期（PE {pe:.1f}），估值难以锚定，建议以技术面和资金面为主要参考。"
        elif pe is not None and pe > 80:
            judgment += f"PE {pe:.0f}倍估值偏高，需关注业绩兑现情况。"
        judgment += f"建议仓位控制在{position_ratio:.0%}，等待方向明确后再做调整。"

    # 组装推理链
    result.reasoning_chain = "## 综合研判\n\n"

    # 核心矛盾
    if contradictions:
        result.reasoning_chain += f"**核心矛盾分析**\n\n"
        for c in contradictions[:4]:
            result.reasoning_chain += f"- {c}\n"
        result.reasoning_chain += "\n"

    # 多空力量
    result.reasoning_chain += f"**多空力量对比**: 看多 {bullish} 票 vs 看空 {bearish} 票 vs 中性 {neutral} 票\n\n"

    # 阵营渲染辅助函数
    def _render_camp(agents_list, camp_label):
        if not agents_list:
            return ""
        lines = f"### {camp_label}\n"
        for s in agents_list:
            lines += f"- **{s['name']}**: {s['reasoning']}\n"
        return lines + "\n"

    # 看多阵营
    result.reasoning_chain += _render_camp(bullish_agents, "看多阵营")
    # 看空阵营
    result.reasoning_chain += _render_camp(bearish_agents, "看空阵营")
    # 中性阵营
    result.reasoning_chain += _render_camp(neutral_agents, "中性阵营")

    # 最终判断
    result.reasoning_chain += f"### 最终判断：{decision}\n\n"
    result.reasoning_chain += f"{judgment}"

    return result, agent_signals


# ── 报告生成 ──────────────────────────────────────────────

def generate_and_save_report(stock_code, stock_name, price_data, arbitration_result, agent_signals):
    """生成 HTML 报告并保存到文件"""
    html = generate_html_report(stock_code, stock_name, price_data, arbitration_result, agent_signals)

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"report_{stock_code}_{timestamp}.html"
    filepath = reports_dir / filename

    filepath.write_text(html, encoding="utf-8")
    return str(filepath)


# ── UI 工具 ───────────────────────────────────────────────

def print_banner():
    banner = r"""
  ___  ____  ____  _  __  ___  _  _  ____  _  _  ____  ____
 / __)(  __)/ ___)/ )( \(  _)/ )( \(  _ \( \/ )(_  _)( ___)
 \__ \ ) _) \___ \) __ ( ) _) ) \/ ( ) _ (<  <  _)(_  )__)
 (___/(____)(____/\_)(_/(_)  \____/(_)(_/ \_)(_)(____)(____)

          R E N A I S S A N C E   I N T E L L
          多智能体投资决策引擎 v3.0
          数据源: 新浪财经 + 腾讯财经 + 东方财富
"""
    print(banner)


def print_result(result):
    decision_map = {
        "buy": "\033[91mBUY 建议买入\033[0m",
        "sell": "\033[92mSELL 建议卖出\033[0m",
        "hold": "\033[93mHOLD 建议持有\033[0m",
        "wait": "\033[90mWAIT 建议观望\033[0m",
    }
    print(f"\n  {'_' * 50}")
    print(f"  Decision: {decision_map.get(result.decision, result.decision)}")
    print(f"  Direction: {result.direction}  |  Confidence: {result.confidence:.1%}  |  Position: {result.position_ratio:.0%}")

    if result.risks:
        print(f"\n  Risk Alerts:")
        for risk in result.risks:
            print(f"    ! {risk}")

    summary = result.signals_summary
    print(f"\n  Signals: Total {summary.get('total', 0)} | Bullish {summary.get('bullish', 0)} | Bearish {summary.get('bearish', 0)} | Neutral {summary.get('neutral', 0)}")
    print(f"  {'_' * 50}")


def open_in_browser(filepath):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", filepath])
        elif sys.platform == "win32":
            os.startfile(filepath)
        else:
            subprocess.Popen(["xdg-open", filepath])
    except Exception:
        pass


# ── 主流程 ───────────────────────────────────────────────

def run_single_analysis(stock_code: str) -> bool:
    """执行单只股票的完整分析流程。返回是否成功。"""
    # 1. 获取全量真实数据
    market_data = get_stock_full_data(stock_code)
    quote = market_data.get("quote", {})
    stock_name = quote.get("name", stock_code)
    price = quote.get("price") or quote.get("close") or "--"
    chg = quote.get("change_pct") or 0
    sign = "+" if chg >= 0 else ""

    print(f"\n  {stock_name} ({stock_code}) -- Price: {price}  Change: {sign}{chg:.2f}%")

    if quote.get("market_cap"):
        print(f"  Market Cap: {quote['market_cap']}  |  PE: {quote.get('pe', '--')}  |  Turnover: {quote.get('turnover', '--')}%")

    # 2. 分析
    print(f"  Analyzing with 7 expert agents...\n")
    result, agent_signals = _build_analysis(market_data, stock_code, stock_name)

    # 3. 输出结果
    print_result(result)

    # 4. 生成报告
    print(f"\n  Generating report...")
    report_path = generate_and_save_report(stock_code, stock_name, quote, result, agent_signals)
    print(f"  Report saved: {report_path}")
    open_in_browser(report_path)
    return True


def interactive_mode():
    """交互式模式：输入股票名称或代码"""
    print_banner()

    while True:
        user_input = input("\n  > Enter stock name or code (q to quit): ").strip()

        if not user_input:
            continue
        if user_input.lower() in ("q", "quit", "exit"):
            print("\n  Bye!")
            break

        # 搜索/解析股票
        stock_code = user_input

        if not user_input.isdigit() or len(user_input) != 6:
            print(f"  Searching '{user_input}'...")
            match = search_stock(user_input)
            if match:
                stock_code = match["code"]
                stock_name = match["name"]
                print(f"  Found: {stock_name} ({stock_code})")
            else:
                print(f"  Not found. Please enter a valid stock code (6 digits) or name.")
                continue

        try:
            run_single_analysis(stock_code)
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            print(f"  Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="AI Renaissance - Multi-Agent Investment Analysis")
    parser.add_argument("--stock", type=str, default="", help="Stock code(s), comma separated")
    args = parser.parse_args()

    if args.stock:
        # CLI 批量模式
        codes = [c.strip() for c in args.stock.split(",")]
        for code in codes:
            # 如果是名称先搜索
            if not code.isdigit() or len(code) != 6:
                match = search_stock(code)
                if match:
                    code = match["code"]
            try:
                run_single_analysis(code)
            except Exception as e:
                print(f"  Error analyzing {code}: {e}")
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
