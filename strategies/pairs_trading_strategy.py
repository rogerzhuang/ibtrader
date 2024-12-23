from datetime import datetime
import requests
import logging
from .base_strategy import BaseStrategy
from signal_types import SignalResponse, PairTrade, OptionTrade, TradeLeg
from logger import setup_logger

logger = setup_logger('PairsTradingStrategy')

class PairsTradingStrategy(BaseStrategy):
    def check_trading_time(self) -> bool:
        now = datetime.now(self.strategy_config['timezone'])
        should_check = (
            now.hour == self.strategy_config['signal_check_hour'] and 
            now.minute == self.strategy_config['signal_check_minute'] and
            (self.last_signal_check is None or 
             now.date() > self.last_signal_check.date())
        )
        return should_check

    def fetch_signals(self):
        date_str = datetime.now(
            self.strategy_config['timezone']
        ).strftime("%Y%m%d")
        url = (f"{self.strategy_config['signal_base_url']}/"
               f"{date_str}/{self.strategy_config['capital_allocation']}")
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Convert the raw data into proper dataclass instances
            signals = SignalResponse(
                timestamp=data['timestamp'],
                total_capital=data['total_capital'],
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
            
            logger.debug(f"Processed {len(signals.pairs_trades)} pair trades and {len(signals.options_trades)} option trades")
            
            # Process the signals and update last check time
            self.process_signals(signals)
            self.last_signal_check = datetime.now(self.strategy_config['timezone'])
            logger.info(
                f"[PAIRS:{self.strategy_id}] Successfully fetched and processed "
                f"{len(signals.pairs_trades)} pair trades and "
                f"{len(signals.options_trades)} option trades"
            )
            
            return signals
            
        except Exception as e:
            logger.error(
                f"[PAIRS:{self.strategy_id}] Error fetching signals: {e}", 
                exc_info=True
            )
            raise

    def process_signals(self, signals: SignalResponse):
        try:
            # Process pairs trades
            for pair_trade in signals.pairs_trades:
                if pair_trade.action == "TRADE":
                    for leg in pair_trade.legs:
                        current_position = self.execution_module.get_position(
                            leg.ticker,
                            strategy_id=self.strategy_id,
                            instrument_type='STOCK'
                        )
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
                                'order_type': 'MKT',
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
                        current_position = self.execution_module.get_position(
                            symbol,
                            strategy_id=self.strategy_id,
                            instrument_type='STOCK'
                        )
                        current_quantity = current_position.get('quantity', 0)
                        if current_quantity != 0:
                            action = 'SELL' if current_quantity > 0 else 'BUY'
                            self.signal_queue.put({
                                'type': 'STOCK',
                                'ticker': symbol,
                                'action': action,
                                'quantity': abs(current_quantity),
                                'order_type': 'MKT',
                                'pair_id': pair_trade.pair,
                                'strategy_id': self.strategy_id
                            })
                            logger.info(
                                f"[PAIRS:{self.strategy_id}] Closing position: "
                                f"{symbol} {action} {abs(current_quantity)}"
                            )

            # Process options trades
            for option_trade in signals.options_trades:
                self.signal_queue.put({
                    'type': 'OPTION',
                    'ticker': option_trade.contract.split()[0],
                    'action': option_trade.action,
                    'quantity': option_trade.contracts,
                    'order_type': 'MKT',
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

