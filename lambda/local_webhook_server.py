#!/usr/bin/env python3
"""
Local webhook server for testing Notifii package events with AI Agent.
Run this script locally to receive and process webhooks from Postman.
"""

import json
import logging
import os
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================
# CONFIGURATION - CONFIGURING REAL CREDENTIALS
# ============================================

# 1. READ YOUR SKILL MESSAGING CREDENTIALS (FROM PERMISSIONS TAB)
ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', 'YOUR_SKILL_MESSAGING_CLIENT_ID')
ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', 'YOUR_SKILL_MESSAGING_CLIENT_SECRET')

# 2. YOUR REAL ALEXA USER ID FROM DEVELOPMENT TEST TAB
REAL_ALEXA_USER_ID = "amzn1.ask.account.AMAUHAHRXHZZR5MRPWPMBML6XKO7QLJEKDRQEHSZRTQCUW3W6PPR6X2MEHWP5MEP4BKXFQ5XQKUOFPQDM23EMWZY2G4CRNDOTIPCK3UKZOXOKJD47R63EQA3YOPT5NVLKBRU4FIDK7ZDK7S5LWLH6MNJTPENDSYTNEU42JVS77NR5I3NK2GSZEHLW5RP7K2TTYERYGJPOTVB6OVLVYPUNBHRAAOJQLKTHSLELZ2WS3NA"

MOCK_USER_CONFIGS = {
    "4B": {
        "unit": "4B",
        "user_id": "U123",
        "opted_alexa": True,
        "alexa_user_id": REAL_ALEXA_USER_ID,  
        "name": "John Doe",
        "email": "john.doe@example.com",
        "phone": "+1234567890"
    }
}

# Helper function to get the OAuth Token safely with the exact Proactive Events scope
def get_alexa_proactive_token():
    logger.info("🔑 Requesting access token from Amazon OAuth server...")
    url = "https://api.amazon.com/auth/o2/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": ALEXA_CLIENT_ID,
        "client_secret": ALEXA_CLIENT_SECRET,
        "scope": "alexa::proactive_events"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(url, data=payload, headers=headers)
    
    if response.status_code != 200:
        logger.error(f"❌ Token exchange failed: {response.status_code} - {response.text}")
        raise Exception("Failed to get authenticated bearer token from LWA.")
        
    token = response.json().get("access_token")
    logger.info("✅ Successfully generated access token!")
    return token

# Helper function to send the completely clean schema directly to the Sandbox/Development endpoint
# Helper function to send the completely clean schema directly to the Sandbox/Development endpoint
# Helper function to send the completely clean schema directly to the Sandbox/Development endpoint
def send_proactive_notification(carrier_name, package_id):
    token = get_alexa_proactive_token()
    
    # FORCE DEVELOPMENT ENDPOINT ONLY
    url = "https://api.amazonalexa.com/v1/proactiveEvents/stages/development"
    
    # Clean ISO timestamps without milliseconds fractions
    now_clean = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    expiry_clean = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Strict Amazon Custom Event Schema Definition Contract
    payload = {
        "timestamp": now_clean,
        "referenceId": f"notifii{int(datetime.now().timestamp())}",  # Purely alphanumeric tracking string
        "expiryTime": expiry_clean,
        "event": {
            "name": "AMAZON.OrderStatus.Updated",
            "payload": {
                "state": {
                    "status": "ORDER_DELIVERED"
                },
                "order": {
                    "seller": {
                        "name": "localizedattribute:sellerName"
                    }
                }
            }
        },
        "localizedAttributes": [
            {
                "locale": "en-US",
                "sellerName": carrier_name
            }
        ],
        "relevantAudience": {
            "type": "Unicast",
            "payload": {
                "user": REAL_ALEXA_USER_ID
            }
        }
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    
    logger.info(f"🚀 Firing Proactive Event directly to sandbox:\n{json.dumps(payload, indent=2)}")
    res = requests.post(url, json=payload, headers=headers)
    logger.info(f"📬 Alexa Server Response: {res.status_code} - {res.text}")
    return res.status_code, res.text
# ============================================
# MAIN WEBHOOK ENDPOINT
# ============================================

@app.route('/webhook/package-delivered', methods=['POST'])
def webhook_package_delivered():
    """Main webhook endpoint for package events from Notifii"""
    try:
        payload = request.get_json()
        logger.info(f"Received webhook payload: {payload}")
        
        if not payload or 'data' not in payload:
            return jsonify({"status": "error", "message": "Missing data block"}), 400
            
        data = payload['data']
        carrier = data.get('carrier', 'FedEx')
        package_id = data.get('package_id', 'PKG-12345')
        
        # Fire our working notification engine directly
        status_code, response_text = send_proactive_notification(carrier, package_id)
        
        if status_code in [200, 202]:
            return jsonify({
                "status": "success",
                "package_id": package_id,
                "message": "Notification successfully queued on user device!"
            }), 200
        else:
            return jsonify({
                "status": "error",
                "package_id": package_id,
                "error": response_text
            }), 400
            
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/webhook/test', methods=['GET'])
def test_webhook():
    return jsonify({"status": "running"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)