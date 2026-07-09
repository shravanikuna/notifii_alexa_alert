import logging
import json
import requests
import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================
# CONFIGURATION
# ============================================

# Get these from Build > Permissions > Alexa Skill Messaging
CLIENT_ID = "YOUR_CLIENT_ID_HERE"
CLIENT_SECRET = "YOUR_CLIENT_SECRET_HERE"

# ============================================
# PROACTIVE EVENTS FUNCTION
# ============================================

def get_proactive_events_token():
    """Get access token for Proactive Events API"""
    token_url = "https://api.amazon.com/auth/o2/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "alexa::proactive_events"
    }
    
    try:
        response = requests.post(token_url, data=payload)
        if response.status_code == 200:
            return response.json().get("access_token")
        else:
            logger.error(f"Failed to get token: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Token error: {str(e)}")
        return None

def send_proactive_notification(alexa_user_id, seller_name, status="ORDER_DELIVERED"):
    """Send a proactive notification to a user"""
    access_token = get_proactive_events_token()
    if not access_token:
        return {"error": "Failed to get access token"}
    
    payload = {
        "timestamp": "2026-07-09T10:00:00.00Z",
        "referenceId": f"notifii_pkg_{alexa_user_id}_{__import__('time').time()}",
        "expiryTime": "2026-07-10T10:00:00.00Z",
        "event": {
            "name": "AMAZON.OrderStatus.Updated",
            "payload": {
                "state": {
                    "status": status
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
                "sellerName": seller_name
            }
        ],
        "relevantAudience": {
            "type": "Unicast",
            "payload": {
                "user": alexa_user_id
            }
        }
    }
    
    url = "https://api.amazonalexa.com/v1/proactiveEvents/stages/development"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 202:
            logger.info("Notification sent successfully")
            return {"status": "success", "code": 202}
        else:
            logger.error(f"Notification failed: {response.status_code}")
            return {"status": "failed", "code": response.status_code}
    except Exception as e:
        logger.error(f"Notification error: {str(e)}")
        return {"error": str(e)}

# ============================================
# INTENT HANDLERS
# ============================================

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        logger.info(f"User {user_id} launched the skill")
        
        speak_output = "Welcome to Notiffi Alert. You can ask about your packages, locker access, or mailroom hours."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response

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
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(LockerAccessIntentHandler())
sb.add_request_handler(MailroomHoursIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())

lambda_handler = sb.lambda_handler()