from ibapi.contract import Contract
from ibapi.order import Order
from threading import Lock
import logging
import json
from typing import Dict, Any, Optional, Union
from config import Config
from datetime import datetime
from logger import setup_logger
from uuid import uuid4

logger = setup_logger('PositionManager')

class PositionManager:
    def __init__(self):
        self.orders: Dict[Union[int, str], Dict[str, Any]] = {}
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.order_lock = Lock()
        self.TEMP_ORDER_PREFIX = "TEMP_"  # Prefix for temporary orders
        Config.DATA_DIR.mkdir(exist_ok=True)
        self._load_positions()
        self._load_orders()
        
    def _load_positions(self):
        """Load positions from file on startup"""
        try:
            if Config.POSITIONS_FILE.exists():
                with open(Config.POSITIONS_FILE, 'r') as f:
                    self.positions = json.load(f)
                logger.info("Loaded existing positions from file")
        except Exception as e:
            logger.error(f"Error loading positions: {e}")
            self.positions = {}

    def _save_positions(self):
        """Save positions to file"""
        try:
            with open(Config.POSITIONS_FILE, 'w') as f:
                json.dump(self.positions, f, indent=4)
            logger.debug("Saved positions to file")
        except Exception as e:
            logger.error(f"Error saving positions: {e}")
    
    def _generate_position_id(self) -> str:
        """Generate a unique position ID"""
        return str(uuid4())

    def find_matching_position(self, symbol: str, instrument_type: str, 
                             strategy_id: str, **kwargs) -> Optional[str]:
        """
        Find existing position matching exact instrument characteristics
        Returns position_id if found, None otherwise
        """
        with self.order_lock:
            return self._find_matching_position_internal(
                symbol, instrument_type, strategy_id, **kwargs
            )

    def _find_matching_position_internal(self, symbol: str, instrument_type: str, 
                                       strategy_id: str, **kwargs) -> Optional[str]:
        """Find existing position without lock"""
        for position_id, position in self.positions.items():
            if (position['symbol'] == symbol and 
                position['strategy_id'] == strategy_id and
                position['instrument_type'] == instrument_type):
                
                # For options, match all option-specific fields
                if instrument_type == 'OPTION':
                    if (position['strike'] == kwargs.get('strike') and
                        position['expiry'] == kwargs.get('expiry') and
                        position['option_type'] == kwargs.get('option_type')):
                        return position_id
                # For futures, match expiry
                elif instrument_type == 'FUTURE':
                    if position['expiry'] == kwargs.get('expiry'):
                        return position_id
                # For stocks, match already found
                else:
                    return position_id
        return None

    def get_or_create_position_id(self, symbol: str, instrument_type: str, 
                                strategy_id: str, **kwargs) -> str:
        """
        Get existing position ID or generate new one if no match exists
        """
        position_id = self.find_matching_position(
            symbol, instrument_type, strategy_id, **kwargs
        )
        if not position_id:
            position_id = self._generate_position_id()
        return position_id

    def create_order_info(self, signal: Dict) -> Dict:
        """
        Create order info with position ID based on signal
        """
        instrument_type = signal['type']
        
        # Get position ID based on instrument characteristics
        kwargs = {}
        if instrument_type == 'OPTION':
            kwargs.update({
                'strike': signal['strike'],
                'expiry': signal['expiry'],
                'option_type': signal['option_type']
            })
        elif instrument_type == 'FUTURE':
            kwargs.update({
                'expiry': signal['expiry']
            })
            
        position_id = self.get_or_create_position_id(
            signal['ticker'],
            instrument_type,
            signal['strategy_id'],
            **kwargs
        )
        
        # Create order info
        order_info = {
            'symbol': signal['ticker'],
            'action': signal['action'],
            'quantity': signal['quantity'],
            'strategy_id': signal['strategy_id'],
            'instrument_type': instrument_type,
            'position_id': position_id,
            'timestamp': datetime.now(Config.TIMEZONE).isoformat(),
            'last_processed_fill': 0,
            'fill_processed': False,
            'execution_type': signal['execution_strategy']
        }
        
        # Add instrument-specific and strategy-specific details
        order_info.update(kwargs)
        if signal.get('pair_id'):
            order_info['pair_id'] = signal['pair_id']
            
        return order_info

    def update_position(self, symbol: str, quantity: int, avg_price: float, 
                       instrument_type: str, strategy_id: str, 
                       position_id: str, **kwargs):
        """Update position tracking and persist to file"""
        with self.order_lock:
            self._update_position_internal(
                symbol=symbol,
                quantity=quantity,
                avg_price=avg_price,
                instrument_type=instrument_type,
                strategy_id=strategy_id,
                position_id=position_id,
                **kwargs
            )

    def _update_position_internal(self, symbol: str, quantity: int, avg_price: float,
                                instrument_type: str, strategy_id: str,
                                position_id: str, **kwargs):
        """Internal method to update position without locking"""
        # Base position info
        position = {
            'symbol': symbol,
            'quantity': quantity,
            'avg_price': avg_price,
            'strategy_id': strategy_id,
            'instrument_type': instrument_type,
            'last_updated': datetime.now(Config.TIMEZONE).isoformat()
        }
        
        # Add instrument-specific details
        if instrument_type == 'OPTION':
            position.update({
                'strike': kwargs.get('strike'),
                'expiry': kwargs.get('expiry'),
                'option_type': kwargs.get('option_type')
            })
        elif instrument_type == 'FUTURE':
            position.update({
                'expiry': kwargs.get('expiry')
            })
        
        # Add strategy-specific details
        if kwargs.get('pair_id'):
            position['pair_id'] = kwargs.get('pair_id')
        
        self.positions[position_id] = position
        logger.info(f"Updated position {position_id} for {symbol} (Strategy: {strategy_id}): {quantity} @ {avg_price}")
        self._save_positions()
    
    def get_all_positions(self, strategy_id: str = None) -> Dict[str, Dict]:
        """Get all positions for a specific strategy"""
        with self.order_lock:
            if strategy_id:
                return {
                    pos_id: pos for pos_id, pos in self.positions.items() 
                    if pos.get('strategy_id') == strategy_id
                }
            return self.positions.copy()

    def _load_orders(self):
        """Load orders from file on startup"""
        try:
            if Config.ORDERS_FILE.exists():
                with open(Config.ORDERS_FILE, 'r') as f:
                    self.orders = json.load(f)
                logger.info("Loaded existing orders from file")
        except Exception as e:
            logger.error(f"Error loading orders: {e}")
            self.orders = {}

    def _save_orders(self):
        """Save orders to file"""
        try:
            with open(Config.ORDERS_FILE, 'w') as f:
                json.dump(self.orders, f, indent=4)
            logger.debug("Saved orders to file")
        except Exception as e:
            logger.error(f"Error saving orders: {e}")

    def update_order(self, order_id: int, updates: dict):
        """Update or create order information and save to file"""
        with self.order_lock:
            if order_id in self.orders:
                self.orders[order_id].update(updates)
            else:
                self.orders[order_id] = updates
            self._save_orders()

    def process_fill(self, order_id: int, new_fill_quantity: float, fill_price: float) -> None:
        """Process a fill, handling both regular fills and closing exercises"""
        with self.order_lock:
            self._process_fill_internal(
                order_id=order_id,
                new_fill_quantity=new_fill_quantity,
                fill_price=fill_price,
            )

    def _process_fill_internal(self, order_id: int, new_fill_quantity: float, 
                             fill_price: float) -> None:
        """Process a fill without locking"""
        order = self.orders.get(order_id)
        if not order:
            logger.error(f"Order {order_id} not found")
            return

        position_id = order['position_id']
        current_position = self.positions.get(position_id, {
            'quantity': 0,
            'avg_price': 0
        })

        logger.debug(
            f"Processing position update for position_id {position_id} - "
            f"Current: {current_position.get('quantity', 0)} @ "
            f"{current_position.get('avg_price', 0)}"
        )

        # Calculate position update
        filled_qty = new_fill_quantity if order['action'] == 'BUY' else -new_fill_quantity
        new_quantity = current_position.get('quantity', 0) + filled_qty
        
        # Calculate new average price
        if new_quantity != 0:
            current_qty = current_position.get('quantity', 0)
            current_avg = current_position.get('avg_price', 0)
            
            if current_qty * new_quantity > 0:  # Same direction
                if abs(new_quantity) > abs(current_qty):  # Adding
                    new_avg_price = (
                        (abs(current_qty) * current_avg) + 
                        (abs(filled_qty) * fill_price)
                    ) / abs(new_quantity)
                else:  # Reducing
                    new_avg_price = current_avg
            else:  # Direction changed
                new_avg_price = fill_price
        else:  # Position closed
            new_avg_price = 0

        # Update position without acquiring lock again
        self._update_position_internal(
            symbol=order['symbol'],
            quantity=new_quantity,
            avg_price=new_avg_price,
            instrument_type=order['instrument_type'],
            strategy_id=order['strategy_id'],
            position_id=position_id,
            **{k: v for k, v in order.items() if k in ['strike', 'expiry', 'option_type', 'pair_id']}
        )

    def _generate_order_id(self) -> str:
        """Generate a unique order ID"""
        return str(uuid4())

    def process_exercise(self, symbol: str, position: dict, close_price: float, pos_id: str):
        """Process option exercise/assignment/expiration
        Args:
            symbol: Underlying symbol
            position: Option position dictionary
            close_price: Underlying close price on expiration
            pos_id: Position ID
        """
        with self.order_lock:
            try:
                strike = position['strike']
                quantity = position['quantity']
                option_type = position['option_type']
                
                # Determine if option is in the money
                is_call = option_type.upper() == 'CALL'
                is_itm = (close_price > strike) if is_call else (close_price < strike)
                
                if is_itm:
                    # Exercise (positive position) or Assignment (negative position)
                    # Create synthetic option close order
                    synthetic_option_order_id = self._generate_order_id()
                    synthetic_option_order = {
                        'symbol': symbol,
                        'action': 'BUY' if quantity < 0 else 'SELL',  # Close position direction
                        'quantity': abs(quantity),
                        'position_id': pos_id,
                        'strategy_id': position['strategy_id'],
                        'instrument_type': 'OPTION',
                        'strike': strike,
                        'expiry': position['expiry'],
                        'option_type': option_type,
                        'timestamp': datetime.now(Config.TIMEZONE).isoformat(),
                        'synthetic_exercise_close': True
                    }
                    self.orders[synthetic_option_order_id] = synthetic_option_order
                    
                    # Close option position
                    self._process_fill_internal(
                        order_id=synthetic_option_order_id,
                        new_fill_quantity=abs(quantity),
                        fill_price=0.0
                    )
                    
                    # Create/update stock position
                    stock_position_id = self._find_matching_position_internal(
                        symbol=symbol,
                        instrument_type='STOCK',
                        strategy_id=position['strategy_id']
                    ) or self._generate_position_id()
                    
                    # Determine stock action and quantity
                    is_exercise = quantity > 0
                    stock_action = 'BUY' if (is_call == is_exercise) else 'SELL'
                    stock_qty = abs(quantity) * 100  # Convert to shares
                    
                    # Create synthetic stock order
                    synthetic_stock_order_id = self._generate_order_id()
                    synthetic_stock_order = {
                        'symbol': symbol,
                        'action': stock_action,
                        'quantity': stock_qty,
                        'position_id': stock_position_id,
                        'strategy_id': position['strategy_id'],
                        'instrument_type': 'STOCK',
                        'timestamp': datetime.now(Config.TIMEZONE).isoformat(),
                        'synthetic_exercise_stock': True
                    }
                    self.orders[synthetic_stock_order_id] = synthetic_stock_order
                    
                    # Update stock position
                    self._process_fill_internal(
                        order_id=synthetic_stock_order_id,
                        new_fill_quantity=stock_qty,
                        fill_price=strike  # Exercise/assignment occurs at strike price
                    )
                    
                    logger.info(
                        f"Processed {'exercise' if is_exercise else 'assignment'} for "
                        f"{symbol} {option_type} {strike} - {quantity} contracts"
                    )
                
                else:
                    # Out of the money - expire worthless
                    synthetic_order_id = self._generate_order_id()
                    synthetic_order = {
                        'symbol': symbol,
                        'action': 'BUY' if quantity < 0 else 'SELL',  # Close position direction
                        'quantity': abs(quantity),
                        'position_id': pos_id,
                        'strategy_id': position['strategy_id'],
                        'instrument_type': 'OPTION',
                        'strike': strike,
                        'expiry': position['expiry'],
                        'option_type': option_type,
                        'timestamp': datetime.now(Config.TIMEZONE).isoformat(),
                        'synthetic_expiration': True
                    }
                    self.orders[synthetic_order_id] = synthetic_order
                    
                    # Close option position
                    self._process_fill_internal(
                        order_id=synthetic_order_id,
                        new_fill_quantity=abs(quantity),
                        fill_price=0.0
                    )
                    
                    logger.info(
                        f"Processed expiration for {symbol} {option_type} {strike} - "
                        f"{quantity} contracts"
                    )
                
                self._save_orders()
                
            except Exception as e:
                logger.error(f"Error processing exercise/expiration: {e}")
                raise