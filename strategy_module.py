from typing import List, Dict
from strategies.strategy_base import BaseStrategy
from strategies.pairs_strategy import PairsTradingStrategy
import logging
from logger import setup_logger

logger = setup_logger('StrategyModule')

class StrategyModule:
    def __init__(self, data_module, position_manager):
        self.data_module = data_module
        self.position_manager = position_manager
        self.strategies: Dict[str, BaseStrategy] = {}
        
    def initialize_strategies(self, strategy_configs: List[Dict]):
        """Initialize strategies based on configuration"""
        strategy_classes = {
            'PAIRS': PairsTradingStrategy,
            # Add more strategy classes here
        }
        
        for config in strategy_configs:
            strategy_type = config['type']
            if strategy_type in strategy_classes:
                strategy = strategy_classes[strategy_type](
                    self.data_module,
                    self.position_manager,
                    config
                )
                self.strategies[config['strategy_id']] = strategy

    def check_trading_time(self) -> bool:
        """Check if any strategy needs to fetch signals"""
        return any(strategy.check_trading_time() for strategy in self.strategies.values())

    def fetch_signals(self):
        """Fetch signals for all strategies that need updating"""
        for strategy in self.strategies.values():
            if strategy.check_trading_time():
                strategy.fetch_signals()

    def get_next_signal(self):
        """Get the next signal from any strategy that has one"""
        for strategy_id, strategy in self.strategies.items():
            try:
                if not strategy.signal_queue.empty():
                    signal = strategy.signal_queue.get()
                    logger.debug(f"Got signal from strategy {strategy_id}: {signal}")
                    return signal
            except Exception as e:
                logger.error(f"Error getting signal from strategy {strategy_id}: {e}")
        return None