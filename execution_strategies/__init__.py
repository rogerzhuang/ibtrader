from .market import IOCMarketOrderStrategy
from .dynamic_limit import DynamicLimitOrderStrategy
from .limit import LimitOrderStrategy
from .execution_base import BaseExecutionStrategy

def create_execution_strategy(trading_app, signal: dict) -> BaseExecutionStrategy:
    """Factory function to create appropriate execution strategy"""
    strategy_type = signal.get('execution_strategy', 'MARKET')  # Default to market orders
    
    if strategy_type == "MARKET":
        return IOCMarketOrderStrategy(trading_app, signal)
    elif strategy_type == "DYNAMIC_LIMIT":
        timeout = signal.get('execution_timeout', 60)  # Default 60 second timeout
        return DynamicLimitOrderStrategy(trading_app, signal, timeout)
    elif strategy_type == "LIMIT":
        return LimitOrderStrategy(trading_app, signal)
    else:
        raise ValueError(f"Unknown execution strategy type: {strategy_type}")
