from dataclasses import dataclass
from typing import List
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
class SignalResponse:
    options_trades: List[OptionTrade]
