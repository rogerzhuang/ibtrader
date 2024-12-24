from .execution_base import BaseExecutionStrategy
from ibapi.order import Order
from logger import setup_logger

logger = setup_logger('MarketOrders')


class IOCMarketOrderStrategy(BaseExecutionStrategy):
    """Market order strategy that uses IOC to prevent unfilled orders from hanging"""
    
    def __init__(self, trading_app, signal: dict, timeout_seconds: int = 30):
        super().__init__(trading_app, signal)
        self.timeout_seconds = timeout_seconds
        
    def create_order(self) -> Order:
        order = Order()
        order.action = self.signal['action']
        order.totalQuantity = self.signal['quantity']
        order.orderType = "MKT"
        order.tif = "IOC"  # Immediate or Cancel
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        return order
        
    def check_and_update(self) -> None:
        """Check if order should be cancelled due to timeout"""
        if self.status == "ACTIVE" and self.timeout_exceeded(self.timeout_seconds):
            logger.info(f"Order {self.order_id} exceeded timeout - cancelling")
            self.trading_app.cancelOrder(self.order_id)
