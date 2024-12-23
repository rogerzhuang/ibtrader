from abc import ABC, abstractmethod
from typing import Dict, Any
from queue import Queue

class BaseStrategy(ABC):
    def __init__(self, data_module, execution_module, strategy_config: Dict[str, Any]):
        self.data_module = data_module
        self.execution_module = execution_module
        self.strategy_config = strategy_config
        self.signal_queue = Queue()
        self.last_signal_check = None
        self.strategy_id = strategy_config['strategy_id']

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
