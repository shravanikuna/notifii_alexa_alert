import logging
import json
import os
import uuid
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List

import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class Config:
    ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '').strip()
    ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '').strip()
    ALEXA_API_URL = os.environ.get('ALEXA_API_URL', '').strip()
    LATEST_ALEXA_USER_ID = os.environ.get('ALEXA_USER_ID', '').strip()

config = Config()

LATEST_PACKAGES = {}
CURRENT_UNIT = "4B"
USER_ID_TO_UNIT = {config.LATEST_ALEXA_USER_ID: "4B"}

class AlexaProactiveEventsClient:
    def __init__(self):
        self.client_id = config.ALEXA_CLIENT_ID
        self.client_secret = config.ALEXA_CLIENT_SECRET
        self.api_url = config.ALEXA_API_URL
        self._cached_token = None
        self._token_expires_at = datetime.utcnow()

    def get_token(self) -> Optional[str]:
        if self._cached_token and datetime.utcnow() < self._token_expires_at:
            return self._cached_token
        token_url = "https://api.amazon.com/auth/o2/token"
        payload = {"grant_type": "client_credentials", "client_id": self.client_id,
                   "client_secret": self.client_secret, "scope": "alexa::proactive_events"}
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

    def send_notification(self, alexa_user_id, carrier, package_id, tracking_number=None,
                          compartment=None, delivered_at=None, unit=None) -> Dict:
        token = self.get_token()
        if not token:
            return {"status": "error", "message": "Failed to obtain LWA access token"}
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        expiry = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "timestamp": now,
            "referenceId": f"notifii.{package_id}.{int(datetime.utcnow().timestamp())}",
            "expiryTime": expiry,
            "event": {"name": "AMAZON.OrderStatus.Updated", "payload": {
                "state": {"status": "ORDER_DELIVERED", "deliveredOn": delivered_at or now},
                "order": {"seller": {"name": "localizedattribute:sellerName"}, "orderId": package_id,
                          "trackingNumber": tracking_number,
                          "delivery": {"compartment": compartment or "unknown", "unit": unit or "unknown"}}
            }},
            "localizedAttributes": [{"locale": "en-US", "sellerName": carrier}],
            "relevantAudience": {"type": "Unicast", "payload": {"user": alexa_user_id}}
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        logger.info(f"OUTGOING PAYLOAD: {json.dumps(payload)}")
        try:
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 202:
                logger.info(f"Notification successfully sent to {alexa_user_id}")
                return {"status": "success", "code": 202}
            logger.error(f"Proactive Events API error: {response.status_code} - {response.text}")
            return {"status": "error", "code": response.status_code, "message": response.text}
        except Exception as e:
            logger.error(f"Send notification error: {str(e)}")
            return {"status": "error", "message": str(e)}

alexa_client = AlexaProactiveEventsClient()

def get_user_configuration(unit: str) -> Optional[Dict]:
    active_user_id = os.environ.get('ALEXA_USER_ID', '').strip()
    configs = {
        "4B": {"unit": "4B", "opted_alexa": True, "alexa_user_id": active_user_id},
        "2A": {"unit": "2A", "opted_alexa": True, "alexa_user_id": active_user_id}
    }
    return configs.get(unit)

# ============================================
# HELPERS
# ============================================

def get_slot_value(handler_input, slot_name: str) -> Optional[str]:
    """CORRECT way to read a Slot object — attribute access, not dict .get()"""
    try:
        slots = handler_input.request_envelope.request.intent.slots
        if not slots or slot_name not in slots:
            return None
        value = slots[slot_name].value
        return str(value).strip() if value else None
    except Exception as e:
        logger.error(f"get_slot_value error for '{slot_name}': {e}")
        return None

def format_package_details(package: Dict) -> str:
    carrier = package.get('carrier', 'unknown carrier')
    tracking = package.get('tracking_number', 'no tracking number')
    compartment = package.get('compartment', 'unknown compartment')
    delivered_at = package.get('delivered_at')
    if delivered_at:
        try:
            dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
            delivered_str = dt.strftime('%B %d, %Y at %I:%M %p')
        except Exception:
            delivered_str = delivered_at
    else:
        delivered_str = 'recently'
    return f"Package from {carrier}, tracking number {tracking}, stored in compartment {compartment}, delivered on {delivered_str}"

def format_all_package_details(packages: List[Dict]) -> str:
    return " Also, ".join(format_package_details(p) for p in packages)

def get_package_summary(packages: List[Dict]) -> str:
    if not packages:
        return "You have no packages right now."
    descriptions = [f"from {p.get('carrier', 'unknown')} with tracking number {p.get('tracking_number', 'unknown')}" for p in packages]
    if len(descriptions) == 1:
        return f"You have a package {descriptions[0]}. If you want to know more about this package, just ask me."
    elif len(descriptions) == 2:
        return f"You have {descriptions[0]} and {descriptions[1]}. If you want to know more about any package, just ask me."
    return f"You have {', '.join(descriptions[:-1])}, and {descriptions[-1]}. If you want to know more about any package, just ask me."


class PackageMatcher:
    @staticmethod
    def match(packages, query):
        if not packages or not query:
            return None
        try:
            query = query.lower().strip()
            query_clean = ''.join(c for c in query if c.isalnum())
            logger.info(f"PackageMatcher - Query: '{query}', Clean: '{query_clean}'")

            for p in packages:
                carrier = p.get('carrier', '').lower().strip()
                carrier_clean = ''.join(c for c in carrier if c.isalnum())
                if query == carrier or query_clean == carrier_clean:
                    return p
            for p in packages:
                tracking = p.get('tracking_number', '').lower().strip()
                tracking_clean = ''.join(c for c in tracking if c.isalnum())
                if query_clean and (query_clean == tracking_clean or query_clean in tracking_clean):
                    return p
            for p in packages:
                carrier = p.get('carrier', '').lower().strip()
                carrier_clean = ''.join(c for c in carrier if c.isalnum())
                if query in carrier or carrier in query or query_clean in carrier_clean or carrier_clean in query_clean:
                    return p
            return None
        except Exception as e:
            logger.error(f"PackageMatcher error: {e}")
            return None


def resolve_package(handler_input, packages: List[Dict]):
    """
    Single source of truth for 'which package'.
    Returns (status, package_or_None):
      "resolved"      -> package dict
      "not_found"     -> selector given but no match (must NOT silently fall back)
      "no_selector"   -> nothing given; caller decides single-vs-multiple behavior
    """
    tracking_value = get_slot_value(handler_input, 'tracking')
    if tracking_value:
        found = PackageMatcher.match(packages, tracking_value)
        return ("resolved", found) if found else ("not_found", None)

    carrier_value = get_slot_value(handler_input, 'carrier')
    if carrier_value:
        found = PackageMatcher.match(packages, carrier_value)
        return ("resolved", found) if found else ("not_found", None)

    return ("no_selector", None)


# ============================================
# WEBHOOK HANDLER
# ============================================

def handle_package_event(event: Dict, context: Any) -> Dict:
    logger.info(f"📦 Webhook event received: {event}")
    data = event.get('data', {})
    unit = data.get('unit')
    package_id = data.get('package_id')
    carrier = data.get('carrier', 'courier')
    tracking_number = data.get('tracking_number')
    compartment = data.get('compartment')
    delivered_at = data.get('delivered_at')

    if not unit or not package_id:
        return {"status": "error", "message": "Missing unit or package_id"}

    user_config = get_user_configuration(unit)
    if not user_config:
        return {"status": "error", "message": f"User unit {unit} not found"}
    if not user_config.get('opted_alexa', False):
        return {"status": "skipped", "reason": "User not opted in"}

    alexa_user_id = user_config.get('alexa_user_id')
    LATEST_PACKAGES.setdefault(unit, []).append({
        "package_id": package_id, "carrier": carrier, "tracking_number": tracking_number,
        "compartment": compartment, "delivered_at": delivered_at, "unit": unit
    })
    logger.info(f"📦 Added package. Total for unit {unit}: {len(LATEST_PACKAGES[unit])}")

    if not alexa_user_id:
        return {"status": "skipped", "reason": "No Alexa User ID linked"}

    result = alexa_client.send_notification(alexa_user_id, carrier, package_id, tracking_number, compartment, delivered_at, unit)
    if result.get('status') == 'success':
        return {"status": "success", "package_id": package_id, "unit": unit, "message": f"Notification sent for {carrier} package"}
    return {"status": "error", "package_id": package_id, "error": result.get('message')}


# ============================================
# INTENT HANDLERS
# ============================================

class ProactiveSubscriptionChangedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AlexaSkillEvent.ProactiveSubscriptionChanged")(handler_input)
    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        logger.info(f"✅ SUBSCRIPTION EVENT RECEIVED for user: {user_id}")
        return handler_input.response_builder.response


class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        global LATEST_ALEXA_USER_ID, CURRENT_UNIT
        LATEST_ALEXA_USER_ID = handler_input.request_envelope.context.system.user.user_id
        CURRENT_UNIT = USER_ID_TO_UNIT.get(LATEST_ALEXA_USER_ID, "4B")
        logger.info(f"🚀 User {LATEST_ALEXA_USER_ID[:20]}... mapped to unit {CURRENT_UNIT}")

        session_attr = handler_input.attributes_manager.session_attributes
        session_attr.pop('current_package', None)

        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        speak_output = get_package_summary(packages) if packages else "Welcome to Notifii Alert. Your account is connected for package updates. You have no packages at the moment."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            return handler_input.response_builder.speak("You have no packages right now.").response
        speak_output = get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Would you like details about any package?").response


class PackageDetailsIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageDetailsIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            return handler_input.response_builder.speak("You have no packages to get details about.").response

        session_attr = handler_input.attributes_manager.session_attributes
        status, found = resolve_package(handler_input, packages)

        if status == "resolved":
            session_attr['current_package'] = found
            speak_output = format_package_details(found)
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response

        if status == "not_found":
            speak_output = "I couldn't find a package matching that. You can ask about a specific carrier or tracking number."
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response

        # no_selector
        if len(packages) == 1:
            session_attr['current_package'] = packages[0]
            speak_output = format_package_details(packages[0])
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response

        speak_output = format_all_package_details(packages)
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class WhichPackageIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("WhichPackageIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        status, found = resolve_package(handler_input, packages)

        if status == "resolved":
            session_attr['current_package'] = found
            speak_output = format_package_details(found)
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response

        speak_output = "I couldn't find that package. " + get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response


class CarrierInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CarrierInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            return handler_input.response_builder.speak("You have no packages to inquire about.").response

        session_attr = handler_input.attributes_manager.session_attributes
        status, found = resolve_package(handler_input, packages)

        if status == "not_found":
            return handler_input.response_builder.speak("I couldn't find that package. Please specify the carrier or tracking number.").ask("Anything else?").response

        if status == "no_selector":
            found = session_attr.get('current_package') or packages[-1]

        session_attr['current_package'] = found
        speak_output = f"This package was delivered by {found.get('carrier', 'unknown carrier')}."
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class TrackingInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("TrackingInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            return handler_input.response_builder.speak("You have no packages to inquire about.").response

        session_attr = handler_input.attributes_manager.session_attributes
        status, found = resolve_package(handler_input, packages)

        if status == "not_found":
            return handler_input.response_builder.speak("I couldn't find that package. Please specify the carrier or tracking number.").ask("Anything else?").response

        if status == "no_selector":
            found = session_attr.get('current_package') or packages[-1]

        session_attr['current_package'] = found
        speak_output = f"The tracking number for the {found.get('carrier', '')} package is {found.get('tracking_number', 'not available')}."
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class CompartmentInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CompartmentInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            return handler_input.response_builder.speak("You have no packages to inquire about.").response

        session_attr = handler_input.attributes_manager.session_attributes
        status, found = resolve_package(handler_input, packages)

        if status == "not_found":
            return handler_input.response_builder.speak("I couldn't find that package. Please specify the carrier or tracking number.").ask("Anything else?").response

        if status == "no_selector":
            found = session_attr.get('current_package') or packages[-1]

        session_attr['current_package'] = found
        speak_output = f"This package is stored in compartment {found.get('compartment', 'unknown compartment')}."
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class DeliveryInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("DeliveryInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            return handler_input.response_builder.speak("You have no packages to inquire about.").response

        session_attr = handler_input.attributes_manager.session_attributes
        status, found = resolve_package(handler_input, packages)

        if status == "not_found":
            return handler_input.response_builder.speak("I couldn't find that package. Please specify the carrier or tracking number.").ask("Anything else?").response

        if status == "no_selector":
            found = session_attr.get('current_package') or packages[-1]

        session_attr['current_package'] = found
        delivered_at = found.get('delivered_at')
        if delivered_at:
            try:
                dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
                delivered_str = dt.strftime('%B %d, %Y at %I:%M %p')
            except Exception:
                delivered_str = delivered_at
        else:
            delivered_str = 'recently'
        speak_output = f"The {found.get('carrier', 'unknown carrier')} package was delivered on {delivered_str}."
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class ExitIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("ExitIntent")(handler_input)
    def handle(self, handler_input):
        return handler_input.response_builder.speak("Goodbye! Have a great day.").set_should_end_session(True).response


class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)
    def handle(self, handler_input):
        speak_output = "You can ask me about your packages. For example, specific details like carrier, tracking number, compartment, or delivery date."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class CancelAndStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return (ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or
                ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input))
    def handle(self, handler_input):
        return handler_input.response_builder.speak("Goodbye! Have a great day.").set_should_end_session(True).response


class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            return handler_input.response_builder.speak("You have no packages right now. Would you like to know anything else?").ask("How can I help you?").response

        session_attr = handler_input.attributes_manager.session_attributes
        speak_output = get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("What would you like to know?").response


class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True
    def handle(self, handler_input, exception):
        logger.error(f"Error handling request: {exception}", exc_info=True)
        return handler_input.response_builder.speak("Sorry, I had trouble processing your request. Please try again.").response


class SkillPermissionChangedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AlexaSkillEvent.SkillPermissionChanged")(handler_input)
    def handle(self, handler_input):
        logger.info("Permission Changed")
        return handler_input.response_builder.response


class SkillDisabledHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AlexaSkillEvent.SkillDisabled")(handler_input)
    def handle(self, handler_input):
        logger.info("Skill Disabled")
        return handler_input.response_builder.response


class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("SessionEndedRequest")(handler_input)
    def handle(self, handler_input):
        logger.info("Session ended")
        return handler_input.response_builder.response


sb = SkillBuilder()
sb.add_request_handler(SkillPermissionChangedHandler())
sb.add_request_handler(SkillDisabledHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(ProactiveSubscriptionChangedHandler())
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(PackageDetailsIntentHandler())
sb.add_request_handler(WhichPackageIntentHandler())
sb.add_request_handler(CarrierInquiryIntentHandler())
sb.add_request_handler(TrackingInquiryIntentHandler())
sb.add_request_handler(CompartmentInquiryIntentHandler())
sb.add_request_handler(DeliveryInquiryIntentHandler())
sb.add_request_handler(ExitIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()