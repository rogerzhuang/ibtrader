from ibapi.client import EClient
from ibapi.wrapper import EWrapper
import time

class TestApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        
    def error(self, reqId, errorCode, errorString):
        print(f"Error {errorCode}: {errorString}")
        
    def connectionClosed(self):
        print("Connection closed")

def main():
    app = TestApp()
    app.connect("127.0.0.1", 7496, clientId=1)
    
    # Start the socket in a thread
    import threading
    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()
    
    time.sleep(3)  # Give time for connection to establish
    
    if app.isConnected():
        print("Successfully connected to TWS")
    else:
        print("Failed to connect to TWS")
    
    app.disconnect()

if __name__ == "__main__":
    main() 