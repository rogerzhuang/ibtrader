import sys
from threading import Lock
from datetime import datetime
import pytz
from typing import Dict, Optional
import queue
import logging
from pathlib import Path
import time

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.common import TickerId, BarData
from ibapi.order_state import OrderState

from data_module import DataModule
from strategy_module import StrategyModule
from position_manager import PositionManager
from config import Config
from logger import setup_logger
from execution_strategies import create_execution_strategy

logger = setup_logger('TradingApp')

class TradingApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.data_module = DataModule()
        self.position_manager = PositionManager()
        self.strategy_module = StrategyModule(self.data_module, self.position_manager)
        
        # Initialize tracking variables
        self.next_order_id = None
        self.last_connection_time = None
        self.reqId_to_symbol: Dict[int, str] = {}
        self.connected = False
        self.contract_details_queue = queue.Queue()
        self.errors = queue.Queue()
        
        # Keep only the order ID lock - remove connection_lock
        self.lock = Lock()  # Used for next_order_id synchronization
        self.execution_lock = Lock()  # New lock for execution tracking
        
        # Validate capital allocation
        Config.validate_capital_allocation()
        
        # Initialize strategies with calculated capital
        enabled_strategies = Config.get_enabled_strategies()
        self.strategy_module.initialize_strategies(enabled_strategies)
        
        # Add shutdown flag
        self.running = True
        
        # Add tracking for subscribed symbols
        self.subscribed_symbols = set()
        
        # Add data ready tracking
        self.market_data_timeout = 5  # seconds to wait for market data

        # Add daily cleanup tracking
        self.last_cleanup_date = None

        # Add daily exercise tracking
        self.last_exercise_date = None

        # Add execution strategy tracking
        self.active_executions = {}  # order_id -> execution_strategy
        self.execution_check_interval = 1  # Check executions every second
        
        self.ib_to_uuid_map = {}  # Map IB order IDs to UUID order IDs
    
    def connect_and_wait(self) -> bool:
        """Attempt to connect to TWS
        Returns: should_retry
        - False: Successfully connected
        - True: Failed and should retry with new client thread
        """
        interval = 5  # seconds

        time.sleep(interval)  
        
        if self.connected:
            return False
            
        try:
            if not super().isConnected():
                logger.info("Attempting to connect to TWS...")
                self.disconnect()
                self.connect(Config.TWS_HOST, Config.TWS_PORT, Config.CLIENT_ID)
                time.sleep(interval)  
                return True  # Need retry with new client thread
        except Exception as e:
            logger.warning(f"Connection attempt failed: {e}")
            return True  # Need retry with new client thread

    def disconnect(self):
        """Safely disconnect from TWS/IB Gateway"""
        # Remove connection_lock - simple boolean operation is atomic
        if self.connected:
            super().disconnect()
            self.connected = False
            logger.info("Disconnected from TWS")

    def shutdown(self):
        """Clean shutdown of the trading app"""
        self.running = False
        self.disconnect()

    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson=""):
        """Handle errors from TWS"""
        error_message = f"Error {errorCode}: {errorString}"
        
        # Remove connection_lock - simple boolean operations are atomic
        if errorCode == 1100:  # Connectivity lost
            self.connected = False
            logger.error("Connection to TWS lost")
        elif errorCode == 1102:  # Connectivity restored
            self.connected = True
            logger.info("Connection to TWS restored")
        elif errorCode in [2104, 2106, 2158, 2176]:  # Non-critical connection messages
            logger.info(error_message)
        else:
            logger.error(error_message)
        
        self.errors.put((errorCode, errorString))

    def nextValidId(self, orderId: int):
        """Callback for next valid order ID"""
        with self.lock:  # Keep this lock for order ID synchronization
            self.next_order_id = orderId
        
        # Check if this is a new connection (not just an order ID update)
        if not self.connected:
            self.connected = True
            self.last_connection_time = datetime.now(Config.TIMEZONE).strftime("%Y%m%d %H:%M:%S")
            logger.info(f"Connected. Next Valid Order ID: {orderId}")
            
            self.resubscribe_market_data()
        else:
            logger.debug(f"Received next valid order ID: {orderId}")

    def contractDetails(self, reqId: int, contractDetails):
        """Handle contract details response including tick size"""
        try:
            symbol = self.reqId_to_symbol.get(reqId)
            if symbol:
                # Store tick size in data module
                self.data_module.set_tick_size(symbol, contractDetails.minTick)
                logger.info(f"Received tick size for {symbol}: {contractDetails.minTick}")
            
            # Put contract details in queue for other processing
            self.contract_details_queue.put(contractDetails)
            
        except Exception as e:
            logger.error(f"Error processing contract details: {e}")

    def contractDetailsEnd(self, reqId: int):
        """Mark end of contract details"""
        self.contract_details_queue.put(None)

    def tickPrice(self, reqId: TickerId, tickType: int, price: float, attrib):
        """Handle real-time price updates"""
        try:
            original_req_id = reqId
            symbol = self.reqId_to_symbol.get(original_req_id)
            
            if symbol:
                if tickType == 1:  # Bid
                    self.data_module.process_streaming_data(symbol, price, 'BID')
                elif tickType == 2:  # Ask
                    self.data_module.process_streaming_data(symbol, price, 'ASK')
                elif tickType == 4:  # Last
                    self.data_module.process_streaming_data(symbol, price, 'LAST')
        except Exception as e:
            logger.error(f"Error processing tick data: {e}")

    def orderStatus(
            self, orderId: int, status: str, filled: float,
            remaining: float, avgFillPrice: float, permId: int,
            parentId: int, lastFillPrice: float, clientId: int,
            whyHeld: str, mktCapPrice: float):
        """Handle order status updates including partial fills"""
        try:
            logger.info(f"Order {orderId} status: {status} - Filled: {filled}, Remaining: {remaining}")
            
            # Get UUID order ID from IB order ID
            uuid_order_id = self.ib_to_uuid_map.get(orderId)
            if not uuid_order_id:
                logger.error(f"No UUID found for IB order ID {orderId}")
                return
            
            with self.execution_lock:
                # Handle execution strategy status
                execution_strategy = self.active_executions.get(uuid_order_id)
                if execution_strategy:
                    execution_strategy.process_order_status(
                        status, filled, remaining, avgFillPrice
                    )

                # Normal fill processing for existing orders
                order = self.position_manager.orders.get(uuid_order_id)
                if order and filled > 0:
                    last_processed_fill = order.get('last_processed_fill', 0)
                    new_fill_amount = filled - last_processed_fill
                    
                    if new_fill_amount > 0:
                        self.position_manager.process_fill(
                            order_id=uuid_order_id,
                            new_fill_quantity=new_fill_amount,
                            fill_price=lastFillPrice
                        )
                        
                        # Update order tracking
                        self.position_manager.update_order(uuid_order_id, {
                            'last_processed_fill': filled,
                            'fill_processed': remaining == 0
                        })

        except Exception as e:
            logger.error(f"Error processing order status: {e}")

    def openOrder(self, orderId: int, contract: Contract, order: Order, orderState: OrderState):
        """Handle open order information"""
        logger.info(f"Open Order. ID: {orderId}, {contract.symbol}, {order.action}, "
                   f"{order.orderType}, {orderState.status}")

    def execDetails(self, reqId: int, contract: Contract, execution):
        """Handle execution details"""
        try:
            logger.info(
                f"Execution. ReqId: {reqId}, Symbol: {contract.symbol}, "
                f"SecType: {contract.secType}, Currency: {contract.currency}, "
                f"Execution ID: {execution.execId}, Time: {execution.time}, "
                f"Account: {execution.acctNumber}, Exchange: {execution.exchange}, "
                f"Shares: {execution.shares}, Price: {execution.price}, "
                f"OrderId: {execution.orderId}"
            )

        except Exception as e:
            logger.error(f"Error processing execution details: {e}")

    def wait_for_market_data(self, symbol: str) -> bool:
        """Wait for market data to be ready for a symbol and has valid prices
        Returns True if market data is ready with valid prices, False if timeout
        """
        start_time = time.time()
        while time.time() - start_time < self.market_data_timeout:
            try:
                data = self.data_module.streaming_data.get(symbol, {})
                tick_size = self.data_module.get_tick_size(symbol)
                
                # Get bid and ask with default of None (not 0)
                bid = data.get('bid')
                ask = data.get('ask')
                
                # First check if values exist and then compare with 0
                has_valid_prices = (
                    bid is not None and bid > 0 and 
                    ask is not None and ask > 0 and
                    tick_size is not None
                )
                
                if has_valid_prices:
                    logger.info(
                        f"Market data ready for {symbol} with valid prices: {data}, "
                        f"tick size: {tick_size}"
                    )
                    return True
                
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error checking market data for {symbol}: {e}")
                time.sleep(0.1)
        
        # Log what data we actually have when timeout occurs
        data = self.data_module.streaming_data.get(symbol, {})
        tick_size = self.data_module.get_tick_size(symbol)
        logger.warning(
            f"Timeout waiting for market data for {symbol}. "
            f"Current data: bid={data.get('bid')}, ask={data.get('ask')}, "
            f"last={data.get('last')}, tick_size={tick_size}"
        )
        return False

    def _perform_daily_cleanup(self) -> None:
        """Perform daily cleanup of market data subscriptions at 5:30 PM ET"""
        current_time = datetime.now(Config.TIMEZONE)
        if (current_time.hour == 17 and current_time.minute == 30 and 
            (self.last_cleanup_date is None or 
             current_time.date() > self.last_cleanup_date)):
            
            logger.info("Performing daily cleanup of market data subscriptions")
            self.subscribed_symbols.clear()
            self.last_cleanup_date = current_time.date()

    def _perform_daily_exercise(self) -> None:
        """Process option exercises/assignments/expirations at end of day"""
        current_time = datetime.now(Config.TIMEZONE)
        
        # Only run at 17:30 and if not already run today
        if not (current_time.hour == 17 and current_time.minute == 30):
            return
            
        # Check if already run today
        current_date = current_time.date()
        if self.last_exercise_date == current_date:
            # logger.debug("Daily exercise already processed today")
            return
            
        logger.info("Performing daily option exercise check")
        
        # Get all option positions
        positions = self.position_manager.get_all_positions()
        
        for pos_id, position in positions.items():
            try:
                # Skip if not an option or zero quantity
                if (position['instrument_type'] != 'OPTION' or 
                    position['quantity'] == 0):
                    continue
                
                # Parse expiration date
                expiry = datetime.strptime(
                    position['expiry'], 
                    '%Y-%m-%d'
                ).replace(tzinfo=Config.TIMEZONE)
                
                # Skip if not expired
                if expiry.date() > current_date:
                    continue
                    
                # Get underlying symbol
                symbol = position['symbol']
                
                # Request historical data if needed
                close_price = self.data_module.get_historical_close(symbol, expiry)
                if close_price is None:
                    self.data_module.request_historical_data(self, symbol, expiry)
                    # Wait for data (with timeout)
                    start_time = time.time()
                    while time.time() - start_time < 5:  # 5 second timeout
                        close_price = self.data_module.get_historical_close(symbol, expiry)
                        if close_price is not None:
                            break
                        time.sleep(0.1)
                    
                    if close_price is None:
                        logger.error(
                            f"Failed to get close price for {symbol} on {expiry.date()}"
                        )
                        continue
                
                # Process exercise/assignment/expiration
                self.position_manager.process_exercise(
                    symbol=symbol,
                    position=position,
                    close_price=close_price,
                    pos_id=pos_id
                )
                
            except Exception as e:
                logger.error(f"Error processing exercise for position {pos_id}: {e}")
        
        # Mark as run for today
        self.last_exercise_date = current_date
    
    def process_signals(self):
        """Process trading signals from all strategies"""
        while self.running:
            if not self.connected:
                logger.warning("Not connected to TWS. Waiting for reconnection...")
                time.sleep(5)
                continue
            
            try:
                # Check for daily cleanup
                self._perform_daily_cleanup()
                
                # Check for option exercises
                self._perform_daily_exercise()
                
                # Check for new signals
                if self.strategy_module.check_trading_time():
                    logger.info("Trading window active - fetching new signals")
                    try:
                        self.strategy_module.fetch_signals()
                    except Exception as e:
                        logger.error(f"Error in strategy_module.fetch_signals(): {str(e)}", exc_info=True)
                
                # Process any signals from any strategy
                signal = self.strategy_module.get_next_signal()
                while signal:
                    # Create symbol identifier
                    if signal['type'] == 'OPTION':
                        symbol = f"{signal['ticker']}_{signal['strike']}_{signal['expiry']}_{signal['option_type']}"
                    else:
                        symbol = signal['ticker']
                    
                    logger.info(f"Processing signal for {symbol} [{signal['type']}] [Strategy: {signal['strategy_id']}] - {signal['action']} {signal['quantity']}")
                    
                    # Request market data subscription if not already subscribed
                    self.request_market_data([symbol])
                    
                    # Wait for market data to be ready
                    if not self.wait_for_market_data(symbol):
                        logger.warning(f"Timeout waiting for market data for {symbol}. Skipping order.")
                        signal = self.strategy_module.get_next_signal()
                        continue
                    
                    while not self.connected:
                        logger.warning("Not connected to TWS. Waiting to place order...")
                        time.sleep(5)

                    # Create order info with position ID
                    order_info = self.position_manager.create_order_info(signal)
                    
                    # Create and place order
                    execution_strategy = create_execution_strategy(self, signal)
                    contract = execution_strategy.create_contract()
                    order = execution_strategy.create_order()

                    if contract and order:
                        execution_strategy.place_order(contract, order)
                        
                        # Track the execution strategy using UUID
                        with self.execution_lock:
                            self.active_executions[execution_strategy.order_id] = execution_strategy
                        
                        # Store order info with position ID and IB order ID
                        order_info['ib_order_id'] = execution_strategy.ib_order_id
                        self.position_manager.update_order(execution_strategy.order_id, order_info)
                        logger.info(f"Placed order {execution_strategy.order_id} (IB: {execution_strategy.ib_order_id}): {order_info}")
                    else:
                        logger.error(f"Failed to create order for signal: {signal}")

                    signal = self.strategy_module.get_next_signal()
                
            except Exception as e:
                logger.error(f"Error processing signals: {e}")
            
            time.sleep(1)
        
        logger.info("Signal processing thread shutting down")

    def monitor_executions(self):
        """Monitor and update active execution strategies"""
        while self.running:
            try:
                # Make a copy to avoid modification during iteration
                with self.execution_lock:
                    active_executions = dict(self.active_executions)
                
                # Check each active execution strategy
                for order_id, strategy in active_executions.items():
                    try:
                        # Check and update the strategy
                        strategy.check_and_update()
                        
                        # Remove completed strategies
                        if strategy.is_complete():
                            with self.execution_lock:
                                self.active_executions.pop(order_id, None)
                                
                    except Exception as e:
                        logger.error(f"Error checking execution strategy for order {order_id}: {e}")
                
                time.sleep(self.execution_check_interval)
                
            except Exception as e:
                logger.error(f"Error in execution monitor: {e}")
        
        logger.info("Execution monitoring thread shutting down")

    def request_market_data(self, symbols: list):
        """Request market data and contract details for multiple symbols"""
        for symbol in symbols:
            try:
                # Parse symbol to determine if it's an option
                symbol_parts = symbol.split('_')
                is_option = len(symbol_parts) == 4
                
                if is_option:
                    # Skip if already subscribed
                    if symbol in self.subscribed_symbols:
                        continue
                    
                    underlying, strike, expiry, option_type = symbol_parts
                    
                    # Subscribe to underlying if not already subscribed
                    if underlying not in self.subscribed_symbols:
                        underlying_contract = Contract()
                        underlying_contract.symbol = underlying
                        underlying_contract.secType = "STK"
                        underlying_contract.exchange = "SMART"
                        underlying_contract.currency = "USD"
                        
                        underlying_req_id = len(self.reqId_to_symbol)
                        self.reqId_to_symbol[underlying_req_id] = underlying
                        
                        # Request contract details for underlying
                        self.reqContractDetails(underlying_req_id, underlying_contract)
                        
                        self.reqMktData(
                            underlying_req_id,
                            underlying_contract,
                            "",
                            False,
                            False,
                            []
                        )
                        self.subscribed_symbols.add(underlying)
                        logger.info(f"Requested market data for underlying {underlying}")
                    
                    # Create option contract
                    contract = Contract()
                    contract.symbol = underlying
                    contract.secType = "OPT"
                    contract.strike = float(strike)
                    contract.lastTradeDateOrContractMonth = datetime.strptime(expiry, '%Y-%m-%d').strftime('%Y%m%d')
                    contract.right = "C" if option_type.upper() == "CALL" else "P"
                    contract.multiplier = "100"
                    
                else:
                    # Skip if already subscribed
                    if symbol in self.subscribed_symbols:
                        continue
                    
                    # Create stock contract
                    contract = Contract()
                    contract.symbol = symbol
                    contract.secType = "STK"
                
                contract.exchange = "SMART"
                contract.currency = "USD"
                
                # Store mapping
                req_id = len(self.reqId_to_symbol)
                self.reqId_to_symbol[req_id] = symbol
                
                # Request contract details first
                self.reqContractDetails(req_id, contract)
                
                # Request real-time data
                self.reqMktData(
                    req_id,
                    contract,
                    "",  # Generic tick types
                    False,  # Snapshot
                    False,  # Regulatory snapshot
                    []  # Market data options
                )
                
                # Track subscribed symbols
                self.subscribed_symbols.add(symbol)
                logger.info(f"Requested market data for {symbol}")
                
            except Exception as e:
                logger.error(f"Error requesting market data for {symbol}: {e}")

    def resubscribe_market_data(self):
        """Resubscribe to market data for all tracked symbols"""
        if self.subscribed_symbols:
            logger.info(f"Resubscribing to market data for {len(self.subscribed_symbols)} symbols")
            # Clear subscribed symbols before resubscribing
            symbols_to_resubscribe = list(self.subscribed_symbols)
            self.subscribed_symbols.clear()
            self.request_market_data(symbols_to_resubscribe)

    def historicalData(self, reqId: int, bar: BarData):
        """Process historical data from IBKR"""
        try:
            self.data_module.process_historical_data(reqId, bar)
        except Exception as e:
            logger.error(f"Error processing historical data: {e}")
