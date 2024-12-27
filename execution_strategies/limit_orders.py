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
        
        # Get current market data and tick size for the instrument
        symbol = self._get_full_symbol()
        data = self.trading_app.data_module.streaming_data.get(symbol, {})
        tick_size = self.trading_app.data_module.get_tick_size(symbol)
        
        if tick_size is None:
            logger.error(f"No tick size available for {symbol}")
            return None
        
        bid = data.get('bid')
        ask = data.get('ask')
        
        if self.signal['action'] == "BUY":
            if bid is None or bid <= 0 or ask is None or ask <= 0:
                price = data.get('last')  # Try last price as fallback
                if price is None or price <= 0:
                    logger.error(f"No valid price data for {symbol} BUY order")
                    return None
            else:
                # Calculate mid price and round to nearest valid tick
                mid_price = (bid + ask) / 2
                ticks = round(mid_price / tick_size)
                price = ticks * tick_size
                # If rounded mid price is above ask, use bid instead
                if price >= ask:
                    price = bid
        else:  # SELL
            if bid is None or bid <= 0 or ask is None or ask <= 0:
                price = data.get('last')  # Try last price as fallback
                if price is None or price <= 0:
                    logger.error(f"No valid price data for {symbol} SELL order")
                    return None
            else:
                # Calculate mid price and round to nearest valid tick
                mid_price = (bid + ask) / 2
                ticks = round(mid_price / tick_size)
                price = ticks * tick_size
                # If rounded mid price is below bid, use ask instead
                if price <= bid:
                    price = ask
        
        order.lmtPrice = price
        logger.info(f"Creating {order.action} limit order for {symbol} at {order.lmtPrice} (tick size: {tick_size})")
        return order
        
    def check_and_update(self) -> None:
        """Periodic check for order updates"""
        if self.status != "ACTIVE" or not self.order_id:
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
                return
        
        # Updated price adjustment logic
        if not self.converted_to_market and self.attempts < self.max_attempts:
            if fill_info['has_partial_fill']:
                filled_pct = fill_info['filled_quantity'] / self.signal['quantity']
                
                if filled_pct >= self.significant_fill_threshold:
                    logger.info(f"Significant partial fill ({filled_pct*100:.1f}%) - skipping price update")
                    return
                    
            # Get latest market data and tick size
            symbol = self._get_full_symbol()
            data = self.trading_app.data_module.streaming_data.get(symbol, {})
            tick_size = self.trading_app.data_module.get_tick_size(symbol)
            
            if tick_size is None:
                logger.warning(f"No tick size available for {symbol} - skipping price update")
                return
            
            bid = data.get('bid')
            ask = data.get('ask')
            
            if bid is None or ask is None:
                logger.warning(f"Incomplete market data for {symbol} - skipping price update")
                return
            
            # Calculate new price using mid price approach
            mid_price = (bid + ask) / 2
            ticks = round(mid_price / tick_size)
            new_price = ticks * tick_size
            
            if self.signal['action'] == "BUY":
                if new_price >= ask:  # If rounded mid would be above ask
                    new_price = bid
            else:  # SELL
                if new_price <= bid:  # If rounded mid would be below bid
                    new_price = ask
            
            # Compare with current order's limit price
            if self.current_order:
                current_price = self.current_order.lmtPrice
                
                if new_price != current_price:
                    logger.info(f"Updating limit price for order {self.order_id} from {current_price} to {new_price}")
                    
                    self.modify_order({
                        'lmtPrice': new_price
                    })
                    
                    self.attempts += 1

    def _get_full_symbol(self) -> str:
        """Helper method to get the full symbol including option details if applicable"""
        if self.signal.get('type') == 'OPTION':
            # Construct option symbol
            return f"{self.signal['ticker']}_{self.signal['strike']}_{self.signal['expiry']}_{self.signal['option_type']}"
        return self.signal['ticker']
