from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

@dataclass
class TradeLeg:
    ticker: str
    action: str  # "BUY" or "SELL"
    quantity: int
    price: float

@dataclass
class PairTrade:
    pair: str
    action: str  # "TRADE" or "SQUARE"
    legs: List[TradeLeg]

@dataclass
class OptionTrade:
    pair: str
    contract: str
    action: str
    strike: float
    contracts: int
    expiry: str
    premium_target: float

@dataclass
class SignalResponse:
    pairs_trades: List[PairTrade]
    options_trades: List[OptionTrade]