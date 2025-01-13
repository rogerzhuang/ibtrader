from datetime import datetime, timedelta
import requests
import logging
from ..strategy_base import BaseStrategy
from .option_write_signal_types import SignalResponse, OptionTrade, ExerciseSquare
from logger import setup_logger

logger = setup_logger('OptionWriteStrategy')

class OptionWriteStrategy(BaseStrategy):
    def check_trading_time(self, update_timestamp=False) -> tuple[bool, dict | None]:
        now = datetime.now(self.strategy_config['timezone'])
        current_date = now.date()

        for check_time in self.strategy_config['signal_check_times']:
            if (now.hour == check_time['hour'] and 
                now.minute == check_time['minute']):
                
                check_key = f"{check_time['hour']}:{check_time['minute']}"
                last_check = self.last_signal_checks.get(check_key)
                
                if last_check is None or current_date > last_check.date():
                    if update_timestamp:
                        self.last_signal_checks[check_key] = now
                        logger.info(
                            f"[OPTION_WRITE:{self.strategy_id}] Processing signals for "
                            f"check time {check_time['hour']:02d}:{check_time['minute']:02d}"
                        )
                    return True, check_time
        return False, None

    def fetch_signals(self):
        should_process, current_check = self.check_trading_time(update_timestamp=True)
        if not should_process or not current_check:
            return SignalResponse(options_trades=[])

        check_type = current_check.get('check_type', 'ALL')  # Default to ALL for backward compatibility
        logger.info(
            f"[OPTION_WRITE:{self.strategy_id}] Processing {check_type} "
            f"at {current_check['hour']:02d}:{current_check['minute']:02d}"
        )

        try:
            # Add random delay before fetching
            delay = self._apply_random_delay()
            logger.info(f"[OPTION_WRITE:{self.strategy_id}] Applying {delay:.2f}s delay before fetching signals")

            signals = SignalResponse(options_trades=[])

            # Process exercise/assignment positions if needed
            if check_type in ['ALL', 'EXERCISE_SQUARES']:
                positions = self.position_manager.get_all_positions(self.strategy_id)
                
                for pos_id, position in positions.items():
                    if (position['instrument_type'] == 'STOCK' and 
                        position.get('quantity', 0) != 0):
                        
                        last_updated = datetime.fromisoformat(position['last_updated'])
                        position_age = (datetime.now(self.strategy_config['timezone']) - last_updated).days
                        
                        signals.exercise_squares.append(
                            ExerciseSquare(
                                symbol=position['symbol'],
                                action='SELL' if position['quantity'] > 0 else 'BUY',
                                quantity=abs(position['quantity']),
                                avg_price=position['avg_price'],
                                position_age=position_age
                            )
                        )
                        logger.info(
                            f"[OPTION_WRITE:{self.strategy_id}] Adding stock square signal for "
                            f"exercised position: {position['symbol']} "
                            f"{'SELL' if position['quantity'] > 0 else 'BUY'} "
                            f"{abs(position['quantity'])} shares (age: {position_age} days)"
                        )

            # Fetch option signals if needed
            if check_type in ['ALL', 'OPTION_SIGNALS']:
                response = requests.get(f"{self.strategy_config['signal_base_url']}/{datetime.now(self.strategy_config['timezone']).strftime('%Y%m%d')}/{self.strategy_config['capital_allocation']}")
                response.raise_for_status()
                data = response.json()
                
                signals.options_trades = [
                    OptionTrade(
                        action=trade['action'],
                        allocation=trade['allocation'],
                        contract=trade['contract'],
                        contracts=trade['contracts'],
                        expiry=trade['expiry'],
                        iv=trade['iv'],
                        premium=trade['premium'],
                        strike=trade['strike']
                    )
                    for trade in data['options_trades']
                ]
            
            logger.debug(
                f"Processed {len(signals.options_trades)} option trades and "
                f"{len(signals.exercise_squares)} stock squares"
            )
            
            # Process the signals
            self.process_signals(signals)
            logger.info(
                f"[OPTION_WRITE:{self.strategy_id}] Successfully processed signals for "
                f"check time {current_check['hour']:02d}:{current_check['minute']:02d}"
            )
            
            return signals
            
        except Exception as e:
            logger.error(f"[OPTION_WRITE:{self.strategy_id}] Error fetching signals: {e}")
            raise

    def process_signals(self, signals: SignalResponse):
        try:
            # Process option trades
            for option_trade in signals.options_trades:
                # Skip trades with 0 contracts
                if option_trade.contracts <= 0:
                    logger.info(
                        f"[OPTION_WRITE:{self.strategy_id}] Skipping zero-contract trade: "
                        f"{option_trade.contract} {option_trade.strike} {option_trade.expiry}"
                    )
                    continue

                # Extract underlying ticker and option type from the contract string
                contract_parts = option_trade.contract.split()
                ticker = contract_parts[0]
                option_type = contract_parts[1]  # "PUT" or "CALL"
                
                # Create signal for option trade
                self.signal_queue.put({
                    'type': 'OPTION',
                    'ticker': ticker,
                    'action': option_trade.action,
                    'quantity': option_trade.contracts,
                    'execution_strategy': 'DYNAMIC_LIMIT',
                    'strike': option_trade.strike,
                    'expiry': option_trade.expiry,
                    'option_type': option_type,
                    'strategy_id': self.strategy_id
                })
                logger.info(
                    f"[OPTION_WRITE:{self.strategy_id}] New option trade: "
                    f"{ticker} {option_type} {option_trade.strike} {option_trade.expiry} "
                    f"{option_trade.action} {option_trade.contracts}"
                )

            # Process exercise squares
            for square in signals.exercise_squares:
                # Choose execution strategy based on position age
                execution_strategy = 'MARKET' if square.position_age > 21 else 'LIMIT'
                
                signal = {
                    'type': 'STOCK',
                    'ticker': square.symbol,
                    'action': square.action,
                    'quantity': square.quantity,
                    'execution_strategy': execution_strategy,
                    'strategy_id': self.strategy_id
                }
                
                # Add limit price for LIMIT orders
                if execution_strategy == 'LIMIT':
                    signal['limit_price'] = square.avg_price
                
                self.signal_queue.put(signal)
                logger.info(
                    f"[OPTION_WRITE:{self.strategy_id}] New stock square: "
                    f"{square.symbol} {square.action} {square.quantity} shares "
                    f"using {execution_strategy} strategy "
                    f"(age: {square.position_age} days"
                    f"{', limit: ' + str(square.avg_price) if execution_strategy == 'LIMIT' else ''})"
                )
                
        except Exception as e:
            logger.error(f"[OPTION_WRITE:{self.strategy_id}] Error processing signals: {e}")
