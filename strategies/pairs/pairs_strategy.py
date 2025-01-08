from datetime import datetime
import requests
import logging
from ..strategy_base import BaseStrategy
from .pairs_signal_types import SignalResponse, PairTrade, OptionTrade, TradeLeg
from logger import setup_logger

logger = setup_logger('PairsTradingStrategy')

class PairsTradingStrategy(BaseStrategy):
    def check_trading_time(self, update_timestamp=False) -> tuple[bool, dict | None]:
        now = datetime.now(self.strategy_config['timezone'])
        current_date = now.date()

        for check_time in self.strategy_config['signal_check_times']:
            if (now.hour == check_time['hour'] and 
                now.minute == check_time['minute']):
                
                # Create unique key for this check time
                check_key = f"{check_time['hour']}:{check_time['minute']}"
                last_check = self.last_signal_checks.get(check_key)
                
                if last_check is None or current_date > last_check.date():
                    if update_timestamp:
                        self.last_signal_checks[check_key] = now
                        logger.info(
                            f"[PAIRS:{self.strategy_id}] Processing signals for "
                            f"check time {check_time['hour']:02d}:{check_time['minute']:02d}"
                        )
                    return True, check_time
        return False, None

    def fetch_signals(self):
        should_process, current_check = self.check_trading_time(update_timestamp=True)
        if not should_process or not current_check:
            return SignalResponse(pairs_trades=[], options_trades=[])
        
        try:
            # Add random delay before fetching
            delay = self._apply_random_delay()
            logger.info(f"[PAIRS:{self.strategy_id}] Applying {delay:.2f}s delay before fetching signals")

            current_time = datetime.now(self.strategy_config['timezone'])
            date_str = current_time.strftime("%Y%m%d")
            url = (f"{self.strategy_config['signal_base_url']}/"
                   f"{date_str}/{self.strategy_config['capital_allocation']}")
            
            logger.info(
                f"[PAIRS:{self.strategy_id}] Processing signals "
                f"at {current_check['hour']:02d}:{current_check['minute']:02d}"
            )

            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Convert the raw data into proper dataclass instances
            signals = SignalResponse(
                pairs_trades=[
                    PairTrade(
                        pair=trade['pair'],
                        action=trade['action'],
                        legs=[
                            TradeLeg(
                                ticker=leg['ticker'],
                                action=leg['action'],
                                quantity=leg['quantity'],
                                price=leg['price']
                            ) if leg else []
                            for leg in trade['legs']
                        ]
                    ) 
                    for trade in data['pairs_trades']
                ],
                options_trades=[
                    OptionTrade(
                        pair=trade['pair'],
                        contract=trade['contract'],
                        action=trade['action'],
                        strike=trade['strike'],
                        contracts=trade['contracts'],
                        expiry=trade['expiry'],
                        premium_target=trade['premium_target']
                    )
                    for trade in data['options_trades']
                ]
            )
            
            logger.debug(
                f"Processed {len(signals.pairs_trades)} pair trades and "
                f"{len(signals.options_trades)} option trades"
            )
            
            # Process the signals
            self.process_signals(signals)
            logger.info(
                f"[PAIRS:{self.strategy_id}] Successfully processed signals for "
                f"check time {current_check['hour']:02d}:{current_check['minute']:02d}"
            )
            
            return signals
            
        except Exception as e:
            logger.error(f"[PAIRS:{self.strategy_id}] Error fetching signals: {e}", exc_info=True)
            raise

    def process_signals(self, signals: SignalResponse):
        try:
            # Process pairs trades
            for pair_trade in signals.pairs_trades:
                if pair_trade.action == "TRADE":
                    for leg in pair_trade.legs:
                        # Find position ID for this exact instrument
                        position_id = self.position_manager.find_matching_position(
                            leg.ticker,
                            instrument_type='STOCK',
                            strategy_id=self.strategy_id
                        )
                        
                        # Get current position directly from positions dict
                        current_position = self.position_manager.positions.get(position_id, {
                            'quantity': 0,
                            'avg_price': 0
                        })
                        current_quantity = current_position.get('quantity', 0)
                        
                        target_position = (-leg.quantity if leg.action == "SELL" 
                                         else leg.quantity)
                        position_difference = target_position - current_quantity
                        
                        if position_difference != 0:
                            action = 'BUY' if position_difference > 0 else 'SELL'
                            self.signal_queue.put({
                                'type': 'STOCK',
                                'ticker': leg.ticker,
                                'action': action,
                                'quantity': abs(position_difference),
                                'execution_strategy': 'MARKET',
                                'pair_id': pair_trade.pair,
                                'strategy_id': self.strategy_id
                            })
                            logger.info(
                                f"[PAIRS:{self.strategy_id}] New position: "
                                f"{leg.ticker} {action} {abs(position_difference)}"
                            )
                        
                elif pair_trade.action == "SQUARE":
                    pair_symbols = pair_trade.pair.split('/')
                    for symbol in pair_symbols:
                        # Find position ID for this exact instrument
                        position_id = self.position_manager.find_matching_position(
                            symbol,
                            instrument_type='STOCK',
                            strategy_id=self.strategy_id
                        )
                        
                        if position_id:  # Only act if position exists
                            # Get current position directly from positions dict
                            current_position = self.position_manager.positions.get(position_id)
                            current_quantity = current_position.get('quantity', 0)
                            
                            if current_quantity != 0:
                                action = 'SELL' if current_quantity > 0 else 'BUY'
                                self.signal_queue.put({
                                    'type': 'STOCK',
                                    'ticker': symbol,
                                    'action': action,
                                    'quantity': abs(current_quantity),
                                    'execution_strategy': 'MARKET',
                                    'pair_id': pair_trade.pair,
                                    'strategy_id': self.strategy_id
                                })
                                logger.info(
                                    f"[PAIRS:{self.strategy_id}] Closing position: "
                                    f"{symbol} {action} {abs(current_quantity)}"
                                )

            # Process options trades
            for option_trade in signals.options_trades:
                # Create signal with all option details
                self.signal_queue.put({
                    'type': 'OPTION',
                    'ticker': option_trade.contract.split()[0],
                    'action': option_trade.action,
                    'quantity': option_trade.contracts,
                    'execution_strategy': 'MARKET',
                    'strike': option_trade.strike,
                    'expiry': option_trade.expiry,
                    'option_type': option_trade.contract.split()[1],
                    'pair_id': option_trade.pair,
                    'strategy_id': self.strategy_id
                })
                logger.info(
                    f"[PAIRS:{self.strategy_id}] New option trade: "
                    f"{option_trade.contract}"
                )
                
        except Exception as e:
            logger.error(
                f"[PAIRS:{self.strategy_id}] Error processing signals: {e}",
                exc_info=True
            )

