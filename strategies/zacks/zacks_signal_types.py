from dataclasses import dataclass
from typing import List


@dataclass
class Position:
    ticker: str
    shares: int
    price: float
    allocation: float
    weight: float

@dataclass
class ZacksSignal:
    positions: List[Position]
    total_positions: int
    trading_days: List[str]

@dataclass
class SignalResponse:
    zacks_trades: List[Position]
