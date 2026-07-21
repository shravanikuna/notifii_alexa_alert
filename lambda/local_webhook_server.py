#!/usr/bin/env python3
"""
Local webhook server for testing Notifii package events.
"""

import json
import logging
import os
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================
# CONFIGURATION
# ============================================

ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '')
ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '')

ALEXA_USER_ID = os.environ.get('ALEXA_USER_ID', '')

def get_alexa_token():
    """Get OAuth token for Proactive Events"""
    url = "https://api.amazon.com/auth/o2/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": ALEXA_CLIENT_ID,
        "client_secret": ALEXA_CLIENT_SECRET,
        "scope": "alexa::proactive_events"
    }
    response = requests.post(url, data=payload)
    if response.status_code != 200:
        raise Exception(f"Token failed: {response.text}")
    return response.json().get("access_token")

def send_notification(carrier_name):
    """Send proactive notification to Alexa"""
    token = get_alexa_token()
    
    # Use FE (Far East) endpoint for India/Asia
    url = "https://api.fe.amazonalexa.com/v1/proactiveEvents/stages/development"
    
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    expiry = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    payload = {
        "timestamp": now,
        "referenceId": f"notifii_{int(datetime.utcnow().timestamp())}",
        "expiryTime": expiry,
        "event": {
            "name": "AMAZON.OrderStatus.Updated",
            "payload": {
                "state": {"status": "ORDER_DELIVERED"},
                "order": {"seller": {"name": "localizedattribute:sellerName"}}
            }
        },
        "localizedAttributes": [{"locale": "en-US", "sellerName": carrier_name}],
        "relevantAudience": {
            "type": "Unicast",
            "payload": {"user": ALEXA_USER_ID}
        }
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(url, json=payload, headers=headers)
    return response.status_code, response.text

@app.route('/webhook/package-delivered', methods=['POST'])
def webhook_package_delivered():
    try:
        payload = request.get_json()
        logger.info(f"Received: {payload}")
        
        if not payload or 'data' not in payload:
            return jsonify({"status": "error", "message": "Missing data"}), 400
            
        data = payload['data']
        carrier = data.get('carrier', 'FedEx')
        package_id = data.get('package_id', 'PKG-12345')
        
        status_code, response_text = send_notification(carrier)
        
        if status_code == 202:
            return jsonify({
                "status": "success",
                "package_id": package_id,
                "message": "Notification queued successfully!"
            }), 200
        else:
            return jsonify({
                "status": "error",
                "package_id": package_id,
                "error": response_text
            }), 400
            
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/webhook/test', methods=['GET'])
def test_webhook():
    return jsonify({"status": "running"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)