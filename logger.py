import logging
from logging.handlers import RotatingFileHandler
from config import Config
from datetime import datetime

def setup_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Create logs directory if it doesn't exist
    Config.LOG_DIR.mkdir(exist_ok=True)
    
    # File handler - DEBUG level for detailed troubleshooting
    file_handler = RotatingFileHandler(
        Config.LOG_FILE,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_formatter.converter = lambda *args: datetime.now(Config.TIMEZONE).timetuple()
    file_handler.setFormatter(file_formatter)
    
    # Console handler - INFO level for cleaner console output
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_formatter.converter = lambda *args: datetime.now(Config.TIMEZONE).timetuple()
    console_handler.setFormatter(console_formatter)
    
    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger