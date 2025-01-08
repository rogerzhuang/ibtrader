import os
import pytz
from pathlib import Path
from typing import Dict, List

class Config:
    # Connection settings
    TWS_HOST = os.getenv('IB_HOST', '127.0.0.1')
    TWS_PORT = int(os.getenv('IB_PORT', '7496'))
    CLIENT_ID = 1025
    ACCOUNT_ID = os.getenv('IB_ACCOUNT_ID')
    
    # Trading settings
    TOTAL_CAPITAL = float(os.getenv('TOTAL_CAPITAL', '500000'))
    
    # Timezone settings
    TIMEZONE = pytz.timezone('US/Eastern')
    
    # Logging
    LOG_DIR = Path("logs")
    LOG_FILE = LOG_DIR / "trading_system.log"
    
    # Data storage
    DATA_DIR = Path("data")
    POSITIONS_FILE = DATA_DIR / "positions.json"
    ORDERS_FILE = DATA_DIR / "orders.json"

    # Strategy configurations
    STRATEGIES = [
        {
            'type': 'PAIRS',
            'strategy_id': 'PAIRS_TRADING_001',
            'signal_base_url': "http://ec2-44-231-211-145.us-west-2.compute.amazonaws.com/pairs/signals",
            'signal_check_times': [
                {'hour': 15, 'minute': 55}
            ],
            'capital_allocation_pct': float(os.getenv('PAIRS_CAPITAL_PCT', '0.2')),
            'enabled': os.getenv('PAIRS_ENABLED', 'true').lower() == 'true',
            'timezone': pytz.timezone('US/Eastern'),
        },
        {
            'type': 'OPTION_WRITE',
            'strategy_id': 'OPTION_WRITE_TRADING_001',
            'signal_base_url': "http://ec2-44-231-211-145.us-west-2.compute.amazonaws.com/options/1/signals",
            'signal_check_times': [
                {
                    'hour': 9,
                    'minute': 30,
                    'check_type': 'EXERCISE_SQUARES'  # Only check exercise/assignment positions
                },
                {
                    'hour': 13,
                    'minute': 0,
                    'check_type': 'OPTION_SIGNALS'    # Fetch new option signals
                }
            ],
            'capital_allocation_pct': float(os.getenv('OPTION_WRITE_1_CAPITAL_PCT', '0.4')),
            'enabled': os.getenv('OPTION_WRITE_1_ENABLED', 'true').lower() == 'true',
            'timezone': pytz.timezone('US/Eastern'),
        },
        {
            'type': 'OPTION_WRITE',
            'strategy_id': 'OPTION_WRITE_TRADING_002',
            'signal_base_url': "http://ec2-44-231-211-145.us-west-2.compute.amazonaws.com/options/2/signals",
            'signal_check_times': [
                {
                    'hour': 9,
                    'minute': 30,
                    'check_type': 'EXERCISE_SQUARES'  # Only check exercise/assignment positions
                },
                {
                    'hour': 9,
                    'minute': 31,
                    'check_type': 'OPTION_SIGNALS'    # Fetch new option signals
                }
            ],
            'capital_allocation_pct': float(os.getenv('OPTION_WRITE_2_CAPITAL_PCT', '0.4')),
            'enabled': os.getenv('OPTION_WRITE_2_ENABLED', 'true').lower() == 'true',
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
                    int(cls.TOTAL_CAPITAL * strategy['capital_allocation_pct'])
                )
                enabled_strategies.append(strategy_config)
        return enabled_strategies
    
    
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