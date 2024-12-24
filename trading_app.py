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
from ibapi.common import TickerId
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
        self.market_data_ready = {}  # Dict to track if market data is received for each symbol
        self.market_data_timeout = 5  # seconds to wait for market data

        # Add daily cleanup tracking
        self.last_cleanup_date = None

        # Add execution strategy tracking
        self.active_executions = {}  # order_id -> execution_strategy
        self.execution_check_interval = 1  # Check executions every second
    
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
        """Handle contract details response"""
        self.contract_details_queue.put(contractDetails)

    def contractDetailsEnd(self, reqId: int):
        """Mark end of contract details"""
        self.contract_details_queue.put(None)

    def tickPrice(self, reqId: TickerId, tickType: int, price: float, attrib):
        """Handle real-time price updates"""
        try:
            original_req_id = reqId
            symbol = self.reqId_to_symbol.get(original_req_id)
            
            if symbol:
                # Mark market data as ready when we receive first tick
                if symbol not in self.market_data_ready:
                    self.market_data_ready[symbol] = True
                    logger.info(f"Market data ready for {symbol}")
                
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
            
            # Add execution strategy status processing
            with self.execution_lock:
                execution_strategy = self.active_executions.get(orderId)
            if execution_strategy:
                execution_strategy.process_order_status(
                    status, filled, remaining, avgFillPrice
                )
            
            # Process fills (both partial and complete)
            if filled > 0:  # Any fill amount
                # Get order info
                order = self.position_manager.orders.get(orderId)
                logger.debug(f"Looking for order {orderId}, found: {order}")
                
                if order:
                    symbol = order['symbol']
                    strategy_id = order.get('strategy_id')
                    instrument_type = order['instrument_type']
                    
                    # Track last processed fill to handle only new fills
                    last_processed_fill = order.get('last_processed_fill', 0)
                    new_fill_amount = filled - last_processed_fill
                    
                    if new_fill_amount > 0:  # Only process if there's new fill amount
                        current_position = self.position_manager.get_position(
                            symbol, 
                            strategy_id,
                            instrument_type=instrument_type
                        )
                        logger.debug(
                            f"Processing position update for {symbol} - "
                            f"Current: {current_position.get('quantity', 0)} @ "
                            f"{current_position.get('avg_price', 0)}"
                        )
                        
                        # Calculate position updates
                        filled_quantity = (
                            new_fill_amount if order['action'] == 'BUY' 
                            else -new_fill_amount
                        )
                        current_quantity = current_position.get('quantity', 0)
                        current_avg_price = current_position.get('avg_price', 0)
                        new_quantity = current_quantity + filled_quantity
                        
                        if new_quantity != 0:
                            if current_quantity * new_quantity > 0:  # Same direction
                                if abs(new_quantity) > abs(current_quantity):  # Adding to position
                                    # Weighted average of old and new
                                    new_avg_price = (
                                        (abs(current_quantity) * current_avg_price) + 
                                        (abs(filled_quantity) * lastFillPrice)
                                    ) / abs(new_quantity)
                                else:  # Reducing position
                                    # Keep original average price
                                    new_avg_price = current_avg_price
                            else:  # Direction changed (crossed zero)
                                # Use new fill price when crossing zero
                                new_avg_price = lastFillPrice
                        else:  # Position closed
                            new_avg_price = 0
                        
                        # Prepare position update with additional information
                        update_info = {
                            'pair_id': order.get('pair_id')
                        }

                        # Add instrument-specific details
                        if instrument_type == 'OPTION':
                            update_info.update({
                                'strike': order['strike'],
                                'expiry': order['expiry'],
                                'option_type': order['option_type']
                            })
                        
                        # Update position
                        self.position_manager.update_position(
                            symbol,
                            new_quantity,
                            new_avg_price,
                            instrument_type,
                            strategy_id,
                            **update_info
                        )
                        
                        # Update last processed fill
                        order['last_processed_fill'] = filled
                        
                        # Mark order as completed if fully filled
                        if remaining == 0:
                            order['fill_processed'] = True
                            
        except Exception as e:
            logger.error(f"Error processing order status: {e}")

    def openOrder(self, orderId: int, contract: Contract, order: Order, orderState: OrderState):
        """Handle open order information"""
        logger.info(f"Open Order. ID: {orderId}, {contract.symbol}, {order.action}, "
                   f"{order.orderType}, {orderState.status}")

    def execDetails(self, reqId: int, contract: Contract, execution):
        """Handle execution details"""
        logger.info(
            f"Execution. ReqId: {reqId}, Symbol: {contract.symbol}, "
            f"SecType: {contract.secType}, Currency: {contract.currency}, "
            f"Execution ID: {execution.execId}, Time: {execution.time}, "
            f"Account: {execution.acctNumber}, Exchange: {execution.exchange}, "
            f"Shares: {execution.shares}, Price: {execution.price}"
        )

    def wait_for_market_data(self, symbol: str) -> bool:
        """Wait for market data to be ready for a symbol
        Returns True if market data is ready, False if timeout
        """
        start_time = time.time()
        while time.time() - start_time < self.market_data_timeout:
            if self.market_data_ready.get(symbol, False):
                return True
            time.sleep(0.1)
        return False

    def _perform_daily_cleanup(self) -> None:
        """Perform daily cleanup of market data subscriptions at 5:30 PM ET"""
        current_time = datetime.now(Config.TIMEZONE)
        if (current_time.hour == 17 and current_time.minute == 30 and 
            (self.last_cleanup_date is None or 
             current_time.date() > self.last_cleanup_date)):
            
            logger.info("Performing daily cleanup of market data subscriptions")
            self.subscribed_symbols.clear()
            self.market_data_ready.clear()
            self.last_cleanup_date = current_time.date()

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
                
                # Check for new signals
                if self.strategy_module.check_trading_time():
                    logger.info("Trading window active - fetching new signals")
                    self.strategy_module.fetch_signals()
                
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

                    # Replace the old order placement code with execution strategy
                    execution_strategy = create_execution_strategy(self, signal)
                    contract = execution_strategy.create_contract()
                    order = execution_strategy.create_order()

                    if contract and order:
                        execution_strategy.place_order(contract, order)
                        
                        # Track the execution strategy
                        with self.execution_lock:
                            self.active_executions[execution_strategy.order_id] = execution_strategy
                        
                        # Store generic order information
                        order_info = {
                            'symbol': signal['ticker'],
                            'action': signal['action'],
                            'quantity': signal['quantity'],
                            'strategy_id': signal['strategy_id'],
                            'instrument_type': signal['type'],
                            'timestamp': datetime.now(Config.TIMEZONE).isoformat(),
                            'last_processed_fill': 0,  # Initialize last_processed_fill
                            'fill_processed': False,
                            'execution_type': signal['execution_strategy']  # Store the execution strategy type
                        }
                        
                        # Add optional fields based on order type
                        if signal['type'] == 'OPTION':
                            order_info.update({
                                'strike': signal['strike'],
                                'expiry': signal['expiry'],
                                'option_type': signal['option_type']
                            })
                        
                        # Add strategy-specific fields if present
                        if signal.get('pair_id'):
                            order_info['pair_id'] = signal['pair_id']
                        
                        self.position_manager.orders[execution_strategy.order_id] = order_info
                        logger.info(f"Placed order {execution_strategy.order_id}: {order_info}")
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

    def request_market_data(self, symbols: list):
        """Request market data for multiple symbols"""
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
            # Also clear market data ready flags
            self.market_data_ready.clear()
            self.request_market_data(symbols_to_resubscribe)
