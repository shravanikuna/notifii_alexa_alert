# demo.py - Run this to demonstrate the full flow
import requests
import json
from datetime import datetime

def send_test_webhook():
    """Send a test webhook to your local server"""
    
    url = "http://localhost:5000/webhook/package-delivered"
    
    payload = {
        "event": "package.delivered",
        "data": {
            "package_id": "PKG-DEMO-001",
            "unit": "4B",
            "carrier": "FedEx",
            "tracking_number": "123456789",
            "compartment": "B4",
            "delivered_at": datetime.now().isoformat()
        }
    }
    
    response = requests.post(url, json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")

if __name__ == "__main__":
    print("🚀 Sending test webhook...")
    send_test_webhook()