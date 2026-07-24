import logging
from dotenv import load_dotenv
load_dotenv()
import os

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

# print("ALEXA_USER_ID =", os.getenv("ALEXA_USER_ID"))
app.logger.info(f"ALEXA_USER_ID at startup = {os.getenv('ALEXA_USER_ID')}")

# Alexa skill endpoint (handles LaunchRequest, intents, skill events)
skill_adapter = SkillAdapter(skill=sb.create(), skill_id=None, app=app)
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


@app.route("/health")
def health():
    return jsonify({"status": "running"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)