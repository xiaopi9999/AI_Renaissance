"""
风险预警 Agent - 专家7组

signal_type: risk
Skill 域: skills/risk/
核心能力：尾部风险识别、仓位上限、守住不爆仓的底线

数据源（全部 AkShare 实时）：
  - stock_zh_a_hist: 个股日K线 → 回撤、波动率、连续涨跌
  - stock_individual_fund_flow: 主力资金动向
  - stock_individual_info_em: 个股状态（ST、退市等）
"""

from typing import Optional, Dict, Any, List
from datetime import datetime

from agents.base import BaseAgent
from agents.signal import Signal, bullish_signal, bearish_signal, neutral_signal
from loguru import logger

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False


class RiskAgent(BaseAgent):
    """风险预警 Agent（专家7组）— 全 AkShare 实时数据"""

    signal_type = "risk"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="风险预警Agent", config=config or {})
        self.load_skills_from_domain("risk")

    def analyze(self, stock_code: str) -> Signal:
        self.log(f"开始风险预警分析：{stock_code}")

        if not HAS_AKSHARE:
            return neutral_signal(
                confidence=0.1,
                reasoning="AkShare 未安装，无法进行风险分析",
                source=self.name, stock_code=stock_code, signal_type=self.signal_type,
            )

        raw = self._fetch_all_data(stock_code)
        meta = {"data_status": "live", **raw}

        direction, confidence, signals, reasoning, risks = self._evaluate(raw)

        return Signal(
            direction=direction, confidence=confidence,
            reasoning=reasoning, signals=signals,
            source=self.name, stock_code=stock_code,
            signal_type=self.signal_type, meta={
                **meta,
                "risk_warnings": risks,
            },
        )

    # ── 数据获取 ──────────────────────────────────────────────

    def _fetch_all_data(self, stock_code: str) -> Dict[str, Any]:
        data: Dict[str, Any] = {}

        # 1) 日K线（60 日）
        data["kline"] = self._fetch_kline(stock_code)

        # 2) 个股资金流向
        market = "sh" if stock_code.startswith(("6", "688", "689")) else "sz"
        data["fund_flow"] = self._fetch_fund_flow(stock_code, market)

        # 3) 个股基本信息（ST 状态等）
        data["stock_info"] = self._fetch_stock_info(stock_code)

        return data

    def _fetch_kline(self, stock_code: str) -> Dict[str, Any]:
        """获取日K线数据"""
        try:
            df = ak.stock_zh_a_hist(
                symbol=stock_code, period="daily",
                start_date=(datetime.now().replace(month=1, day=1)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="qfq",
            )
            if df is None or df.empty:
                self.log("日K线数据为空")
                return {"rows": [], "metrics": {}}

            rows = df.tail(60).to_dict("records")
            metrics = self._compute_risk_metrics(rows)
            self.log(f"获取日K线成功，{len(rows)} 根K线，最大回撤: {metrics.get('max_drawdown', 0):.2f}%")
            return {"rows": rows, "metrics": metrics}
        except Exception as e:
            self.log(f"日K线获取失败: {e}", "error")
            return {"rows": [], "metrics": {}}

    def _fetch_fund_flow(self, stock_code: str, market: str) -> Dict[str, Any]:
        """个股资金流向"""
        try:
            df = ak.stock_individual_fund_flow(stock=stock_code, market=market)
            if df is None or df.empty:
                return {"latest": {}}
            latest = df.iloc[0].to_dict()
            return {"latest": latest}
        except Exception as e:
            self.log(f"资金流向获取失败: {e}", "error")
            return {"latest": {}}

    def _fetch_stock_info(self, stock_code: str) -> Dict[str, Any]:
        """个股基本信息"""
        try:
            df = ak.stock_individual_info_em(symbol=stock_code)
            if df is None or df.empty:
                return {}
            info = {}
            for _, row in df.iterrows():
                info[str(row.iloc[0])] = row.iloc[1]
            return info
        except Exception as e:
            self.log(f"个股信息获取失败: {e}", "error")
            return {}

    # ── 风险指标计算 ──────────────────────────────────────────

    @staticmethod
    def _compute_risk_metrics(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        """从K线数据中计算风险指标"""
        metrics: Dict[str, float] = {}

        if len(rows) < 5:
            return metrics

        closes = [float(r.get("收盘", r.get("收盘价", 0))) for r in rows]
        highs = [float(r.get("最高", r.get("最高价", 0))) for r in rows]
        lows = [float(r.get("最低", r.get("最低价", 0))) for r in rows]
        volumes = [float(r.get("成交量", 0)) for r in rows]

        # 1. 最大回撤（近 20 日）
        window = min(20, len(closes))
        recent_closes = closes[-window:]
        peak = recent_closes[0]
        max_dd = 0.0
        for c in recent_closes:
            if c > peak:
                peak = c
            dd = (peak - c) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        metrics["max_drawdown"] = max_dd * 100

        # 2. 近 5 日波动率
        if len(closes) >= 6:
            daily_returns = [
                (closes[i] - closes[i - 1]) / closes[i - 1]
                for i in range(max(1, len(closes) - 5), len(closes))
                if closes[i - 1] > 0
            ]
            if daily_returns:
                avg_ret = sum(daily_returns) / len(daily_returns)
                variance = sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns)
                metrics["volatility_5d"] = (variance ** 0.5) * 100

        # 3. 连续涨跌天数
        consecutive_down = 0
        consecutive_up = 0
        for i in range(len(closes) - 1, max(0, len(closes) - 10), -1):
            chg = closes[i] - closes[i - 1]
            if chg < 0 and consecutive_up == 0:
                consecutive_down += 1
            elif chg > 0 and consecutive_down == 0:
                consecutive_up += 1
            else:
                break
        metrics["consecutive_down"] = consecutive_down
        metrics["consecutive_up"] = consecutive_up

        # 4. 成交量异常（与 20 日均量比较）
        if len(volumes) >= 20:
            avg_vol = sum(volumes[-20:]) / 20
            if avg_vol > 0:
                latest_vol = volumes[-1]
                metrics["volume_ratio"] = latest_vol / avg_vol
            else:
                metrics["volume_ratio"] = 1.0
        elif len(volumes) > 0:
            metrics["volume_ratio"] = 1.0

        # 5. 近 5 日涨跌幅
        if len(closes) >= 5:
            chg_5d = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0
            metrics["change_5d"] = chg_5d

        return metrics

    # ── 评估逻辑 ──────────────────────────────────────────────

    def _evaluate(self, data: Dict[str, Any]):
        signals: List[str] = []
        risks: List[str] = []
        risk_score = 0.0  # 正=安全，负=危险

        # ─ 1. ST 状态检查 ─
        stock_info = data.get("stock_info", {})
        stock_name = stock_info.get("股票简称", "")
        if stock_name and "ST" in stock_name.upper():
            risk_score -= 3.0
            risks.append(f"股票被标记为 ST（{stock_name}），重大风险")
            signals.append("ST 股票，属于高风险标的")

        # ─ 2. K线风险指标 ─
        kline_data = data.get("kline", {})
        metrics = kline_data.get("metrics", {})

        max_dd = metrics.get("max_drawdown", 0)
        if max_dd > 20:
            risk_score -= 2.0
            risks.append(f"近 20 日最大回撤 {max_dd:.1f}%，已超过 20% 警戒线")
            signals.append(f"最大回撤: {max_dd:.1f}%")
        elif max_dd > 10:
            risk_score -= 1.0
            risks.append(f"近 20 日最大回撤 {max_dd:.1f}%，需要关注")
            signals.append(f"最大回撤: {max_dd:.1f}%")

        volatility = metrics.get("volatility_5d", 0)
        if volatility > 5:
            risk_score -= 1.0
            risks.append(f"近 5 日波动率 {volatility:.2f}%，高波动风险")
            signals.append(f"波动率: {volatility:.2f}%")

        consecutive_down = metrics.get("consecutive_down", 0)
        if consecutive_down >= 5:
            risk_score -= 1.5
            risks.append(f"连续下跌 {consecutive_down} 天，市场情绪恶化")
            signals.append(f"连续下跌 {consecutive_down} 天")
        elif consecutive_down >= 3:
            risk_score -= 0.5
            signals.append(f"连续下跌 {consecutive_down} 天")

        consecutive_up = metrics.get("consecutive_up", 0)
        if consecutive_up >= 5:
            risk_score += 0.5
            signals.append(f"连续上涨 {consecutive_up} 天，短期过热风险")

        volume_ratio = metrics.get("volume_ratio", 1.0)
        if volume_ratio > 3.0:
            risk_score -= 0.5
            risks.append(f"成交量较 20 日均量放大 {volume_ratio:.1f} 倍，可能存在异动")
            signals.append(f"放量 {volume_ratio:.1f}x")
        elif volume_ratio < 0.3:
            signals.append(f"缩量至均量 {volume_ratio:.1f}x，市场参与度低")

        change_5d = metrics.get("change_5d", 0)
        if change_5d < -15:
            risk_score -= 1.5
            risks.append(f"近 5 日跌幅 {change_5d:.1f}%，短期暴跌风险")
        elif change_5d > 20:
            risk_score -= 0.5
            risks.append(f"近 5 日涨幅 {change_5d:.1f}%，短期追高风险")

        # ─ 3. 主力资金 ─
        ff_data = data.get("fund_flow", {})
        ff_latest = ff_data.get("latest", {})
        main_net = self._safe_float(ff_latest, "主力净流入-净额")
        if main_net == 0:
            main_net = self._safe_float(ff_latest, "主力净流入")

        if main_net < -5e8:
            risk_score -= 1.0
            risks.append(f"主力单日净流出 {abs(main_net) / 1e8:.2f} 亿，资金大幅撤离")
            signals.append(f"主力净流出: {abs(main_net) / 1e8:.2f} 亿")

        # ─ 方向判定 ─
        if risk_score <= -3:
            direction = "bearish"
            confidence = min(0.7 + abs(risk_score) * 0.05, 0.95)
        elif risk_score <= -1.5:
            direction = "bearish"
            confidence = min(0.5 + abs(risk_score) * 0.08, 0.8)
        elif risk_score >= 1:
            direction = "bullish"
            confidence = min(0.4 + risk_score * 0.05, 0.6)
        else:
            direction = "neutral"
            confidence = min(0.3 + abs(risk_score) * 0.05, 0.5)

        if not signals:
            signals = ["K线数据不足，无法进行完整风险评估"]
            confidence = 0.1

        reasoning = f"风险综合评分: {risk_score:+.1f}；" + "；".join(signals[:6])
        return direction, confidence, signals, reasoning, risks

    @staticmethod
    def _safe_float(record: Dict[str, Any], key: str) -> float:
        val = record.get(key)
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
