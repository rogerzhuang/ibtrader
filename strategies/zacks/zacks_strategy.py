from datetime import datetime
import requests
import logging
from ..strategy_base import BaseStrategy
from .zacks_signal_types import SignalResponse, ZacksSignal, Position
from logger import setup_logger

logger = setup_logger('ZacksStrategy')

class ZacksStrategy(BaseStrategy):
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
                            f"[ZACKS:{self.strategy_id}] Processing signals for "
                            f"check time {check_time['hour']:02d}:{check_time['minute']:02d}"
                        )
                    return True, check_time
        return False, None

    def fetch_signals(self):
        should_process, current_check = self.check_trading_time(update_timestamp=True)
        if not should_process or not current_check:
            return SignalResponse(zacks_trades=[])
        
        try:
            delay = self._apply_random_delay()
            logger.info(f"[ZACKS:{self.strategy_id}] Applying {delay:.2f}s delay before fetching signals")

            current_time = datetime.now(self.strategy_config['timezone'])
            date_str = current_time.strftime("%Y%m%d")
            url = (f"{self.strategy_config['signal_base_url']}/"
                   f"{date_str}/{self.strategy_config['capital_allocation']}")
            
            logger.info(
                f"[ZACKS:{self.strategy_id}] Processing signals "
                f"at {current_check['hour']:02d}:{current_check['minute']:02d}"
            )

            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Convert the raw data into proper dataclass instances
            signals = SignalResponse(
                zacks_trades=[
                    Position(
                        ticker=position['ticker'],
                        shares=position['shares'],
                        price=position['price'],
                        allocation=position['allocation'],
                        weight=position['weight']
                    )
                    for position in data['positions']
                ]
            )
            
            logger.debug(f"Processed {len(signals.zacks_trades)} target positions")
            
            self.process_signals(signals)
            logger.info(
                f"[ZACKS:{self.strategy_id}] Successfully processed signals for "
                f"check time {current_check['hour']:02d}:{current_check['minute']:02d}"
            )
            
            return signals
            
        except Exception as e:
            logger.error(f"[ZACKS:{self.strategy_id}] Error fetching signals: {e}", exc_info=True)
            raise

    def process_signals(self, signals: SignalResponse):
        try:
            # Calculate total position value first
            total_position_value = sum(
                abs(pos.shares) * pos.price 
                for pos in signals.zacks_trades
            )

            # Calculate position differences and total order value
            position_changes = []
            total_order_value = 0

            for target_position in signals.zacks_trades:
                position_id = self.position_manager.find_matching_position(
                    target_position.ticker,
                    instrument_type='STOCK',
                    strategy_id=self.strategy_id
                )
                
                current_position = self.position_manager.positions.get(position_id, {
                    'quantity': 0,
                    'avg_price': 0
                })
                current_quantity = current_position.get('quantity', 0)
                
                position_difference = target_position.shares - current_quantity
                if position_difference != 0:
                    order_value = abs(position_difference) * target_position.price
                    total_order_value += order_value
                    position_changes.append({
                        'ticker': target_position.ticker,
                        'difference': position_difference,
                        'target': target_position.shares,
                        'current': current_quantity
                    })

            # Check if total order value exceeds threshold
            order_value_threshold = 0.5 * total_position_value
            should_process = total_order_value > order_value_threshold

            logger.info(
                f"[ZACKS:{self.strategy_id}] {'Processing' if should_process else 'Skipping'} trades: "
                f"order value (${total_order_value:.2f}) "
                f"{'exceeds' if should_process else 'below'} threshold "
                f"(${order_value_threshold:.2f})"
            )

            if should_process:
                for change in position_changes:
                    action = 'BUY' if change['difference'] > 0 else 'SELL'
                    self.signal_queue.put({
                        'type': 'STOCK',
                        'ticker': change['ticker'],
                        'action': action,
                        'quantity': abs(change['difference']),
                        'execution_strategy': 'MARKET',
                        'strategy_id': self.strategy_id
                    })
                    logger.info(
                        f"[ZACKS:{self.strategy_id}] Adjusting position: "
                        f"{change['ticker']} {action} {abs(change['difference'])} "
                        f"(Target: {change['target']}, Current: {change['current']})"
                    )
            
        except Exception as e:
            logger.error(
                f"[ZACKS:{self.strategy_id}] Error processing signals: {e}",
                exc_info=True
            )
