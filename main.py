import sys
import threading
import time
import logging
from pathlib import Path
from datetime import datetime
import pytz
from flask import Flask, Response, request

from config import Config
from trading_app import TradingApp
from logger import setup_logger
from web.routes import logs

logger = setup_logger('main')

def create_app():
    app = Flask(__name__, 
                template_folder='web/templates')
    # Pass Config to the blueprint
    app.register_blueprint(logs, url_prefix='', config=Config)
    return app

def start_flask_server():
    """Start Flask server in debug mode"""
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=False)

def start_client_thread(app):
    """Start the client thread and return it"""
    client_thread = threading.Thread(target=lambda: app.run())
    client_thread.start()
    logger.info("Started client thread")
    return client_thread

def attempt_connection(app, current_thread=None, is_reconnect=False):
    """Attempt to connect to TWS and return new client thread
    Args:
        app: TradingApp instance
        current_thread: Optional current client thread to join
        is_reconnect: Boolean indicating if this is a reconnection attempt
    Returns:
        new client thread if successful
    """
    action = "Reconnection" if is_reconnect else "Connection"
    
    while True:
        should_retry = app.connect_and_wait()
        
        if not should_retry:  # Connection successful
            logger.info(f"{action} successful")
            return current_thread if current_thread else start_client_thread(app)
        
        if current_thread:
            logger.info("Restarting client thread...")
            current_thread.join(timeout=5)
        
        current_thread = start_client_thread(app)

def main():
    logger.info("Starting Trading System")
    
    try:
        # Initialize trading app
        app = TradingApp()
        
        # Start Flask server in a separate thread
        flask_thread = threading.Thread(target=start_flask_server)
        flask_thread.daemon = True
        flask_thread.start()
        logger.info("Started Flask server thread")
        
        logger.info(f"Connecting to {Config.TWS_HOST}:{Config.TWS_PORT}")
        # Initial connection attempt
        client_thread = attempt_connection(app)
        
        # Start the signal processing thread
        signal_thread = threading.Thread(target=app.process_signals)
        signal_thread.start()
        logger.info("Started signal processing thread")
        
        # Start the execution monitor thread
        execution_monitor = threading.Thread(target=app.monitor_executions)
        execution_monitor.start()
        logger.info("Started execution monitor thread")
        
        # Monitor threads and app status
        try:
            while True:
                if not client_thread.is_alive():
                    logger.warning("Client thread has died - attempting reconnection")
                    client_thread = attempt_connection(app, client_thread, is_reconnect=True)
                
                if not signal_thread.is_alive():
                    logger.error("Signal thread has died - shutting down")
                    break
                
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        
        finally:
            # Clean shutdown
            logger.info("Initiating shutdown")
            app.shutdown()
            logger.info("Disconnected from TWS")
            
            # Wait for all threads to finish
            threads = [
                ("client thread", client_thread),
                ("signal thread", signal_thread),
                ("execution monitor thread", execution_monitor),
                ("flask server thread", flask_thread)
            ]
            
            for thread_name, thread in threads:
                logger.info(f"Waiting for {thread_name} to finish...")
                thread.join(timeout=5)
                if thread.is_alive():
                    logger.warning(f"{thread_name.capitalize()} did not shut down cleanly")
            
            logger.info("Threads terminated")
            
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
        
    logger.info("Trading System shutdown complete")

if __name__ == "__main__":
    main()