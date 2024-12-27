from ibapi.common import BarData
from threading import Lock
import pandas as pd
from datetime import datetime
import logging
from logger import setup_logger

logger = setup_logger('DataModule')

class DataModule:
    def __init__(self):
        self.streaming_data = {}   # Store real-time data by symbol
        self.data_lock = Lock()    # Thread safety for data access
        self.tick_sizes = {}       # Store tick sizes by symbol
        
    def set_tick_size(self, symbol: str, tick_size: float):
        """Store tick size information for a symbol"""
        with self.data_lock:
            self.tick_sizes[symbol] = tick_size
            logger.debug(f"Set tick size for {symbol}: {tick_size}")
    
    def get_tick_size(self, symbol: str) -> float:
        """Get tick size for a symbol"""
        with self.data_lock:
            return self.tick_sizes.get(symbol)
    
    def process_streaming_data(self, symbol: str, price: float, tick_type: str):
        """Process streaming data for both stocks and options"""
        with self.data_lock:
            symbol_parts = symbol.split('_')
            is_option = len(symbol_parts) == 4
            
            # Initialize data structure if needed
            if symbol not in self.streaming_data:
                if is_option:
                    self.streaming_data[symbol] = {
                        'last': None,
                        'bid': None,
                        'ask': None,
                        'underlying_last': None,
                        'underlying_bid': None,
                        'underlying_ask': None
                    }
                else:
                    self.streaming_data[symbol] = {
                        'last': None,
                        'bid': None,
                        'ask': None
                    }
            
            # Handle price updates
            if not is_option:  # This is a stock (could be an underlying)
                # If this is an underlying, update all related options
                for opt_symbol in self.streaming_data:
                    opt_parts = opt_symbol.split('_')
                    if len(opt_parts) == 4 and opt_parts[0] == symbol:
                        if tick_type == 'LAST':
                            self.streaming_data[opt_symbol]['underlying_last'] = price
                        elif tick_type == 'BID':
                            self.streaming_data[opt_symbol]['underlying_bid'] = price
                        elif tick_type == 'ASK':
                            self.streaming_data[opt_symbol]['underlying_ask'] = price
            
            # Update direct price for both stocks and options
            if tick_type == 'LAST':
                self.streaming_data[symbol]['last'] = price
            elif tick_type == 'BID':
                self.streaming_data[symbol]['bid'] = price
            elif tick_type == 'ASK':
                self.streaming_data[symbol]['ask'] = price
            
            logger.debug(f"Processed {tick_type} data for {symbol}: {price}")
    
    def get_latest_price(self, symbol: str, price_type: str = 'last', include_underlying: bool = False) -> dict:
        """Get latest price data including underlying if requested"""
        with self.data_lock:
            data = self.streaming_data.get(symbol, {})
            if include_underlying:
                return {
                    'price': data.get(price_type),
                    'underlying_price': data.get(f'underlying_{price_type}')
                }
            return data.get(price_type)
