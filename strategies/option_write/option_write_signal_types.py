from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

@dataclass
class OptionTrade:
    action: str  # "SELL" or "BUY"
    allocation: float
    contract: str
    contracts: int
    expiry: str
    iv: float
    premium: float
    strike: float

@dataclass
class ExerciseSquare:
    symbol: str
    action: str
    quantity: float
    avg_price: float
    position_age: int

@dataclass
class SignalResponse:
    options_trades: List[OptionTrade]
    exercise_squares: List[ExerciseSquare] = None

    def __post_init__(self):
        if self.exercise_squares is None:
            self.exercise_squares = []
