import logging
import json
import os
import uuid
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
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
    # ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '')
    # ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '')
    ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '').strip()
    ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '').strip()
    ALEXA_API_URL = os.environ.get('ALEXA_API_URL', '').strip()
    # Global state tracker for development/testing
    LATEST_ALEXA_USER_ID = os.environ.get('ALEXA_USER_ID', '').strip()
    # ALEXA_API_URL = os.environ.get('ALEXA_API_URL', 'https://api.eu.amazonalexa.com/v1/proactiveEvents/stages/development')

config = Config()

# ============================================
# ALEXA PROACTIVE EVENTS CLIENT
# ============================================
# POC had no database to store user → unit mappings
# Only one test user "4B" was used for all testing

LATEST_PACKAGES = {}
CURRENT_UNIT = "4B",
USER_ID_TO_UNIT = {
   LATEST_ALEXA_USER_ID: "4B"
}

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
            response = requests.post(token_url, data=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                self._cached_token = data["access_token"]
                expires_in = data.get("expires_in", 3600)
                self._token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)
                logger.info(f"Successfully obtained LWA token (expires in {expires_in}s)")
                return self._cached_token

            logger.error(f"Token failed: {response.status_code} - {response.text}")
            return None
        except Exception as e:
            logger.error(f"Token fetch error: {str(e)}")
            return None

    def send_notification(self, alexa_user_id: str, carrier: str, package_id: str) -> Dict:
        token = self.get_token()
        if not token:
            return {"status": "error", "message": "Failed to obtain LWA access token"}

        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        expiry = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Correct schema payload for AMAZON.OrderStatus.Updated
        payload = {
            "timestamp": now,
            "referenceId": f"notifii.{package_id}.{int(datetime.utcnow().timestamp())}",
            "expiryTime": expiry,
            "event": {
                "name": "AMAZON.OrderStatus.Updated",
                "payload": {
                    "state": {
                        "status": "ORDER_DELIVERED",
                        "deliveredOn": now
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
                    "sellerName": carrier
                }
            ],
            "relevantAudience": {
                "type": "Unicast",
                "payload": {
                    "user": alexa_user_id
                }
            }
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        logger.info(f"OUTGOING PAYLOAD: {json.dumps(payload)}")
        try:
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 202:
                logger.info(f"Notification successfully sent to {alexa_user_id}")
                logger.info(f"Amazon Request ID: {response.headers.get('x-amzn-requestid')}")
                return {"status": "success", "code": 202}

            else:
                logger.error(f"Proactive Events API error: {response.status_code} - {response.text}")
                return {"status": "error", "code": response.status_code, "message": response.text}
        except Exception as e:
            logger.error(f"Send notification error: {str(e)}")
            return {"status": "error", "message": str(e)}

alexa_client = AlexaProactiveEventsClient()


# ============================================
# USER DATA STORE
# ============================================


def get_user_configuration(unit: str) -> Optional[Dict]:
    """Fetch user configuration. Uses LATEST_ALEXA_USER_ID updated dynamically at launch."""
    # active_user_id = LATEST_ALEXA_USER_ID or os.environ.get('ALEXA_USER_ID', '')
    active_user_id = os.environ.get('ALEXA_USER_ID', '').strip()
    
    configs = {
        "4B": {"unit": "4B", "opted_alexa": True, "alexa_user_id": active_user_id},
        "2A": {"unit": "2A", "opted_alexa": True, "alexa_user_id": active_user_id}
    }
    return configs.get(unit)


# ============================================
# WEBHOOK HANDLER
# ============================================

def handle_package_event(event: Dict, context: Any) -> Dict:
    logger.info(f"📦 Webhook event received: {event}")
    data = event.get('data', {})
    unit = data.get('unit')
    package_id = data.get('package_id')
    carrier = data.get('carrier', 'courier')

    if not unit or not package_id:
        return {"status": "error", "message": "Missing unit or package_id"}

    user_config = get_user_configuration(unit)
    if not user_config:
        return {"status": "error", "message": f"User unit {unit} not found"}

    if not user_config.get('opted_alexa', False):
        return {"status": "skipped", "reason": "User not opted in"}

    alexa_user_id = user_config.get('alexa_user_id')
    LATEST_PACKAGES.setdefault(unit, []).append({
        "package_id": package_id,
        "carrier": carrier,
        "tracking_number": data.get("tracking_number"),
        "compartment": data.get("compartment"),
        "package_size": data.get("package_size"),
        "signature_required": data.get("signature_required"),
        "delivered_at": data.get("delivered_at")
    })

    debug_info = {
        "active_alexa_user_id_present": bool(alexa_user_id),
        "active_alexa_user_id_length": len(alexa_user_id) if alexa_user_id else 0,
        "active_alexa_user_id_preview": alexa_user_id[:15] + "..." if alexa_user_id else None,
    }

    if not alexa_user_id:
        return {"status": "skipped", "reason": "No Alexa User ID linked", "debug": debug_info}
    
    logger.info(f"RAW USERID REPR: {repr(alexa_user_id)}")

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
# ALEXA SKILL INTENT & EVENT HANDLERS
# ============================================

class ProactiveSubscriptionChangedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AlexaSkillEvent.ProactiveSubscriptionChanged")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        logger.info(f"✅ SUBSCRIPTION EVENT RECEIVED for user: {user_id}")
        return handler_input.response_builder.response


# class LaunchRequestHandler(AbstractRequestHandler):
#     def can_handle(self, handler_input):
#         return ask_utils.is_request_type("LaunchRequest")(handler_input)

#     def handle(self, handler_input):
#         user_id = handler_input.request_envelope.context.system.user.user_id
#         logger.info(f"🚀 LaunchRequest triggered by User ID: {user_id}")
        
#         # Dynamically store active user ID
#         global LATEST_ALEXA_USER_ID
#         LATEST_ALEXA_USER_ID = user_id

#         speak_output = "Welcome to Notifii Alert. Your account is connected for package updates."
#         return (
#             handler_input.response_builder
#             .speak(speak_output)
#             .ask("How can I help you?")
#             .response
#         )
class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        global LATEST_ALEXA_USER_ID, CURRENT_UNIT
        LATEST_ALEXA_USER_ID = handler_input.request_envelope.context.system.user.user_id
        
        # Map user ID to unit
        CURRENT_UNIT = USER_ID_TO_UNIT.get(LATEST_ALEXA_USER_ID, "4B")
        logger.info(f"🚀 User {LATEST_ALEXA_USER_ID[:20]}... mapped to unit {CURRENT_UNIT}")
        
        speak_output = "Welcome to Notifii Alert. Your account is connected for package updates."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response
# class PackageStatusIntentHandler(AbstractRequestHandler):
#     def can_handle(self, handler_input):
#         return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

#     def handle(self, handler_input):
#         user_id = handler_input.request_envelope.context.system.user.user_id
#         logger.info(f"PackageStatusIntent triggered by user: {user_id}")
#         speak_output = "You have 2 packages waiting. One from FedEx arrived today, and one from UPS arrived yesterday."
#         return handler_input.response_builder.speak(speak_output).response

class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)
    
    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages right now."
        else:
            speak_output = generate_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).response

# class LockerAccessIntentHandler(AbstractRequestHandler):
#     def can_handle(self, handler_input):
#         return ask_utils.is_intent_name("LockerAccessIntent")(handler_input)

#     def handle(self, handler_input):
#         speak_output = "Your package is in locker B4. Please use access code 12345."
#         return handler_input.response_builder.speak(speak_output).response

class LockerAccessIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input): 
        return ask_utils.is_intent_name("LockerAccessIntent")(handler_input)
    
    def handle(self, handler_input):
        unit = CURRENT_UNIT if 'CURRENT_UNIT' in globals() else "4B"
        packages = LATEST_PACKAGES.get(unit, [])
        
        if not packages:
            speak_output = "You have no packages in a locker right now."
        else:
            latest = packages[-1]
            speak_output = f"Your package is in compartment {latest.get('compartment', 'unknown')}."
        
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
        return (
            ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or
            ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input)
        )

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
# EXCEPTION HANDLER
# ============================================

class CatchAllExceptionHandler(AbstractExceptionHandler):
    """Catch-all exception handler to prevent HTTP 500 responses."""
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(f"Error handling request: {exception}", exc_info=True)
        speak_output = "Sorry, I had trouble processing your request. Please try again."
        return (
            handler_input.response_builder
            .speak(speak_output)
            .response
        )

class SkillPermissionChangedHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return ask_utils.is_request_type(
            "AlexaSkillEvent.SkillPermissionChanged"
        )(handler_input)

    def handle(self, handler_input):
        logger.info("Permission Changed")
        logger.info(json.dumps(
            handler_input.request_envelope.to_dict(),
            indent=2
        ))
        return handler_input.response_builder.response

class SkillDisabledHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return ask_utils.is_request_type(
            "AlexaSkillEvent.SkillDisabled"
        )(handler_input)

    def handle(self, handler_input):
        logger.info("Skill Disabled")
        logger.info(json.dumps(
            handler_input.request_envelope.to_dict(),
            indent=2
        ))
        return handler_input.response_builder.response

class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        logger.info("Session ended")
        return handler_input.response_builder.response
def get_time_of_day():
    hour = datetime.now().hour
    if 5 <= hour < 12: return "morning"
    elif 12 <= hour < 17: return "afternoon"
    else: return "evening"

def get_urgency(days):
    if days >= 7: return "URGENT"
    elif days >= 5: return "Important"
    elif days >= 3: return "Reminder"
    else: return "New"

def calculate_waiting_days(delivered_at):
    delivered = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
    now = datetime.now(delivered.tzinfo)
    return (now - delivered).days

def generate_package_summary(packages):
    if not packages:
        return "You have no packages right now."
    
    total = len(packages)
    carrier_counts = {}
    total_waiting = 0
    
    for p in packages:
        carrier = p.get('carrier', 'unknown')
        carrier_counts[carrier] = carrier_counts.get(carrier, 0) + 1
        
        # Calculate waiting days
        delivered_at = p.get('delivered_at')
        if delivered_at:
            days = calculate_waiting_days(delivered_at)
            total_waiting = max(total_waiting, days)
    
    parts = []
    for carrier, count in carrier_counts.items():
        parts.append(f"{count} from {carrier}")
    
    # Add urgency if waiting
    urgency = get_urgency(total_waiting)
    time = get_time_of_day()
    
    if total_waiting >= 7:
        return f"URGENT: You have {total} packages that have been waiting for {total_waiting} days: {', '.join(parts)}."
    elif total_waiting >= 3:
        return f"Reminder: You have {total} packages waiting for {total_waiting} days: {', '.join(parts)}."
    else:
        return f"You have {total} packages: {', '.join(parts)}."
    
# ============================================
# SKILL BUILDER REGISTRATION
# ============================================

sb = SkillBuilder()
sb.add_request_handler(SkillPermissionChangedHandler())
sb.add_request_handler(SkillDisabledHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(ProactiveSubscriptionChangedHandler())
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(LockerAccessIntentHandler())
sb.add_request_handler(MailroomHoursIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())

# Catch-all exception handler (prevents 500 internal server errors)
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()