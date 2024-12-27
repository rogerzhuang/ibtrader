from datetime import datetime
import requests
import logging
from ..strategy_base import BaseStrategy
from .option_write_signal_types import SignalResponse, OptionTrade
from logger import setup_logger

logger = setup_logger('OptionWriteStrategy')

class OptionWriteStrategy(BaseStrategy):
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
                options_trades=[
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
            )
            
            logger.debug(f"Processed {len(signals.options_trades)} option write trades")
            
            # Process the signals and update last check time
            self.process_signals(signals)
            self.last_signal_check = datetime.now(self.strategy_config['timezone'])
            logger.info(
                f"[OPTION_WRITE:{self.strategy_id}] Successfully fetched and processed "
                f"{len(signals.options_trades)} option write trades"
            )
            
            return signals
            
        except Exception as e:
            logger.error(
                f"[OPTION_WRITE:{self.strategy_id}] Error fetching signals: {e}", 
                exc_info=True
            )
            raise

    def process_signals(self, signals: SignalResponse):
        try:
            for option_trade in signals.options_trades:
                # Extract underlying ticker and option type from the contract string
                contract_parts = option_trade.contract.split()
                ticker = contract_parts[0]
                option_type = contract_parts[1]  # "PUT" or "CALL"
                
                # Create signal directly from the option trade
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
                
        except Exception as e:
            logger.error(
                f"[OPTION_WRITE:{self.strategy_id}] Error processing signals: {e}",
                exc_info=True
            )
