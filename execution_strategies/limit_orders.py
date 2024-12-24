from ibapi.order import Order
from .execution_base import BaseExecutionStrategy
from logger import setup_logger
from datetime import datetime

logger = setup_logger('LimitOrders')


class DynamicLimitOrderStrategy(BaseExecutionStrategy):
    """Dynamic limit order strategy that adapts to market conditions"""
    
    def __init__(self, trading_app, signal: dict, timeout_seconds: int = 60):
        super().__init__(trading_app, signal)
        self.timeout_seconds = timeout_seconds
        self.attempts = 0
        self.max_attempts = 3
        self.converted_to_market = False
        self.market_conversion_time = None
        self.market_order_timeout = 10
        self.partial_fill_timeout_multiplier = 1.5  # Extend timeout by 50% for partial fills
        self.significant_fill_threshold = 0.25      # 25% fill considered significant

    def create_order(self) -> Order:
        order = Order()
        order.action = self.signal['action']
        order.totalQuantity = self.signal['quantity']
        order.orderType = "LMT"
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.tif = 'DAY'
        
        # Get current market data
        symbol = self.signal['ticker']
        data = self.trading_app.data_module.streaming_data.get(symbol, {})
        
        if self.signal['action'] == "BUY":
            # Place limit at current bid for buys
            order.lmtPrice = data.get('bid', 0)
        else:
            # Place limit at current ask for sells
            order.lmtPrice = data.get('ask', 0)
            
        return order
        
    def check_and_update(self) -> None:
        """Periodic check for order updates"""
        if self.status != "ACTIVE" or not self.order_id:
            return
            
        # New: Check if we need to cancel a dangling market order
        if self.converted_to_market and self.market_conversion_time:
            time_since_market = (datetime.now() - self.market_conversion_time).total_seconds()
            if time_since_market > self.market_order_timeout:
                logger.warning(
                    f"Market IOC order {self.order_id} still active after "
                    f"{time_since_market:.1f}s - cancelling"
                )
                self.trading_app.cancelOrder(self.order_id)
                return

        # New: Get fill information
        fill_info = self.get_fill_info()
        
        # Updated timeout logic with partial fill handling
        if fill_info['has_partial_fill']:
            timeout_with_fills = self.timeout_seconds * self.partial_fill_timeout_multiplier
            if not self.converted_to_market and self.timeout_exceeded(timeout_with_fills):
                remaining = self.signal['quantity'] - fill_info['filled_quantity']
                logger.info(
                    f"Timeout reached for partially filled order {self.order_id}, "
                    f"converting remaining {remaining} to IOC market order"
                )
                self.modify_order({
                    'orderType': 'MKT',
                    'tif': 'IOC',
                    'lmtPrice': 0.0
                })
                self.converted_to_market = True
                self.market_conversion_time = datetime.now()
                return
        else:
            if not self.converted_to_market and self.timeout_exceeded(self.timeout_seconds):
                logger.info(f"Timeout reached for unfilled order {self.order_id}, converting to IOC market order")
                self.modify_order({
                    'orderType': 'MKT',
                    'tif': 'IOC',
                    'lmtPrice': 0.0
                })
                self.converted_to_market = True
                self.market_conversion_time = datetime.now()
                return
        
        # Updated price adjustment logic
        if not self.converted_to_market and self.attempts < self.max_attempts:
            # New: Check for significant partial fills
            if fill_info['has_partial_fill']:
                filled_pct = fill_info['filled_quantity'] / self.signal['quantity']
                
                if filled_pct >= self.significant_fill_threshold:
                    logger.info(f"Significant partial fill ({filled_pct*100:.1f}%) - skipping price update")
                    return
                    
            # Get latest market data
            symbol = self.signal['ticker']
            data = self.trading_app.data_module.streaming_data.get(symbol, {})
            
            if self.signal['action'] == "BUY":
                new_price = data.get('bid', 0)
            else:
                new_price = data.get('ask', 0)
            
            # Compare with current order's limit price
            if self.current_order:  # Use stored IBKR Order object
                current_price = self.current_order.lmtPrice
                
                if new_price != current_price:
                    logger.info(f"Updating limit price for order {self.order_id} from {current_price} to {new_price}")
                    
                    self.modify_order({
                        'lmtPrice': new_price
                    })
                    
                    self.attempts += 1
