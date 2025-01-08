from .execution_base import BaseExecutionStrategy
from ibapi.order import Order
from logger import setup_logger
from config import Config

logger = setup_logger('LimitOrders')


class LimitOrderStrategy(BaseExecutionStrategy):
    """Simple limit order strategy that places orders at a specified price"""
    
    def __init__(self, trading_app, signal: dict):
        super().__init__(trading_app, signal)
        self.limit_price = signal.get('limit_price')
        if self.limit_price is None:
            raise ValueError("Limit price must be specified for limit orders")
        
    def create_order(self) -> Order:
        order = Order()
        order.action = self.signal['action']
        order.totalQuantity = self.signal['quantity']
        order.orderType = "LMT"
        order.lmtPrice = self.limit_price
        order.tif = "DAY"  # Day order by default
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.account = Config.ACCOUNT_ID
        return order

    def check_and_update(self) -> None:
        """
        Simple limit orders don't need periodic updates.
        They will remain active until filled or cancelled at end of day.
        """
        pass
