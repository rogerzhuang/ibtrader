from ibapi.common import BarData
from ibapi.contract import Contract
from threading import Lock
import pandas as pd
from datetime import datetime, timedelta
import logging
from logger import setup_logger
import pytz
from typing import Optional

logger = setup_logger('DataModule')

class DataModule:
    def __init__(self):
        self.streaming_data = {}   # Store real-time data by symbol
        self.historical_data = {}  # symbol -> {date -> price}
        self.data_lock = Lock()    # Thread safety for data access
        self.tick_sizes = {}       # Store tick sizes by symbol
        self.historical_data_requests = {}  # reqId -> symbol
        self.HISTORICAL_DATA_REQ_ID_BASE = 10000  # Base for historical data reqIds
        
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
    
    def request_historical_data(self, app, symbol: str, end_date: datetime):
        """Request historical daily data from IBKR
        Args:
            app: TradingApp instance
            symbol: Symbol to request data for
            end_date: End date for historical data request
        """
        try:
            # Create contract
            contract = Contract()
            contract.symbol = symbol
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"
            
            # Use next day at midnight in ET to ensure we get full closing data
            end_datetime = (end_date + timedelta(days=1)).replace(
                hour=0, minute=0, second=0
            )
            # Format with correct spacing for timezone
            end_str = end_datetime.strftime('%Y%m%d %H:%M:%S US/Eastern')
            
            # Generate request ID with offset to avoid conflicts
            req_id = self.HISTORICAL_DATA_REQ_ID_BASE + len(self.historical_data_requests)
            self.historical_data_requests[req_id] = symbol
            
            # Request 2 weeks of data (to ensure we have the close price)
            app.reqHistoricalData(
                reqId=req_id,
                contract=contract,
                endDateTime=end_str,
                durationStr="2 W",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=1,
                formatDate=1,
                keepUpToDate=False,
                chartOptions=[]
            )
            
            logger.info(f"Requested historical data for {symbol} ending {end_str}")
            
        except Exception as e:
            logger.error(f"Error requesting historical data for {symbol}: {e}")
    
    def process_historical_data(self, reqId: int, bar: BarData):
        """Process historical data bar from IBKR"""
        try:
            symbol = self.historical_data_requests.get(reqId)
            if not symbol:
                logger.error(f"Unknown reqId for historical data: {reqId}")
                return
                
            # Parse date from bar
            bar_date = datetime.strptime(bar.date, '%Y%m%d').date()
            
            with self.data_lock:
                if symbol not in self.historical_data:
                    self.historical_data[symbol] = {}
                self.historical_data[symbol][bar_date] = bar.close
                
            logger.debug(f"Stored historical close for {symbol} on {bar_date}: {bar.close}")
            
        except Exception as e:
            logger.error(f"Error processing historical data: {e}")
    
    def get_historical_close(self, symbol: str, date: datetime) -> Optional[float]:
        """Get historical close price for a symbol on a specific date"""
        with self.data_lock:
            return self.historical_data.get(symbol, {}).get(date.date())
