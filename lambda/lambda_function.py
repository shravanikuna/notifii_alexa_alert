import logging
import json
import os
import uuid
import requests
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List

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
# CONFIGURATION
# ============================================
class Config:
    ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '').strip()
    ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '').strip()
    ALEXA_API_URL = os.environ.get('ALEXA_API_URL', '').strip()
    LATEST_ALEXA_USER_ID = os.environ.get('ALEXA_USER_ID', '').strip()

config = Config()

# ============================================
# GLOBAL STATE (POC only — resets on restart)
# ============================================
LATEST_PACKAGES = {}
CURRENT_UNIT = "4B"
USER_ID_TO_UNIT = {
    config.LATEST_ALEXA_USER_ID: "4B"
}

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

    def send_notification(self, alexa_user_id: str, carrier: str, package_id: str,
                          tracking_number: str = None, compartment: str = None,
                          delivered_at: str = None, unit: str = None) -> Dict:
        token = self.get_token()
        if not token:
            return {"status": "error", "message": "Failed to obtain LWA access token"}

        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        expiry = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")

        payload = {
            "timestamp": now,
            "referenceId": f"notifii.{package_id}.{int(datetime.utcnow().timestamp())}",
            "expiryTime": expiry,
            "event": {
                "name": "AMAZON.OrderStatus.Updated",
                "payload": {
                    "state": {"status": "ORDER_DELIVERED", "deliveredOn": delivered_at or now},
                    "order": {
                        "seller": {"name": "localizedattribute:sellerName"},
                        "orderId": package_id,
                        "trackingNumber": tracking_number,
                        "delivery": {"compartment": compartment or "unknown", "unit": unit or "unknown"}
                    }
                }
            },
            "localizedAttributes": [{"locale": "en-US", "sellerName": carrier}],
            "relevantAudience": {"type": "Unicast", "payload": {"user": alexa_user_id}}
        }

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        logger.info(f"OUTGOING PAYLOAD: {json.dumps(payload)}")
        try:
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 202:
                logger.info(f"Notification successfully sent to {alexa_user_id}")
                logger.info(f"Amazon Request ID: {response.headers.get('x-amzn-requestid')}")
                return {"status": "success", "code": 202}
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
    active_user_id = os.environ.get('ALEXA_USER_ID', '').strip()
    configs = {
        "4B": {"unit": "4B", "opted_alexa": True, "alexa_user_id": active_user_id},
        "2A": {"unit": "2A", "opted_alexa": True, "alexa_user_id": active_user_id}
    }
    return configs.get(unit)

# ============================================
# HELPERS — formatting
# ============================================
def format_delivered_date(delivered_at: str) -> str:
    if not delivered_at:
        return "recently"
    try:
        dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
        return dt.strftime('%B %d, %Y at %I:%M %p')
    except Exception:
        return delivered_at

def format_full_details(package: Dict) -> str:
    carrier = package.get('carrier', 'an unknown carrier')
    tracking = package.get('tracking_number', 'no tracking number')
    compartment = package.get('compartment', 'an unknown compartment')
    delivered_str = format_delivered_date(package.get('delivered_at'))
    return (f"Your package from {carrier}, tracking number {tracking}, "
            f"is stored in compartment {compartment}, delivered on {delivered_str}.")

def format_short_line(package: Dict) -> str:
    carrier = package.get('carrier', 'unknown carrier')
    tracking = package.get('tracking_number', 'no tracking number')
    return f"one from {carrier}, tracking number {tracking}"

def get_carrier_list_summary(packages: List[Dict]) -> str:
    """Used on launch — just carrier names, not full details."""
    carriers = [p.get('carrier', 'unknown') for p in packages]
    if len(carriers) == 1:
        return f"You have a package from {carriers[0]}."
    return f"You have packages from {', '.join(carriers[:-1])} and {carriers[-1]}."

def get_package_status_summary(packages: List[Dict]) -> str:
    """Used for 'do I have any packages' — carrier + tracking per package."""
    if not packages:
        return "You have no packages right now."
    if len(packages) == 1:
        return f"You have one package: {format_short_line(packages[0])}. Would you like to know more?"
    lines = [format_short_line(p) for p in packages]
    return f"You have {len(packages)} packages: {', '.join(lines)}. Would you like to know more about any of them?"

# ============================================
# HELPERS — improved lookup with normalization
# ============================================
def normalize_text(text: str) -> str:
    """Normalize text by removing periods, spaces, and converting to lowercase"""
    if not text:
        return ""
    # Remove periods, spaces, hyphens, and convert to lowercase
    normalized = re.sub(r'[.\s\-]+', '', text.lower().strip())
    return normalized

def find_packages_by_carrier(packages: List[Dict], carrier_query: str) -> List[Dict]:
    """Find packages by carrier with improved matching"""
    if not carrier_query:
        return []
    
    # Normalize the query
    query_normalized = normalize_text(carrier_query)
    
    # Try exact match first
    exact_matches = []
    partial_matches = []
    
    for p in packages:
        carrier = p.get('carrier', '')
        carrier_normalized = normalize_text(carrier)
        
        # Check if the normalized carrier contains the query or vice versa
        if query_normalized in carrier_normalized or carrier_normalized in query_normalized:
            # Prefer exact matches over partial
            if query_normalized == carrier_normalized:
                exact_matches.append(p)
            else:
                partial_matches.append(p)
    
    # Return exact matches first, then partial matches
    return exact_matches + partial_matches

def find_package_by_tracking(packages: List[Dict], tracking_query: str) -> Optional[Dict]:
    """Find a package by tracking number with improved matching"""
    if not tracking_query:
        return None
    
    # Normalize the query
    query_normalized = normalize_text(tracking_query)
    
    # Try exact match first, then partial
    for p in packages:
        stored = p.get('tracking_number', '')
        stored_normalized = normalize_text(stored)
        
        # Check if the normalized tracking contains the query or vice versa
        if query_normalized in stored_normalized or stored_normalized in query_normalized:
            return p
    
    return None

def get_slot_value(handler_input, slot_name: str) -> Optional[str]:
    try:
        slot = handler_input.request_envelope.request.intent.slots.get(slot_name, {})
        value = slot.get('value')
        return value.strip() if value else None
    except Exception:
        return None

# ============================================
# CORE RESOLVER — single source of truth for "which package?"
# ============================================
def resolve_package(handler_input, packages: List[Dict]):
    """
    Returns one of:
      ("resolved", package_dict)      — exactly one package identified
      ("ambiguous", [package, ...])   — multiple matches, need tracking number
      ("not_found", None)             — no match at all
      ("need_selection", None)        — no slot given and more than one package exists
    """
    session_attr = handler_input.attributes_manager.session_attributes

    if not packages:
        return ("not_found", None)

    # Get slot values
    carrier_value = get_slot_value(handler_input, 'carrier')
    tracking_value = get_slot_value(handler_input, 'tracking')
    
    logger.info(f"🔍 Resolving package - carrier: '{carrier_value}', tracking: '{tracking_value}'")
    logger.info(f"📦 Total packages: {len(packages)}")

    # 1. TRACKING NUMBER - Most specific, ALWAYS wins
    if tracking_value:
        logger.info(f"🔢 Looking for tracking number: '{tracking_value}'")
        found = find_package_by_tracking(packages, tracking_value)
        if found:
            session_attr['current_package'] = found
            session_attr.pop('pending_matches', None)
            logger.info(f"✅ Resolved by tracking: {found.get('tracking_number')} -> {found.get('carrier')}")
            return ("resolved", found)
        logger.info(f"❌ No package found with tracking: '{tracking_value}'")
        return ("not_found", None)

    # 2. CARRIER NAME - Can be ambiguous
    if carrier_value:
        logger.info(f"🏷️ Looking for carrier: '{carrier_value}'")
        matches = find_packages_by_carrier(packages, carrier_value)
        logger.info(f"📊 Found {len(matches)} matches for carrier '{carrier_value}'")
        
        if len(matches) == 1:
            session_attr['current_package'] = matches[0]
            session_attr.pop('pending_matches', None)
            logger.info(f"✅ Resolved by carrier: {matches[0].get('carrier')} -> {matches[0].get('tracking_number')}")
            return ("resolved", matches[0])
        elif len(matches) > 1:
            session_attr['pending_matches'] = matches
            logger.info(f"⚠️ Ambiguous: {len(matches)} packages from {carrier_value}")
            return ("ambiguous", matches)
        else:
            logger.info(f"❌ No packages found for carrier: '{carrier_value}'")
            return ("not_found", None)

    # 3. SESSION CONTEXT - Use remembered package
    current = session_attr.get('current_package')
    if current and current in packages:
        logger.info(f"🔄 Resolved from session: {current.get('carrier')} -> {current.get('tracking_number')}")
        return ("resolved", current)

    # 4. ONLY ONE PACKAGE - Auto-select
    if len(packages) == 1:
        session_attr['current_package'] = packages[0]
        logger.info(f"📌 Auto-selected only package: {packages[0].get('carrier')}")
        return ("resolved", packages[0])

    # 5. MULTIPLE PACKAGES - Need user to specify
    logger.info(f"❓ Need selection - {len(packages)} packages available")
    return ("need_selection", None)
def ambiguous_prompt(matches: List[Dict]) -> str:
    carrier = matches[0].get('carrier', 'that carrier')
    trackings = [m.get('tracking_number', 'unknown') for m in matches]
    joined = " or ".join(trackings)
    return f"You have {len(matches)} packages from {carrier}. Please confirm the tracking number — {joined}?"

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

    package_info = {
        "package_id": package_id,
        "carrier": carrier,
        "tracking_number": tracking_number,
        "compartment": compartment,
        "delivered_at": delivered_at,
        "unit": unit
    }
    LATEST_PACKAGES.setdefault(unit, []).append(package_info)
    logger.info(f"📦 Added package. Total for unit {unit}: {len(LATEST_PACKAGES[unit])}")

    if not alexa_user_id:
        return {"status": "skipped", "reason": "No Alexa User ID linked"}

    result = alexa_client.send_notification(
        alexa_user_id, carrier, package_id, tracking_number, compartment, delivered_at, unit
    )

    if result.get('status') == 'success':
        return {"status": "success", "package_id": package_id, "unit": unit,
                "message": f"Notification sent for {carrier} package"}
    return {"status": "error", "package_id": package_id, "error": result.get('message')}

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


class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        global LATEST_ALEXA_USER_ID, CURRENT_UNIT
        LATEST_ALEXA_USER_ID = handler_input.request_envelope.context.system.user.user_id
        CURRENT_UNIT = USER_ID_TO_UNIT.get(LATEST_ALEXA_USER_ID, "4B")
        logger.info(f"🚀 User {LATEST_ALEXA_USER_ID[:20]}... mapped to unit {CURRENT_UNIT}")

        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if packages:
            speak_output = "Welcome, your account is connected. " + get_carrier_list_summary(packages)
        else:
            speak_output = "Welcome, your account is connected."

        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        speak_output = get_package_status_summary(packages)
        if packages:
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like to know more about?").response
        return handler_input.response_builder.speak(speak_output).response


class PackageDetailsIntentHandler(AbstractRequestHandler):
    """Handles: 'give me more details about the FedEx package'"""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageDetailsIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        status, result = resolve_package(handler_input, packages)

        if status == "resolved":
            speak_output = format_full_details(result)
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
        elif status == "ambiguous":
            speak_output = ambiguous_prompt(result)
            return handler_input.response_builder.speak(speak_output).ask("Which tracking number?").response
        elif status == "need_selection":
            speak_output = "Which package would you like to know about — you can say the carrier name or tracking number."
            return handler_input.response_builder.speak(speak_output).ask(speak_output).response
        else:
            speak_output = "I couldn't find a package matching that. You have no packages right now." if not packages else "I couldn't find that package. Try saying the carrier name or tracking number."
            return handler_input.response_builder.speak(speak_output).response


class TrackingNumberIntentHandler(AbstractRequestHandler):
    """Handles replies like '555' when disambiguating."""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("TrackingNumberIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        status, result = resolve_package(handler_input, packages)

        if status == "resolved":
            speak_output = format_full_details(result)
        elif status == "not_found":
            speak_output = "I couldn't find a package with that tracking number. Could you say it again?"
        else:
            speak_output = "I didn't catch a tracking number. Could you repeat it?"

        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class CarrierInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CarrierInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        status, result = resolve_package(handler_input, packages)
        if status == "resolved":
            speak_output = f"This package was delivered by {result.get('carrier', 'an unknown carrier')}."
        elif status == "ambiguous":
            speak_output = ambiguous_prompt(result)
        else:
            speak_output = "I couldn't find that package. Please specify the carrier or tracking number."
        return handler_input.response_builder.speak(speak_output).ask("Anything else?").response


class TrackingInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("TrackingInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        status, result = resolve_package(handler_input, packages)
        if status == "resolved":
            speak_output = f"The tracking number is {result.get('tracking_number', 'not available')}."
        elif status == "ambiguous":
            speak_output = ambiguous_prompt(result)
        else:
            speak_output = "I couldn't find that package. Please specify the carrier or tracking number."
        return handler_input.response_builder.speak(speak_output).ask("Anything else?").response


class CompartmentInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CompartmentInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        status, result = resolve_package(handler_input, packages)
        if status == "resolved":
            speak_output = f"This package is stored in compartment {result.get('compartment', 'unknown')}."
        elif status == "ambiguous":
            speak_output = ambiguous_prompt(result)
        else:
            speak_output = "I couldn't find that package. Please specify the carrier or tracking number."
        return handler_input.response_builder.speak(speak_output).ask("Anything else?").response


class DeliveryInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("DeliveryInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        status, result = resolve_package(handler_input, packages)
        if status == "resolved":
            speak_output = f"This package was delivered on {format_delivered_date(result.get('delivered_at'))}."
        elif status == "ambiguous":
            speak_output = ambiguous_prompt(result)
        else:
            speak_output = "I couldn't find that package. Please specify the carrier or tracking number."
        return handler_input.response_builder.speak(speak_output).ask("Anything else?").response


class ExitIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("ExitIntent")(handler_input)
    def handle(self, handler_input):
        return handler_input.response_builder.speak("Goodbye! Have a great day.").set_should_end_session(True).response


class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)
    def handle(self, handler_input):
        speak_output = ("You can ask 'do I have any packages', then ask about a specific carrier "
                         "like 'tell me about the FedEx package'. If you have more than one from the "
                         "same carrier, I'll ask you to confirm the tracking number.")
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
        session_attr = handler_input.attributes_manager.session_attributes

        if session_attr.get('pending_matches'):
            speak_output = "Sorry, I didn't catch that. Please say the tracking number."
        elif not packages:
            speak_output = "You have no packages right now."
        else:
            speak_output = get_package_status_summary(packages)

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
sb.add_request_handler(PackageDetailsIntentHandler())
sb.add_request_handler(TrackingNumberIntentHandler())
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