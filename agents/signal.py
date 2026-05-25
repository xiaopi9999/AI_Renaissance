"""
统一信号格式 - 所有Agent必须使用这个格式输出
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum


class Direction(Enum):
    """信号方向"""
    BULLISH = "bullish"   # 看多
    BEARISH = "bearish"   # 看空
    NEUTRAL = "neutral"   # 中性


class SignalType(Enum):
    """信号类型"""
    FINANCIAL = "financial"       # 财报类
    TECHNICAL = "technical"       # 技术类
    FUNDFLOW = "fundflow"        # 资金类
    MACRO = "macro"              # 宏观类
    NEWS = "news"                # 新闻类
    VALUATION = "valuation"      # 估值类
    INDUSTRY = "industry"        # 行业类
    RISK = "risk"                # 风险类


@dataclass
class Signal:
    """
    统一信号格式

    Attributes:
        direction: 信号方向 (bullish/bearish/neutral)
        confidence: 置信度 (0.0 ~ 1.0)
        reasoning: 推理过程（为什么得出这个结论）
        signals: 检测到的具体信号列表
        source: 信号来源（哪个Agent）
        signal_type: 信号类型
        stock_code: 相关股票代码
        timestamp: 时间戳
        meta: 额外元数据
    """

    direction: str                    # bullish/bearish/neutral
    confidence: float                 # 0.0 ~ 1.0
    reasoning: str                    # 为什么
    signals: List[str] = field(default_factory=list)  # 具体信号列表
    source: str = ""                  # Agent名称
    signal_type: str = ""             # 信号类型
    stock_code: str = ""              # 股票代码
    timestamp: str = ""               # 时间戳
    weight: float = 1.0              # 权重（用于仲裁层加权）
    meta: Dict[str, Any] = field(default_factory=dict)  # 额外数据

    def __post_init__(self):
        """数据校验"""
        # 校验置信度范围
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {self.confidence}")

        # 校验方向
        valid_directions = ["bullish", "bearish", "neutral"]
        if self.direction not in valid_directions:
            raise ValueError(f"direction must be one of {valid_directions}, got {self.direction}")

        # 自动填充时间戳
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    @property
    def direction_enum(self) -> Direction:
        """获取方向枚举"""
        return Direction(self.direction)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "direction": self.direction,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "signals": self.signals,
            "source": self.source,
            "signal_type": self.signal_type,
            "stock_code": self.stock_code,
            "timestamp": self.timestamp,
            "weight": self.weight,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Signal":
        """从字典创建"""
        return cls(
            direction=data["direction"],
            confidence=data["confidence"],
            reasoning=data["reasoning"],
            signals=data.get("signals", []),
            source=data.get("source", ""),
            signal_type=data.get("signal_type", ""),
            stock_code=data.get("stock_code", ""),
            timestamp=data.get("timestamp", ""),
            weight=data.get("weight", 1.0),
            meta=data.get("meta", {}),
        )

    def __str__(self) -> str:
        emoji = {
            "bullish": "📈",
            "bearish": "📉",
            "neutral": "➡️"
        }
        return (
            f"{emoji.get(self.direction, '')} [{self.source}] "
            f"{self.direction.upper()} ({self.confidence:.0%}) - {self.reasoning[:50]}..."
        )


@dataclass
class SignalBundle:
    """
    信号束 - 用于包装多个信号
    """

    signals: List[Signal] = field(default_factory=list)
    stock_code: str = ""
    timestamp: str = ""

    def add(self, signal: Signal):
        """添加信号"""
        self.signals.append(signal)

    def filter_by_confidence(self, threshold: float) -> List[Signal]:
        """过滤低置信度信号"""
        return [s for s in self.signals if s.confidence >= threshold]

    def filter_by_direction(self, direction: str) -> List[Signal]:
        """按方向过滤"""
        return [s for s in self.signals if s.direction == direction]

    def average_confidence(self) -> float:
        """计算平均置信度"""
        if not self.signals:
            return 0.0
        return sum(s.confidence for s in self.signals) / len(self.signals)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "stock_code": self.stock_code,
            "timestamp": self.timestamp or datetime.now().isoformat(),
            "signal_count": len(self.signals),
            "signals": [s.to_dict() for s in self.signals],
        }


# 便捷函数
def bullish_signal(
    confidence: float,
    reasoning: str,
    signals: List[str],
    source: str,
    stock_code: str = "",
    **kwargs
) -> Signal:
    """快速创建看多信号"""
    return Signal(
        direction="bullish",
        confidence=confidence,
        reasoning=reasoning,
        signals=signals,
        source=source,
        stock_code=stock_code,
        **kwargs
    )


def bearish_signal(
    confidence: float,
    reasoning: str,
    signals: List[str],
    source: str,
    stock_code: str = "",
    **kwargs
) -> Signal:
    """快速创建看空信号"""
    return Signal(
        direction="bearish",
        confidence=confidence,
        reasoning=reasoning,
        signals=signals,
        source=source,
        stock_code=stock_code,
        **kwargs
    )


def neutral_signal(
    confidence: float,
    reasoning: str,
    source: str,
    stock_code: str = "",
    **kwargs
) -> Signal:
    """快速创建中性信号"""
    return Signal(
        direction="neutral",
        confidence=confidence,
        reasoning=reasoning,
        signals=[],
        source=source,
        stock_code=stock_code,
        **kwargs
    )
