from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict
from ibapi.contract import Contract
from ibapi.order import Order
from threading import Lock
import logging
from datetime import datetime
from logger import setup_logger

logger = setup_logger('ExecutionStrategy')


class BaseExecutionStrategy(ABC):
    """Base class for execution strategies"""
    
    def __init__(self, trading_app, signal: dict):
        self.trading_app = trading_app
        self.signal = signal
        self.start_time = datetime.now()
        self.order_id = None  # UUID-based order ID
        self.ib_order_id = None  # IB-assigned order ID
        self.status = "PENDING"  # PENDING, ACTIVE, COMPLETED, CANCELLED
        self.lock = Lock()
        self.current_order = None  # Store the actual IBKR Order object
        self.filled_quantity = 0  # Track filled quantity
        self.avg_fill_price = 0  # Track average fill price
        self.has_partial_fill = False  # Flag for tracking partial fills
        
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
            contract.lastTradeDateOrContractMonth = datetime.strptime(self.signal['expiry'], '%Y-%m-%d').strftime('%Y%m%d')
            contract.strike = self.signal['strike']
            contract.right = "C" if self.signal['option_type'].upper() == "CALL" else "P"
            contract.multiplier = "100"
        return contract

    def process_order_status(self, status: str, filled: float, remaining: float, avgFillPrice: float) -> None:
        """Process order status updates from TWS"""
        with self.lock:
            self.filled_quantity = filled
            self.avg_fill_price = avgFillPrice
            
            if filled > 0 and remaining > 0:
                # Order is still active but has partial fills
                self.status = "ACTIVE"
                self.has_partial_fill = True
                logger.debug(
                    f"Order {self.order_id} partially filled: {filled} executed at avg price "
                    f"{avgFillPrice}, {remaining} remaining"
                )
            elif status == "Filled":
                self.status = "COMPLETED"
                logger.debug(
                    f"Order {self.order_id} fully filled: {filled} executed at avg price "
                    f"{avgFillPrice}"
                )
            elif status == "Cancelled":
                self.status = "CANCELLED"
                logger.debug(
                    f"Order {self.order_id} cancelled with {filled} filled at "
                    f"{avgFillPrice} and {remaining} remaining"
                )
            else:
                self.status = "ACTIVE"  # Keep as active for other statuses
                logger.debug(
                    f"Order {self.order_id} status {status}: {filled} filled at "
                    f"{avgFillPrice}, {remaining} remaining"
                )

    @abstractmethod
    def check_and_update(self) -> None:
        """Periodic check for order updates and modifications"""
        pass

    def modify_order(self, order_modifications: Dict) -> None:
        """Safely modify an existing order using IBKR's order modification"""
        with self.lock:
            if not self.ib_order_id or self.status != "ACTIVE" or not self.current_order:
                return
            
            # Create modified order from current IBKR Order object
            modified_order = Order()
            for key, value in vars(self.current_order).items():
                if not key.startswith('_'):
                    setattr(modified_order, key, value)
            
            # Apply modifications
            for key, value in order_modifications.items():
                setattr(modified_order, key, value)
            
            # Update current order and place modification
            self.current_order = modified_order
            self.trading_app.placeOrder(self.ib_order_id, self.create_contract(), modified_order)
            logger.info(f"Modified order {self.order_id} (IB: {self.ib_order_id}) with {order_modifications}")

    def is_complete(self) -> bool:
        """Check if execution is complete"""
        return self.status in ["COMPLETED", "CANCELLED"]

    def place_order(self, contract: Contract, order: Order) -> None:
        """Place new order and track both UUID and IB order IDs"""
        with self.lock:
            if self.trading_app.next_order_id:
                self.order_id = self.trading_app.position_manager._generate_order_id()  # Get UUID from position manager
                self.ib_order_id = self.trading_app.next_order_id
                self.trading_app.next_order_id += 1
                self.current_order = order
                
                # Store mapping of IB order ID to UUID
                self.trading_app.ib_to_uuid_map[self.ib_order_id] = self.order_id
                
                self.trading_app.placeOrder(self.ib_order_id, contract, order)
                self.status = "ACTIVE"
                logger.info(f"Placed order {self.order_id} (IB: {self.ib_order_id})")
                
    def timeout_exceeded(self, timeout_seconds: int) -> bool:
        """Check if strategy has exceeded timeout"""
        return (datetime.now() - self.start_time).total_seconds() > timeout_seconds

    def get_fill_info(self) -> dict:
        """Get current fill information
        
        Returns:
            dict containing current fill quantity, average price and partial fill status
        """
        with self.lock:
            return {
                'filled_quantity': self.filled_quantity,
                'avg_fill_price': self.avg_fill_price,
                'has_partial_fill': self.has_partial_fill
            }
