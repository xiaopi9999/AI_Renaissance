"""
资金流向 Agent - 专家3组

signal_type: fundflow
Skill 域: skills/fundflow/
核心能力：主力资金追踪、北向资金、聪明钱动向

数据源（全部 AkShare 实时）：
  - stock_individual_fund_flow: 个股资金流向（主力/超大/大/中/小单）
  - stock_hsgt_individual_em: 北向资金个股持仓
  - stock_margin_detail_sse/szse: 融资融券
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from agents.base import BaseAgent
from agents.signal import Signal, bullish_signal, bearish_signal, neutral_signal
from loguru import logger

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False


class FundflowAgent(BaseAgent):
    """资金流向 Agent（专家3组）— 全 AkShare 实时数据"""

    signal_type = "fundflow"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="资金流向Agent", config=config or {})
        self.load_skills_from_domain("fundflow")
        self.load_skills_from_domain("data")

    def analyze(self, stock_code: str) -> Signal:
        self.log(f"开始资金流向分析：{stock_code}")

        if not HAS_AKSHARE:
            return neutral_signal(
                confidence=0.1,
                reasoning="AkShare 未安装，无法获取资金流向数据",
                source=self.name, stock_code=stock_code, signal_type=self.signal_type,
            )

        market = self._detect_market(stock_code)
        raw = self._fetch_all_data(stock_code, market)
        meta = {
            "data_status": "live",
            "data_timestamp": datetime.now().isoformat(),
            **raw,
        }

        direction, confidence, signals, reasoning = self._evaluate(raw)

        return Signal(
            direction=direction, confidence=confidence,
            reasoning=reasoning, signals=signals,
            source=self.name, stock_code=stock_code,
            signal_type=self.signal_type, meta=meta,
        )

    # ── 数据获取 ──────────────────────────────────────────────

    @staticmethod
    def _detect_market(code: str) -> str:
        """根据代码前缀判断市场"""
        if code.startswith(("6",)):
            return "sh"
        if code.startswith(("0", "3")):
            return "sz"
        if code.startswith("688") or code.startswith("689"):
            return "sh"
        return "sh"

    def _fetch_all_data(self, stock_code: str, market: str) -> Dict[str, Any]:
        """获取全部资金流向数据"""
        data: Dict[str, Any] = {}

        # 1) 个股资金流向
        data["fund_flow"] = self._fetch_fund_flow(stock_code, market)

        # 2) 北向资金个股
        data["northbound"] = self._fetch_northbound(stock_code)

        # 3) 融资融券
        data["margin"] = self._fetch_margin(stock_code, market)

        return data

    def _fetch_fund_flow(self, stock_code: str, market: str) -> Dict[str, Any]:
        """个股资金流向（最近 N 日）"""
        try:
            df = ak.stock_individual_fund_flow(stock=stock_code, market=market)
            if df is None or df.empty:
                self.log("个股资金流向数据为空")
                return {"latest": {}, "recent_5d": []}

            # 取最近 5 个交易日
            recent = df.head(5).to_dict("records") if len(df) >= 5 else df.to_dict("records")
            latest = recent[0] if recent else {}

            self.log(f"获取个股资金流向成功，最新日期: {latest.get('日期', 'N/A')}")
            return {"latest": latest, "recent_5d": recent}
        except Exception as e:
            self.log(f"个股资金流向获取失败: {e}", "error")
            return {"latest": {}, "recent_5d": []}

    def _fetch_northbound(self, stock_code: str) -> Dict[str, Any]:
        """北向资金个股持仓"""
        try:
            df = ak.stock_hsgt_individual_em(symbol=stock_code)
            if df is None or df.empty:
                self.log("北向资金个股数据为空")
                return {"latest": {}, "recent": []}

            recent = df.head(5).to_dict("records") if len(df) >= 5 else df.to_dict("records")
            latest = recent[0] if recent else {}
            self.log(f"获取北向资金个股数据成功")
            return {"latest": latest, "recent": recent}
        except Exception as e:
            self.log(f"北向资金获取失败: {e}", "error")
            return {"latest": {}, "recent": []}

    def _fetch_margin(self, stock_code: str, market: str) -> Dict[str, Any]:
        """融资融券数据"""
        try:
            if market == "sh":
                df = ak.stock_margin_detail_sse(symbol=stock_code)
            else:
                df = ak.stock_margin_detail_szse(symbol=stock_code)
            if df is None or df.empty:
                self.log("融资融券数据为空")
                return {"latest": {}}
            latest = df.iloc[0].to_dict()
            self.log(f"获取融资融券数据成功")
            return {"latest": latest}
        except TypeError as e:
            # 某些版本参数名不同
            try:
                if market == "sh":
                    df = ak.stock_margin_detail_sse()
                else:
                    df = ak.stock_margin_detail_szse()
                if df is not None and not df.empty:
                    code_col = [c for c in df.columns if "代码" in c]
                    if code_col:
                        df = df[df[code_col[0]].astype(str) == stock_code]
                        if not df.empty:
                            return {"latest": df.iloc[0].to_dict()}
            except Exception:
                pass
            self.log(f"融资融券获取失败: {e}", "error")
            return {"latest": {}}

    # ── 评估逻辑 ──────────────────────────────────────────────

    def _evaluate(self, data: Dict[str, Any]):
        """
        综合评估资金流向

        Returns:
            (direction, confidence, signals, reasoning)
        """
        signals: List[str] = []
        score = 0.0  # 正=看多，负=看空

        # ─ 1. 个股资金流向 ─
        ff = data.get("fund_flow", {})
        latest = ff.get("latest", {})
        recent_5d = ff.get("recent_5d", [])

        if latest:
            # 主力净流入（不同版本 AkShare 字段名可能不同）
            main_net = self._safe_float(latest, "主力净流入-净额")
            if main_net == 0:
                main_net = self._safe_float(latest, "主力净流入")

            if main_net > 0:
                score += min(main_net / 1e8, 2.0)  # 净流入超 2 亿封顶 +2
                signals.append(f"主力净流入 {main_net / 1e8:.2f} 亿")
            elif main_net < 0:
                score += max(main_net / 1e8, -2.0)
                signals.append(f"主力净流出 {abs(main_net) / 1e8:.2f} 亿")

            # 连续性判断
            if len(recent_5d) >= 3:
                consecutive = 0
                for rec in recent_5d[:3]:
                    val = self._safe_float(rec, "主力净流入-净额")
                    if val == 0:
                        val = self._safe_float(rec, "主力净流入")
                    if val > 0:
                        if consecutive >= 0:
                            consecutive += 1
                        else:
                            consecutive = 1
                    elif val < 0:
                        if consecutive <= 0:
                            consecutive -= 1
                        else:
                            consecutive = -1

                if consecutive >= 3:
                    score += 1.0
                    signals.append("主力资金连续 3 日净流入")
                elif consecutive <= -3:
                    score -= 1.0
                    signals.append("主力资金连续 3 日净流出")

        # ─ 2. 北向资金 ─
        nb = data.get("northbound", {})
        nb_latest = nb.get("latest", {})

        if nb_latest:
            # 持股市值变化
            hold_val = self._safe_float(nb_latest, "持股市值")
            hold_ratio = self._safe_float(nb_latest, "持股占比")
            net_buy = self._safe_float(nb_latest, "当日净买入")

            if net_buy > 0:
                score += 0.5
                signals.append(f"北向资金净买入 {net_buy / 1e8:.2f} 亿")
            elif net_buy < 0:
                score -= 0.5
                signals.append(f"北向资金净卖出 {abs(net_buy) / 1e8:.2f} 亿")

            if hold_ratio > 0:
                signals.append(f"北向持股占比 {hold_ratio:.2f}%")

        # ─ 3. 融资融券 ─
        mg = data.get("margin", {})
        mg_latest = mg.get("latest", {})

        if mg_latest:
            # 融资余额变化
            rzye = self._safe_float(mg_latest, "融资余额")
            rzmre = self._safe_float(mg_latest, "融资买入额")
            rqyl = self._safe_float(mg_latest, "融券余量")

            if rzmre > 0:
                signals.append(f"融资买入额 {rzmre / 1e8:.2f} 亿")

        # ─ 方向判定 ─
        if score >= 1.5:
            direction = "bullish"
            confidence = min(0.5 + abs(score) * 0.1, 0.9)
        elif score <= -1.5:
            direction = "bearish"
            confidence = min(0.5 + abs(score) * 0.1, 0.9)
        else:
            direction = "neutral"
            confidence = min(0.3 + abs(score) * 0.05, 0.5)

        if not signals:
            signals = ["资金流向数据不足，无法形成明确判断"]
            confidence = 0.1

        reasoning = self._build_reasoning(direction, score, signals)
        return direction, confidence, signals, reasoning

    @staticmethod
    def _safe_float(record: Dict[str, Any], key: str) -> float:
        """安全取浮点值"""
        val = record.get(key)
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _build_reasoning(direction: str, score: float, signals: List[str]) -> str:
        dir_map = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
        parts = [f"资金流向综合评分: {score:+.2f}，方向判定: {dir_map.get(direction, direction)}"]
        parts.append("。".join(signals[:5]) + "。")
        return "；".join(parts)
