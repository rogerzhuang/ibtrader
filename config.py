import pytz
from pathlib import Path
from typing import Dict, List

class Config:
    # Connection settings
    TWS_HOST = "127.0.0.1"
    TWS_PORT = 7496
    CLIENT_ID = 1025
    
    # Trading settings
    TOTAL_CAPITAL = 100000
    
    # Timezone settings
    TIMEZONE = pytz.timezone('US/Eastern')
    
    # Logging
    LOG_DIR = Path("logs")
    LOG_FILE = LOG_DIR / "trading_system.log"
    
    # Data storage
    DATA_DIR = Path("data")
    POSITIONS_FILE = DATA_DIR / "positions.json"
    
    # Strategy configurations
    STRATEGIES = [
        {
            'type': 'PAIRS',
            'strategy_id': 'PAIRS_TRADING_001',
            'signal_base_url': "http://127.0.0.1:5000/pairtrade/signals",
            'signal_check_hour': 15,
            'signal_check_minute': 55,
            'capital_allocation_pct': 1.0,  
            'enabled': True,
            'timezone': pytz.timezone('US/Eastern'),
        },
        {
            'type': 'ZACKS',
            'strategy_id': 'ZACKS_TRADING_001',
            'api_key': 'your_zacks_api_key',
            'signal_check_hour': 9,
            'signal_check_minute': 30,
            'capital_allocation_pct': 0.0,  
            'enabled': True,
            'timezone': pytz.timezone('US/Eastern'),
        }
        # Add more strategies as needed
    ]
    
    @classmethod
    def get_enabled_strategies(cls) -> List[Dict]:
        """Return only enabled strategies with calculated capital allocation"""
        enabled_strategies = []
        for strategy in cls.STRATEGIES:
            if strategy.get('enabled', True):
                # Create a copy of the strategy config
                strategy_config = dict(strategy)
                # Calculate actual capital allocation
                strategy_config['capital_allocation'] = (
                    cls.TOTAL_CAPITAL * strategy['capital_allocation_pct']
                )
                enabled_strategies.append(strategy_config)
        return enabled_strategies
    
    @classmethod
    def get_strategy_config(cls, strategy_id: str) -> Dict:
        """Get configuration for a specific strategy with calculated capital"""
        for strategy in cls.STRATEGIES:
            if strategy['strategy_id'] == strategy_id:
                # Create a copy of the strategy config
                strategy_config = dict(strategy)
                # Calculate actual capital allocation
                strategy_config['capital_allocation'] = (
                    cls.TOTAL_CAPITAL * strategy['capital_allocation_pct']
                )
                return strategy_config
        raise ValueError(f"Strategy {strategy_id} not found in configuration")
    
    @classmethod
    def validate_capital_allocation(cls) -> bool:
        """Validate that total capital allocation doesn't exceed 100%"""
        total_allocation = sum(
            strategy['capital_allocation_pct'] 
            for strategy in cls.STRATEGIES 
            if strategy.get('enabled', True)
        )
        if total_allocation > 1.0:
            raise ValueError(
                f"Total capital allocation ({total_allocation * 100}%) "
                "exceeds 100% of available capital"
            )
        return True