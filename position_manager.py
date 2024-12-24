from ibapi.contract import Contract
from ibapi.order import Order
from threading import Lock
import logging
import json
from typing import Dict, Any
from config import Config
from datetime import datetime
from logger import setup_logger

logger = setup_logger('PositionManager')

class PositionManager:
    def __init__(self):
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.order_lock = Lock()
        Config.DATA_DIR.mkdir(exist_ok=True)
        self._load_positions()
        
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
    
    def update_position(self, symbol: str, quantity: int, avg_price: float, instrument_type: str, strategy_id: str = None, **kwargs):
        """
        Update position tracking and persist to file
        Args:
            symbol: str - The symbol/ticker
            quantity: int - Position quantity
            avg_price: float - Average price
            instrument_type: str - Type of instrument ('STOCK', 'OPTION', etc.)
            strategy_id: str - Optional strategy identifier
            **kwargs: Additional fields like:
                - strike: float (for options)
                - expiry: str (for options)
                - option_type: str (for options)
                - pair_id: str (for pair trades)
        """
        with self.order_lock:
            position_key = f"{symbol}_{instrument_type}_{strategy_id}" if strategy_id else f"{symbol}_{instrument_type}"
            
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
            
            # Add strategy-specific details
            if kwargs.get('pair_id'):
                position['pair_id'] = kwargs.get('pair_id')
            
            self.positions[position_key] = position
            logger.info(f"Updated position for {symbol} (Strategy: {strategy_id}): {quantity} @ {avg_price}")
            self._save_positions()
    
    def get_position(self, symbol: str, strategy_id: str = None, instrument_type: str = 'STOCK') -> dict:
        """Get current position for a symbol, strategy, and instrument type"""
        with self.order_lock:
            position_key = f"{symbol}_{instrument_type}_{strategy_id}" if strategy_id else f"{symbol}_{instrument_type}"
            return self.positions.get(position_key, {
                'quantity': 0, 
                'avg_price': 0, 
                'strategy_id': strategy_id,
                'symbol': symbol,
                'instrument_type': instrument_type
            })

    def get_all_positions(self, strategy_id: str = None) -> Dict[str, Dict]:
        """Get all positions for a specific strategy"""
        with self.order_lock:
            if strategy_id:
                return {
                    k: v for k, v in self.positions.items() 
                    if v.get('strategy_id') == strategy_id
                }
            return self.positions.copy()