from abc import ABC, abstractmethod
from typing import Dict, Any
from queue import Queue
import random
import time

class BaseStrategy(ABC):
    # Class-level constants for delay configuration
    MIN_DELAY_SECONDS = 0
    MAX_DELAY_SECONDS = 30

    def __init__(self, data_module, position_manager, strategy_config: Dict[str, Any]):
        self.data_module = data_module
        self.position_manager = position_manager
        self.strategy_config = strategy_config
        self.signal_queue = Queue()
        self.strategy_id = strategy_config['strategy_id']
        self.last_signal_checks = {}  # Track last check for each time slot

    def _apply_random_delay(self):
        """Apply a random delay before fetching signals"""
        delay = random.uniform(self.MIN_DELAY_SECONDS, self.MAX_DELAY_SECONDS)
        time.sleep(delay)
        return delay

    @abstractmethod
    def check_trading_time(self) -> bool:
        """Check if it's time to fetch signals for this strategy"""
        pass

    @abstractmethod
    def fetch_signals(self):
        """Fetch signals specific to this strategy"""
        pass

    @abstractmethod
    def process_signals(self, signals: Any):
        """Process signals for this strategy"""
        pass
