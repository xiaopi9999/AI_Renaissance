"""
行业景气 Agent - 专家5组

signal_type: industry
Skill 域: skills/industry/
核心能力：产业链景气度、行业拐点、竞争格局

数据源（全部 AkShare 实时）：
  - stock_individual_info_em: 个股基本信息（所属行业）
  - stock_sector_fund_flow_rank: 行业板块资金流排名
  - stock_board_industry_name_em / spot_em: 行业板块涨跌
  - stock_fund_flow_industry: 行业资金流向汇总
"""

from typing import Optional, Dict, Any, List

from agents.base import BaseAgent
from agents.signal import Signal, bullish_signal, bearish_signal, neutral_signal
from loguru import logger

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False


class IndustryAgent(BaseAgent):
    """行业景气 Agent（专家5组）— 全 AkShare 实时数据"""

    signal_type = "industry"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="行业景气Agent", config=config or {})
        self.load_skills_from_domain("industry")

    def analyze(self, stock_code: str) -> Signal:
        self.log(f"开始行业景气分析：{stock_code}")

        if not HAS_AKSHARE:
            return neutral_signal(
                confidence=0.1,
                reasoning="AkShare 未安装，无法获取行业数据",
                source=self.name, stock_code=stock_code, signal_type=self.signal_type,
            )

        raw = self._fetch_all_data(stock_code)
        meta = {"data_status": "live", **raw}

        direction, confidence, signals, reasoning = self._evaluate(raw, stock_code)

        return Signal(
            direction=direction, confidence=confidence,
            reasoning=reasoning, signals=signals,
            source=self.name, stock_code=stock_code,
            signal_type=self.signal_type, meta=meta,
        )

    # ── 数据获取 ──────────────────────────────────────────────

    def _fetch_all_data(self, stock_code: str) -> Dict[str, Any]:
        """获取行业相关数据"""
        data: Dict[str, Any] = {}

        # 1) 个股基本信息 → 所属行业
        data["stock_info"] = self._fetch_stock_info(stock_code)

        # 2) 行业板块资金流排名
        data["sector_flow_rank"] = self._fetch_sector_flow_rank()

        # 3) 行业板块列表 + 涨跌
        data["industry_boards"] = self._fetch_industry_boards()

        return data

    def _fetch_stock_info(self, stock_code: str) -> Dict[str, Any]:
        """个股基本信息（含所属行业）"""
        try:
            df = ak.stock_individual_info_em(symbol=stock_code)
            if df is None or df.empty:
                self.log("个股基本信息为空")
                return {}

            info = {}
            for _, row in df.iterrows():
                info[str(row.iloc[0])] = row.iloc[1]

            self.log(f"个股所属行业: {info.get('行业', 'N/A')}")
            return info
        except Exception as e:
            self.log(f"个股基本信息获取失败: {e}", "error")
            return {}

    def _fetch_sector_flow_rank(self) -> List[Dict[str, Any]]:
        """行业板块资金流向排名"""
        try:
            df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
            if df is None or df.empty:
                self.log("行业资金流排名为空")
                return []

            records = df.head(20).to_dict("records")
            self.log(f"获取行业资金流排名成功，前 20 行业")
            return records
        except Exception as e:
            self.log(f"行业资金流排名获取失败: {e}", "error")
            return []

    def _fetch_industry_boards(self) -> List[Dict[str, Any]]:
        """行业板块列表与涨跌"""
        try:
            df = ak.stock_board_industry_name_em()
            if df is None or df.empty:
                self.log("行业板块列表为空")
                return []

            records = df.to_dict("records")
            self.log(f"获取行业板块列表成功，共 {len(records)} 个板块")
            return records
        except Exception as e:
            self.log(f"行业板块列表获取失败: {e}", "error")
            return []

    # ── 评估逻辑 ──────────────────────────────────────────────

    def _evaluate(self, data: Dict[str, Any], stock_code: str):
        signals: List[str] = []
        score = 0.0

        stock_info = data.get("stock_info", {})
        industry_name = stock_info.get("行业", "")

        if not industry_name:
            return "neutral", 0.1, ["无法识别所属行业"], "无法获取个股所属行业信息"

        signals.append(f"所属行业: {industry_name}")

        # ─ 1. 在行业资金流排名中查找 ─
        flow_rank = data.get("sector_flow_rank", [])
        industry_rank_info = self._find_in_rank(industry_name, flow_rank)

        if industry_rank_info:
            net_inflow = self._safe_float(industry_rank_info, "今日主力净流入-净额")
            if net_inflow == 0:
                net_inflow = self._safe_float(industry_rank_info, "今日主力净流入")
            rank = self._safe_float(industry_rank_info, "排名")

            if net_inflow > 0:
                score += min(net_inflow / 5e8, 1.5)
                signals.append(f"行业主力净流入 {net_inflow / 1e8:.2f} 亿")
            elif net_inflow < 0:
                score += max(net_inflow / 5e8, -1.5)
                signals.append(f"行业主力净流出 {abs(net_inflow) / 1e8:.2f} 亿")

            if rank > 0 and rank <= 10:
                score += 0.5
                signals.append(f"行业资金流入排名第 {int(rank)} 名")
            elif rank > 0:
                signals.append(f"行业资金流入排名第 {int(rank)} 名")

        # ─ 2. 在行业板块列表中查找涨跌 ─
        boards = data.get("industry_boards", [])
        board_info = self._find_in_boards(industry_name, boards)

        if board_info:
            change_pct = self._safe_float(board_info, "涨跌幅")
            signals.append(f"行业板块涨跌幅: {change_pct:.2f}%")

            if change_pct > 2:
                score += 1.0
                signals.append("行业板块表现强势（涨幅>2%）")
            elif change_pct > 0:
                score += 0.3
            elif change_pct < -2:
                score -= 1.0
                signals.append("行业板块表现弱势（跌幅>2%）")
            elif change_pct < 0:
                score -= 0.3

            # 领涨/领跌股
            top_stock = board_info.get("领涨股票", board_info.get("今日领涨股票", ""))
            if top_stock:
                signals.append(f"行业领涨: {top_stock}")

        # ─ 方向判定 ─
        if score >= 1.0:
            direction = "bullish"
            confidence = min(0.5 + abs(score) * 0.12, 0.85)
        elif score <= -1.0:
            direction = "bearish"
            confidence = min(0.5 + abs(score) * 0.12, 0.85)
        else:
            direction = "neutral"
            confidence = min(0.3 + abs(score) * 0.08, 0.5)

        if len(signals) <= 1:
            signals.append("行业景气数据有限")
            confidence = 0.1

        reasoning = f"行业景气综合评分: {score:+.2f}；{'；'.join(signals[:6])}"
        return direction, confidence, signals, reasoning

    @staticmethod
    def _find_in_rank(industry_name: str, rank_list: List[Dict]) -> Optional[Dict]:
        """在资金流排名中模糊匹配行业"""
        for item in rank_list:
            name = str(item.get("名称", item.get("行业", "")))
            if industry_name in name or name in industry_name:
                return item
        return None

    @staticmethod
    def _find_in_boards(industry_name: str, boards: List[Dict]) -> Optional[Dict]:
        """在板块列表中模糊匹配行业"""
        for item in boards:
            name = str(item.get("板块名称", item.get("行业", "")))
            if industry_name in name or name in industry_name:
                return item
        return None

    @staticmethod
    def _safe_float(record: Dict[str, Any], key: str) -> float:
        val = record.get(key)
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
