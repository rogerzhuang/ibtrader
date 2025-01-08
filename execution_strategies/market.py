from .execution_base import BaseExecutionStrategy
from ibapi.order import Order
from logger import setup_logger
from config import Config

logger = setup_logger('MarketOrders')


class IOCMarketOrderStrategy(BaseExecutionStrategy):
    """Market order strategy that uses IOC to prevent unfilled orders from hanging"""
    
    def __init__(self, trading_app, signal: dict):
        super().__init__(trading_app, signal)
        
    def create_order(self) -> Order:
        order = Order()
        order.action = self.signal['action']
        order.totalQuantity = self.signal['quantity']
        order.orderType = "MKT"
        order.tif = "IOC"  # Immediate or Cancel
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.account = Config.ACCOUNT_ID
        return order

    def check_and_update(self) -> None:
        """
        No updates needed for IOC market orders as they are automatically cancelled if unfilled
        """
        pass
