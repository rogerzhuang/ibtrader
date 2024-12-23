from abc import ABC, abstractmethod
from typing import Optional, Tuple
from ibapi.contract import Contract
from ibapi.order import Order
import time
from threading import Lock
import logging
from logger import setup_logger
from datetime import datetime, timedelta

logger = setup_logger('ExecutionStrategy')

class BaseExecutionStrategy(ABC):
    """Base class for execution strategies"""
    
    def __init__(self, trading_app, signal: dict):
        self.trading_app = trading_app
        self.signal = signal
        self.start_time = datetime.now()
        self.order_id = None
        self.status = "PENDING"  # PENDING, ACTIVE, COMPLETED, CANCELLED
        self.lock = Lock()
        
    @abstractmethod
    def create_order(self) -> Order:
        """Create the order object based on strategy"""
        pass
        
    def create_contract(self) -> Contract:
        """Create the contract object"""
        if self.signal['type'] == 'STOCK':
            contract = Contract()
            contract.symbol = self.signal['ticker']
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"
        else:  # OPTION
            contract = Contract()
            contract.symbol = self.signal['ticker']
            contract.secType = "OPT"
            contract.exchange = "SMART"
            contract.currency = "USD"
            contract.lastTradeDateOrContractMonth = self.signal['expiry']
            contract.strike = self.signal['strike']
            contract.right = "P" if self.signal['option_type'] == "PUT" else "C"
            contract.multiplier = "100"
        return contract
        
    @abstractmethod
    def process_order_status(self, status: str, filled: float, remaining: float, avgFillPrice: float) -> None:
        """Process order status updates"""
        pass

    def cancel_order(self) -> None:
        """Cancel current order if active"""
        if self.order_id and self.status == "ACTIVE":
            self.trading_app.cancelOrder(self.order_id)
            logger.info(f"Cancelled order {self.order_id}")

    def is_complete(self) -> bool:
        """Check if execution is complete"""
        return self.status == "COMPLETED"
        
    def place_order(self, contract: Contract, order: Order) -> None:
        """Place new order and track order ID"""
        with self.lock:
            if self.trading_app.next_order_id:
                self.order_id = self.trading_app.next_order_id
                self.trading_app.next_order_id += 1
                self.trading_app.placeOrder(self.order_id, contract, order)
                self.status = "ACTIVE"
                logger.info(f"Placed order {self.order_id}")
                
    def timeout_exceeded(self, timeout_seconds: int) -> bool:
        """Check if strategy has exceeded timeout"""
        return (datetime.now() - self.start_time).total_seconds() > timeout_seconds

class MarketOrderStrategy(BaseExecutionStrategy):
    """Simple market order execution strategy"""
    
    def create_order(self) -> Order:
        order = Order()
        order.action = self.signal['action']
        order.totalQuantity = self.signal['quantity']
        order.orderType = "MKT"
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.tif = 'DAY'
        return order
        
    def process_order_status(self, status: str, filled: float, remaining: float, avgFillPrice: float) -> None:
        """Process market order status"""
        with self.lock:
            if status == "Filled":
                self.status = "COMPLETED"
                logger.info(f"Market order {self.order_id} filled at {avgFillPrice}")

class DynamicLimitOrderStrategy(BaseExecutionStrategy):
    """Dynamic limit order strategy that adapts to market conditions"""
    
    def __init__(self, trading_app, signal: dict, timeout_seconds: int = 60):
        super().__init__(trading_app, signal)
        self.timeout_seconds = timeout_seconds
        self.last_price_update = None
        self.attempts = 0
        self.max_attempts = 3
        
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
            
        self.last_price_update = datetime.now()
        return order
        
    def process_order_status(self, status: str, filled: float, remaining: float, avgFillPrice: float) -> None:
        """Process limit order status and adjust if needed"""
        with self.lock:
            if status == "Filled":
                self.status = "COMPLETED"
                logger.info(f"Limit order {self.order_id} filled at {avgFillPrice}")
                return
                
            # Check if we need to adjust the order
            current_time = datetime.now()
            
            # If final timeout reached, cancel and switch to market
            if self.timeout_exceeded(self.timeout_seconds):
                logger.info(f"Timeout reached for order {self.order_id}, switching to market order")
                self.cancel_order()
                
                # Create and place market order
                market_order = Order()
                market_order.action = self.signal['action']
                market_order.totalQuantity = remaining
                market_order.orderType = "MKT"
                market_order.eTradeOnly = False
                market_order.firmQuoteOnly = False
                market_order.tif = 'DAY'
                
                contract = self.create_contract()
                self.place_order(contract, market_order)
                return
                
            # Check if we should update the limit price
            if (self.last_price_update and 
                current_time - self.last_price_update > timedelta(seconds=10) and
                self.attempts < self.max_attempts):
                
                # Get latest market data
                symbol = self.signal['ticker']
                data = self.trading_app.data_module.streaming_data.get(symbol, {})
                
                if self.signal['action'] == "BUY":
                    new_price = data.get('bid', 0)
                else:
                    new_price = data.get('ask', 0)
                
                # Only update if price has changed
                current_order = self.trading_app.execution_module.orders.get(self.order_id, {})
                current_price = current_order.get('limit_price')
                
                if new_price != current_price:
                    logger.info(f"Updating limit price for order {self.order_id} from {current_price} to {new_price}")
                    self.cancel_order()
                    
                    # Create and place new limit order
                    limit_order = Order()
                    limit_order.action = self.signal['action']
                    limit_order.totalQuantity = remaining
                    limit_order.orderType = "LMT"
                    limit_order.lmtPrice = new_price
                    limit_order.eTradeOnly = False
                    limit_order.firmQuoteOnly = False
                    limit_order.tif = 'DAY'
                    
                    contract = self.create_contract()
                    self.place_order(contract, limit_order)
                    
                    self.attempts += 1
                    self.last_price_update = current_time

def create_execution_strategy(trading_app, signal: dict) -> BaseExecutionStrategy:
    """Factory function to create appropriate execution strategy"""
    strategy_type = signal.get('execution_strategy', 'MARKET')  # Default to market orders
    
    if strategy_type == "MARKET":
        return MarketOrderStrategy(trading_app, signal)
    elif strategy_type == "DYNAMIC_LIMIT":
        timeout = signal.get('execution_timeout', 60)  # Default 60 second timeout
        return DynamicLimitOrderStrategy(trading_app, signal, timeout)
    else:
        raise ValueError(f"Unknown execution strategy type: {strategy_type}")