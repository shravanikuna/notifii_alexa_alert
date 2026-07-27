import logging
import json
import os
import uuid
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

# ============================================
# LOGGING
# ============================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================
# CONFIGURATION - Environment Variables
# ============================================

class Config:
    ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '')
    ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '')
    ALEXA_API_URL = os.environ.get('ALEXA_API_URL', 'https://api.amazonalexa.com/v1/proactiveEvents/stages/development')

config = Config()

# ============================================
# ALEXA PROACTIVE EVENTS CLIENT
# ============================================

class AlexaProactiveEventsClient:
    def __init__(self):
        self.client_id = config.ALEXA_CLIENT_ID
        self.client_secret = config.ALEXA_CLIENT_SECRET
        self.api_url = config.ALEXA_API_URL
        self._cached_token = None
        self._token_expires_at = datetime.utcnow()

    def get_token(self) -> Optional[str]:
        # Return cached token if valid for at least 5 more minutes
        if self._cached_token and datetime.utcnow() < self._token_expires_at:
            return self._cached_token

        token_url = "https://api.amazon.com/auth/o2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "alexa::proactive_events"
        }
        try:
            response = requests.post(token_url, data=payload, timeout=5)
            if response.status_code == 200:
                data = response.json()
                self._cached_token = data["access_token"]
                # Cache for 55 minutes
                self._token_expires_at = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 3600) - 300)
                return self._cached_token
            return None
        except Exception as e:
            logger.error(f"Token fetch error: {str(e)}")
            return None

alexa_client = AlexaProactiveEventsClient()


# ============================================
# MOCK USER DATA (Replace with your real user ID)
# ============================================

ALEXA_USER_ID = os.environ.get('ALEXA_USER_ID', '')

MOCK_USER_CONFIGS = {
    "4B": {"unit": "4B", "opted_alexa": True, "alexa_user_id": ALEXA_USER_ID},
    "2A": {"unit": "2A", "opted_alexa": True, "alexa_user_id": ALEXA_USER_ID}
}

def get_user_configuration(unit: str) -> Optional[Dict]:
    """Mock user config - In production, fetch from Notifii API"""
    return MOCK_USER_CONFIGS.get(unit)


# ============================================
# WEBHOOK HANDLER
# ============================================

def handle_package_event(event: Dict, context: Any) -> Dict:
    logger.info(f"📦 Received: {event}")

    data = event.get('data', {})
    unit = data.get('unit')
    package_id = data.get('package_id')
    carrier = data.get('carrier', 'courier')

    if not unit or not package_id:
        return {"status": "error", "message": "Missing unit or package_id"}

    user_config = get_user_configuration(unit)
    if not user_config:
        return {"status": "error", "message": f"User {unit} not found"}

    if not user_config.get('opted_alexa', False):
        return {"status": "skipped", "reason": "User not opted in"}

    alexa_user_id = user_config.get('alexa_user_id')

    # DEBUG: show exactly what value the app actually loaded
    debug_info = {
        "env_ALEXA_USER_ID_present": bool(ALEXA_USER_ID),
        "env_ALEXA_USER_ID_length": len(ALEXA_USER_ID) if ALEXA_USER_ID else 0,
        "env_ALEXA_USER_ID_preview": ALEXA_USER_ID[:15] + "..." if ALEXA_USER_ID else None,
    }

    if not alexa_user_id:
        return {"status": "skipped", "reason": "No Alexa ID linked", "debug": debug_info}

    result = alexa_client.send_notification(alexa_user_id, carrier, package_id)

    if result.get('status') == 'success':
        return {
            "status": "success",
            "package_id": package_id,
            "unit": unit,
            "message": f"Notification sent for {carrier} package",
            "debug": debug_info
        }
    else:
        return {
            "status": "error",
            "package_id": package_id,
            "error": result.get('message'),
            "debug": debug_info
        }
# ============================================
# ALEXA SKILL INTENT HANDLERS
# ============================================
class ProactiveSubscriptionChangedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AlexaSkillEvent.ProactiveSubscriptionChanged")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        logger.info(f"✅ SUBSCRIPTION EVENT RECEIVED for user: {user_id}")
        print(f"\n✅ SUBSCRIPTION CHANGED: {user_id}\n")
        return handler_input.response_builder.response
class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        
        # Log or dynamically store in DB (e.g. update user unit mapping)
        logger.info(f"Updated Alexa User ID: {user_id}")
        
        # Example: Update global/DB mapping automatically
        global ALEXA_USER_ID
        ALEXA_USER_ID = user_id
        
        speak_output = "Welcome to Notifii Alert. Your account is connected for package updates."
        return handler_input.responseBuilder.speak(speak_output).getResponse()

class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        logger.info(f"PackageStatusIntent triggered by user: {user_id}")
        speak_output = "You have 2 packages waiting. One from FedEx arrived today, and one from UPS arrived yesterday."
        return handler_input.response_builder.speak(speak_output).response


class LockerAccessIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("LockerAccessIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "Your package is in locker B4. Please use access code 12345."
        return handler_input.response_builder.speak(speak_output).response


class MailroomHoursIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("MailroomHoursIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "The mailroom is open from 8 AM to 8 PM, Monday through Friday."
        return handler_input.response_builder.speak(speak_output).response


class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "You can ask me about your packages, locker access, or mailroom hours."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class CancelAndStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or \
               ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "Goodbye!"
        return handler_input.response_builder.speak(speak_output).response


class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "I'm sorry, I didn't understand. You can ask about your packages, locker access, or mailroom hours."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


# ============================================
# SKILL BUILDER
# ============================================

sb = SkillBuilder()
sb.add_request_handler(ProactiveSubscriptionChangedHandler())
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(LockerAccessIntentHandler())
sb.add_request_handler(MailroomHoursIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())

lambda_handler = sb.lambda_handler()