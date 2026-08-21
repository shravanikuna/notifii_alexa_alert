import logging
import json
import os
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List

import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response
import db

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class Config:
    ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '').strip()
    ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '').strip()
    ALEXA_API_URL = os.environ.get('ALEXA_API_URL', '').strip()

config = Config()
CURRENT_UNIT = None
CURRENT_RESIDENT_ID = None

SESSION_GAP_THRESHOLD_DAYS = 2  # per spec: 2+ days gap => report ALL packages again


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
        payload = {"grant_type": "client_credentials", "client_id": self.client_id,
                   "client_secret": self.client_secret, "scope": "alexa::proactive_events"}
        try:
            response = requests.post(token_url, data=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                self._cached_token = data["access_token"]
                expires_in = data.get("expires_in", 3600)
                self._token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)
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
        try:
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 202:
                return {"status": "success", "code": 202}
            logger.error(f"Proactive Events API error: {response.status_code} - {response.text}")
            return {"status": "error", "code": response.status_code, "message": response.text}
        except Exception as e:
            logger.error(f"Send notification error: {str(e)}")
            return {"status": "error", "message": str(e)}

alexa_client = AlexaProactiveEventsClient()


# ============================================
# HELPERS
# ============================================
def parse_iso_to_mysql_datetime(iso_string: Optional[str]) -> Optional[str]:
    if not iso_string:
        return None
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Failed to parse delivered_at '{iso_string}': {e}")
        return None

def calculate_waiting_days(delivered_at: Optional[str]) -> int:
    if not delivered_at:
        return 0
    try:
        dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00')) if isinstance(delivered_at, str) else delivered_at
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        return (now - dt).days
    except Exception:
        return 0

def get_slot_value(handler_input, slot_name: str) -> Optional[str]:
    try:
        slots = handler_input.request_envelope.request.intent.slots
        if not slots or slot_name not in slots:
            return None
        value = slots[slot_name].value
        return str(value).strip() if value else None
    except Exception as e:
        logger.error(f"get_slot_value error for '{slot_name}': {e}")
        return None

def _format_dt(delivered_at):
    if not delivered_at:
        return 'recently'
    try:
        if isinstance(delivered_at, str):
            dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S')
        else:
            dt = delivered_at
        return dt.strftime('%B %d, %Y at %I:%M %p')
    except Exception:
        return str(delivered_at)

def format_package_details(package: Dict) -> str:
    carrier = package.get('carrier', 'unknown carrier')
    tracking = package.get('tracking_number', 'no tracking number')
    compartment = package.get('compartment', 'unknown compartment')
    return f"Package from {carrier}, tracking number {tracking}, stored in compartment {compartment}, delivered on {_format_dt(package.get('delivered_at'))}"

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

def get_current_packages() -> List[Dict]:
    """Full package list for CURRENT_RESIDENT — used for in-session follow-up detail questions."""
    if not CURRENT_RESIDENT_ID:
        return []
    return db.get_all_packages_for_resident(CURRENT_RESIDENT_ID)

def get_status_report_packages(resident: Dict) -> List[Dict]:
    """
    Implements the 'only tell them what they haven't heard' rule:
      - Gap since last session >= 2 days -> report ALL packages, mark ALL heard
      - Otherwise -> report only unheard packages, mark those heard
    """
    resident_id = resident['id']
    last_session_at = resident.get('last_session_at')

    gap_days = None
    if last_session_at:
        try:
            gap_days = (datetime.now() - last_session_at).days
        except Exception:
            gap_days = None

    if gap_days is None or gap_days >= SESSION_GAP_THRESHOLD_DAYS:
        packages = db.get_all_packages_for_resident(resident_id)
    else:
        packages = db.get_unheard_packages_for_resident(resident_id)

    if packages:
        db.mark_packages_heard([p['id'] for p in packages])
    db.update_last_session(resident_id)

    return packages


class PackageMatcher:
    @staticmethod
    def match(packages, query):
        if not packages or not query:
            return None
        try:
            query = query.lower().strip()
            query_clean = ''.join(c for c in query if c.isalnum())
            for p in packages:
                carrier = (p.get('carrier') or '').lower().strip()
                carrier_clean = ''.join(c for c in carrier if c.isalnum())
                if query == carrier or query_clean == carrier_clean:
                    return p
            for p in packages:
                tracking = (p.get('tracking_number') or '').lower().strip()
                tracking_clean = ''.join(c for c in tracking if c.isalnum())
                if query_clean and (query_clean == tracking_clean or query_clean in tracking_clean):
                    return p
            for p in packages:
                carrier = (p.get('carrier') or '').lower().strip()
                carrier_clean = ''.join(c for c in carrier if c.isalnum())
                if query in carrier or carrier in query or query_clean in carrier_clean or carrier_clean in query_clean:
                    return p
            return None
        except Exception as e:
            logger.error(f"PackageMatcher error: {e}")
            return None

def resolve_package(handler_input, packages: List[Dict]):
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
    address = data.get('unit')
    package_id = data.get('package_id')
    carrier = data.get('carrier', 'courier')
    tracking_number = data.get('tracking_number')
    compartment = data.get('compartment')
    delivered_at_raw = data.get('delivered_at')
    delivered_at = parse_iso_to_mysql_datetime(delivered_at_raw)

    if not address or not package_id:
        return {"status": "error", "message": "Missing address or package_id"}

    resident = db.get_resident_by_address(address)
    if not resident:
        return {"status": "error", "message": f"No resident found for address: {address}"}

    alexa_user_id = resident.get('alexa_user_id')

    # save_or_update_package NEVER creates a duplicate row for the same package_id+resident
    package_row, is_new = db.save_or_update_package(
        resident_id=resident['id'], package_id=package_id, carrier=carrier,
        tracking_number=tracking_number, compartment=compartment, delivered_at=delivered_at
    )
    if not package_row:
        return {"status": "error", "message": "Failed to save package to database"}

    if not alexa_user_id:
        db.log_notification(package_row['id'], None, "failed", "No Alexa account linked")
        return {"status": "skipped_notification", "package_id": package_id, "reason": "no_alexa_link"}

    if is_new:
        result = alexa_client.send_notification(alexa_user_id, carrier, package_id, tracking_number, compartment, delivered_at_raw, address)
        if result.get('status') == 'success':
            db.mark_package_notified(package_row['id'])
            db.log_notification(package_row['id'], None, "sent", "Initial delivery notification")
            return {"status": "success", "package_id": package_id, "message": "New package notification sent"}
        db.log_notification(package_row['id'], None, "failed", result.get('message'))
        return {"status": "error", "package_id": package_id, "error": result.get('message')}

    # Existing package re-sent (not picked up) — only re-notify on reminder days, never duplicate the row
    days_waiting = calculate_waiting_days(delivered_at_raw)
    reminder_thresholds = [3, 5, 7]
    current_reminder_count = package_row.get('reminder_count', 0)

    if days_waiting in reminder_thresholds and current_reminder_count < reminder_thresholds.index(days_waiting) + 1:
        result = alexa_client.send_notification(alexa_user_id, carrier, package_id, tracking_number, compartment, delivered_at_raw, address)
        if result.get('status') == 'success':
            db.increment_reminder(package_row['id'])
            db.log_notification(package_row['id'], None, "sent", f"Day {days_waiting} reminder")
            return {"status": "success", "package_id": package_id, "message": f"Day {days_waiting} reminder sent"}
        db.log_notification(package_row['id'], None, "failed", result.get('message'))
        return {"status": "error", "package_id": package_id, "error": result.get('message')}

    return {"status": "no_action", "package_id": package_id, "reason": "already notified, not due for reminder"}


# ============================================
# INTENT HANDLERS
# ============================================
class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        global CURRENT_UNIT, CURRENT_RESIDENT_ID
        alexa_user_id = handler_input.request_envelope.context.system.user.user_id

        resident = db.get_resident_by_alexa_id(alexa_user_id)

        if not resident:
            # Genuinely unrecognized account — only case where linking is needed
            speak_output = "Welcome to Notifii Alert. Please say your unit number to link your account."
            return handler_input.response_builder.speak(speak_output).ask("What's your unit number?").response

        CURRENT_UNIT = resident['unit']
        CURRENT_RESIDENT_ID = resident['id']

        packages = get_status_report_packages(resident)
        if packages:
            speak_output = "Welcome to Notifii Alert. " + get_package_summary(packages)
        else:
            speak_output = "Welcome to Notifii Alert. You have no packages right now."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class LinkUnitIntentHandler(AbstractRequestHandler):
    """Fallback only — used when a resident's row genuinely has no alexa_user_id yet."""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("LinkUnitIntent")(handler_input)

    def handle(self, handler_input):
        global CURRENT_UNIT, CURRENT_RESIDENT_ID
        alexa_user_id = handler_input.request_envelope.context.system.user.user_id
        unit_value = get_slot_value(handler_input, 'unit')

        if not unit_value:
            speak_output = "Which unit number should I link?"
            return handler_input.response_builder.speak(speak_output).ask(speak_output).response

        resident = db.get_resident_by_address(unit_value)
        if resident and resident.get('alexa_user_id') and resident['alexa_user_id'] != alexa_user_id:
            speak_output = f"Unit {unit_value} is already linked to another account. Please contact support."
            return handler_input.response_builder.speak(speak_output).response

        success = db.link_resident(unit_value, alexa_user_id, region="EU")
        if success:
            CURRENT_UNIT = unit_value
            resident = db.get_resident_by_address(unit_value)
            CURRENT_RESIDENT_ID = resident['id'] if resident else None
            speak_output = f"Got it. Unit {unit_value} is now linked for package notifications."
        else:
            speak_output = "Sorry, I couldn't link your unit right now. Please try again."
        return handler_input.response_builder.speak(speak_output).response


class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        alexa_user_id = handler_input.request_envelope.context.system.user.user_id
        resident = db.get_resident_by_alexa_id(alexa_user_id)
        if not resident:
            speak_output = "It looks like your account isn't linked yet. Please say your unit number to link it."
            return handler_input.response_builder.speak(speak_output).ask("What's your unit number?").response

        packages = get_status_report_packages(resident)
        if not packages:
            return handler_input.response_builder.speak("You don't have any new notifications right now.").response

        speak_output = get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Would you like details about any package?").response


class PackageDetailsIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageDetailsIntent")(handler_input)

    def handle(self, handler_input):
        packages = get_current_packages()
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
        packages = get_current_packages()
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
        packages = get_current_packages()
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
        packages = get_current_packages()
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
        packages = get_current_packages()
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
        packages = get_current_packages()
        if not packages:
            return handler_input.response_builder.speak("You have no packages to inquire about.").response
        session_attr = handler_input.attributes_manager.session_attributes
        status, found = resolve_package(handler_input, packages)
        if status == "not_found":
            return handler_input.response_builder.speak("I couldn't find that package. Please specify the carrier or tracking number.").ask("Anything else?").response
        if status == "no_selector":
            found = session_attr.get('current_package') or packages[-1]
        session_attr['current_package'] = found
        speak_output = f"The {found.get('carrier', 'unknown carrier')} package was delivered on {_format_dt(found.get('delivered_at'))}."
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
        packages = get_current_packages()
        if not packages:
            return handler_input.response_builder.speak("You have no packages right now. Would you like to know anything else?").ask("How can I help you?").response
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
        return handler_input.response_builder.response


class SkillDisabledHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AlexaSkillEvent.SkillDisabled")(handler_input)
    def handle(self, handler_input):
        return handler_input.response_builder.response


class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("SessionEndedRequest")(handler_input)
    def handle(self, handler_input):
        return handler_input.response_builder.response


sb = SkillBuilder()
sb.add_request_handler(LinkUnitIntentHandler())
sb.add_request_handler(SkillPermissionChangedHandler())
sb.add_request_handler(SkillDisabledHandler())
sb.add_request_handler(SessionEndedRequestHandler())
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