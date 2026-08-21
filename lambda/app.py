import logging
from dotenv import load_dotenv
import db
load_dotenv()
import os
import hmac
import hashlib
from flask import Flask, request, jsonify
from flask_ask_sdk.skill_adapter import SkillAdapter
from lambda_function import sb, alexa_client, handle_package_event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = Flask(__name__)

if __name__ != "__main__":
    gunicorn_logger = logging.getLogger('gunicorn.error')
    logger.handlers = gunicorn_logger.handlers
    logger.setLevel(gunicorn_logger.level)
else:
    logging.basicConfig(level=logging.INFO)

# Secret shared with Notifii (store in env)
WEBHOOK_SECRET = os.environ.get('NOTIFII_WEBHOOK_SECRET', '')

# print("ALEXA_USER_ID =", os.getenv("ALEXA_USER_ID"))
app.logger.info(f"ALEXA_USER_ID at startup = {os.getenv('ALEXA_USER_ID')}")

# Alexa skill endpoint (handles LaunchRequest, intents, skill events)
# skill_adapter = SkillAdapter(skill=sb.create(), skill_id=None, app=app)
skill_adapter = SkillAdapter(skill=sb.create(),skill_id=os.getenv("SKILL_ID"),app=app)
skill_adapter.register(app=app, route="/alexa")

# Notifii package webhook
@app.route('/webhook/package-delivered', methods=['POST'])
def webhook_package_delivered():
    try:
        payload = request.get_json()
        logger.info(f"Received webhook: {payload}")

        if not payload or 'data' not in payload:
            return jsonify({"status": "error", "message": "Missing data"}), 400

        result = handle_package_event(payload, None)
        status_code = 200 if result.get('status') == 'success' else 400
        return jsonify(result), status_code

    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "service": "Notifii Alexa Alert",
        "health": "/health",
        "alexa": "/alexa",
        "webhook": "/webhook/package-delivered"
    }), 200


@app.route('/api/link-account', methods=['POST'])
def link_account():
    """
    Protected API endpoint for Notifii to link alexa_user_id to account_id.
    Requires a secret token in the header.
    """
    try:
        # Verify authorization
        auth_header = request.headers.get('Authorization', '')
        expected_auth = f"Bearer {WEBHOOK_SECRET}"
        
        if auth_header != expected_auth:
            logger.warning(f"Unauthorized attempt to link account from {request.remote_addr}")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
        data = request.get_json()
        account_id = data.get('account_id')
        alexa_user_id = data.get('alexa_user_id')
        region = data.get('region', 'NA')
        
        if not account_id or not alexa_user_id:
            return jsonify({"status": "error", "message": "Missing account_id or alexa_user_id"}), 400
        
        # Store the mapping in the database
        success = db.link_account_id_to_alexa(account_id, alexa_user_id, region)
        
        if success:
            logger.info(f"✅ Successfully linked account {account_id} to Alexa user {alexa_user_id[:20]}...")
            return jsonify({"status": "success", "message": "Account linked successfully"}), 200
        else:
            return jsonify({"status": "error", "message": "Failed to link account"}), 500
            
    except Exception as e:
        logger.error(f"Link account error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route("/health")
def health():
    return jsonify({"status": "running"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)