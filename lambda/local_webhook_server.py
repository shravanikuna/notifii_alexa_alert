#!/usr/bin/env python3
"""
Local webhook server for testing Notifii package events with AI Agent.
Run this script locally to receive and process webhooks from Postman.
"""

import json
import logging
import os
from datetime import datetime
from flask import Flask, request, jsonify
# Import the webhook handler from your lambda_function
from lambda_function import handle_package_event, config

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================
# LOAD ENVIRONMENT VARIABLES
# ============================================

# Read Alexa credentials from environment variables
ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '')
ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '')

# Update the lambda_function config with environment variables
if ALEXA_CLIENT_ID and ALEXA_CLIENT_SECRET:
    config.ALEXA_CLIENT_ID = ALEXA_CLIENT_ID
    config.ALEXA_CLIENT_SECRET = ALEXA_CLIENT_SECRET
    logger.info("✅ Alexa credentials loaded from environment variables")
else:
    logger.warning("⚠️ Alexa credentials not found in environment variables")

# ============================================
# CONFIGURATION - Update these for testing
# ============================================

# ⚠️ REPLACE WITH YOUR REAL ALEXA USER ID FROM TEST TAB
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
    },
    "5C": {
        "unit": "5C",
        "user_id": "U456",
        "opted_alexa": False,
        "alexa_user_id": None,
        "name": "Jane Smith",
        "email": "jane.smith@example.com",
        "phone": "+0987654321"
    },
    "2A": {
        "unit": "2A",
        "user_id": "U789",
        "opted_alexa": True,
        "alexa_user_id": REAL_ALEXA_USER_ID,  
        "name": "Bob Wilson",
        "email": "bob.wilson@example.com",
        "phone": "+1122334455"
    }
}

MOCK_PACKAGES = {
    "PKG-12345": {
        "package_id": "PKG-12345",
        "unit": "4B",
        "carrier": "FedEx",
        "tracking_number": "123456789",
        "compartment": "B4",
        "delivered_at": datetime.now().isoformat(),
        "package_size": "medium",
        "status": "delivered"
    },
    "PKG-67890": {
        "package_id": "PKG-67890",
        "unit": "2A",
        "carrier": "UPS",
        "tracking_number": "987654321",
        "compartment": "A2",
        "delivered_at": "2026-07-08T10:00:00Z",
        "package_size": "large",
        "status": "delivered"
    }
}

# ============================================
# MOCK NOTIFII API
# ============================================

@app.route('/mock/residents/<unit>', methods=['GET'])
def mock_get_user_config(unit):
    """Mock the Notifii API endpoint for user configuration"""
    config = MOCK_USER_CONFIGS.get(unit)
    if config:
        return jsonify(config), 200
    return jsonify({"error": "User not found"}), 404

@app.route('/mock/packages/<package_id>', methods=['GET'])
def mock_get_package(package_id):
    """Mock the Notifii API endpoint for package details"""
    package = MOCK_PACKAGES.get(package_id)
    if package:
        return jsonify(package), 200
    return jsonify({"error": "Package not found"}), 404

@app.route('/mock/packages', methods=['GET'])
def mock_get_uncollected_packages():
    """Mock endpoint for uncollected packages (for reminders)"""
    uncollected = [p for p in MOCK_PACKAGES.values() if p.get('status') == 'delivered']
    return jsonify(uncollected), 200

# ============================================
# MAIN WEBHOOK ENDPOINT
# ============================================

@app.route('/webhook/package-delivered', methods=['POST'])
def webhook_package_delivered():
    """Main webhook endpoint for package events from Notifii"""
    try:
        payload = request.get_json()
        logger.info(f"Received webhook: {payload}")
        
        if not payload or 'data' not in payload:
            return jsonify({
                "status": "error",
                "message": "Missing 'data' field in payload"
            }), 400
        
        result = handle_package_event(payload, None)
        logger.info(f"Webhook processed: {result}")
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/webhook/test', methods=['GET'])
def test_webhook():
    """Test endpoint to verify the server is running"""
    return jsonify({
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "message": "Webhook server is ready for testing with AI Agent"
    }), 200

@app.route('/webhook/status', methods=['GET'])
def webhook_status():
    """Check if credentials are configured"""
    has_credentials = bool(config.ALEXA_CLIENT_ID and config.ALEXA_CLIENT_SECRET)
    return jsonify({
        "status": "ready" if has_credentials else "missing_credentials",
        "message": "Alexa credentials configured" if has_credentials else "Alexa credentials not configured",
        "client_id_set": bool(config.ALEXA_CLIENT_ID),
        "client_secret_set": bool(config.ALEXA_CLIENT_SECRET)
    }), 200

# ============================================
# FIX: Make lambda_function use our mock endpoints
# ============================================

import lambda_function

class MockNotifiiAPIClient:
    def get_user_configuration(self, unit):
        return MOCK_USER_CONFIGS.get(unit)
    
    def get_package_details(self, package_id):
        return MOCK_PACKAGES.get(package_id)
    
    def mark_package_picked_up(self, package_id):
        return True
    
    def refresh_user_token(self, user_id):
        return {"access_token": "mock_token", "refresh_token": "mock_refresh"}

# Override the notifii_client in lambda_function
lambda_function.notifii_client = MockNotifiiAPIClient()

# ============================================
# START THE SERVER
# ============================================

if __name__ == '__main__':
    print("=" * 70)
    print("🤖 NOTIFII WEBHOOK SERVER WITH AI AGENT")
    print("=" * 70)
    print("\n🚀 Server running at: http://localhost:5000")
    print("\n📋 Endpoints:")
    print("  - GET  /webhook/test                 → Check server")
    print("  - GET  /webhook/status               → Check credentials")
    print("  - POST /webhook/package-delivered    → Send webhook")
    print("\n📦 Mock Data:")
    print(f"  - Residents: {list(MOCK_USER_CONFIGS.keys())}")
    print(f"  - Packages: {list(MOCK_PACKAGES.keys())}")
    print(f"\n🔧 Alexa User ID: {REAL_ALEXA_USER_ID[:30]}..." if REAL_ALEXA_USER_ID != "amzn1.ask.account.REAL_USER_ID_HERE" else "\n⚠️  REAL_USER_ID not set - update MOCK_USER_CONFIGS")
    print("\n🤖 AI Agent Features:")
    print("  - Contextual message generation")
    print("  - Time-of-day awareness")
    print("  - Progressive reminders (Day 0, 1-2, 3, 5, 7+)")
    print("  - Urgency detection")
    print("=" * 70)
    
    app.run(host='0.0.0.0', port=5000, debug=True)