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
import db

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

CURRENT_USER_ID = None
CURRENT_UNIT = "4B"
USER_ID_TO_UNIT = {config.LATEST_ALEXA_USER_ID: "4B"}

# ============================================
# DATETIME HELPER
# ============================================

def parse_iso_to_mysql_datetime(iso_string: Optional[str]) -> Optional[str]:
    """Converts '2026-08-24T08:05:00.000Z' -> '2026-08-24 08:05:00' for MySQL"""
    if not iso_string:
        return None
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Failed to parse datetime '{iso_string}': {e}")
        return None

def calculate_waiting_days(delivered_at: Optional[str]) -> int:
    if not delivered_at:
        return 0
    try:
        if isinstance(delivered_at, str):
            dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S')
        else:
            dt = delivered_at
        now = datetime.now()
        days = (now - dt).days
        return days if days > 0 else 0
    except Exception as e:
        logger.error(f"calculate_waiting_days error: {e}")
        return 0

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
                logger.info(f"✅ Successfully obtained LWA token")
                return self._cached_token
            logger.error(f"❌ Token failed: {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"❌ Token fetch error: {str(e)}")
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
        logger.info(f"📤 Sending notification for {tracking_number}")
        try:
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 202:
                logger.info(f"✅ Notification sent successfully")
                return {"status": "success", "code": 202}
            logger.error(f"❌ API error: {response.status_code}")
            return {"status": "error", "code": response.status_code, "message": response.text}
        except Exception as e:
            logger.error(f"❌ Send error: {str(e)}")
            return {"status": "error", "message": str(e)}

alexa_client = AlexaProactiveEventsClient()

# ============================================
# HELPERS
# ============================================

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

def format_package_details(package: Dict) -> str:
    carrier = package.get('carrier', 'unknown carrier')
    tracking = package.get('tracking_number', 'no tracking number')
    compartment = package.get('compartment', 'unknown compartment')
    delivered_at = package.get('delivered_at')
    package_id = package.get('package_id', 'unknown')
    
    if delivered_at:
        try:
            if isinstance(delivered_at, str):
                dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S')
            else:
                dt = delivered_at
            delivered_str = dt.strftime('%B %d, %Y at %I:%M %p IST')
            waiting_days = calculate_waiting_days(delivered_at)
            if waiting_days == 0:
                time_str = "today"
            elif waiting_days == 1:
                time_str = "yesterday"
            else:
                time_str = f"{waiting_days} days ago"
        except Exception:
            delivered_str = str(delivered_at)
            time_str = ""
    else:
        delivered_str = 'recently'
        time_str = ""
    
    return f"Package from {carrier}, tracking number {tracking}, stored in compartment {compartment}, delivered on {delivered_str} ({time_str})."

def format_all_package_details(packages: List[Dict]) -> str:
    return " Also, ".join(format_package_details(p) for p in packages)

def get_package_summary(packages: List[Dict]) -> str:
    if not packages:
        return "You have no packages right now."
    
    descriptions = []
    for p in packages:
        carrier = p.get('carrier', 'unknown')
        tracking = p.get('tracking_number', 'unknown')
        waiting_days = calculate_waiting_days(p.get('delivered_at'))
        if waiting_days == 0:
            waiting_str = "arrived today"
        elif waiting_days == 1:
            waiting_str = "arrived 1 day ago"
        else:
            waiting_str = f"arrived {waiting_days} days ago"
        descriptions.append(f"from {carrier} with tracking {tracking} ({waiting_str})")
    
    if len(descriptions) == 1:
        return f"You have a package {descriptions[0]}. If you want to know more about this package, just ask me."
    elif len(descriptions) == 2:
        return f"You have packages {descriptions[0]} and {descriptions[1]}. If you want to know more about any package, just ask me."
    return f"You have packages {', '.join(descriptions[:-1])}, and {descriptions[-1]}. If you want to know more about any package, just ask me."

def get_packages_for_user(unit: str) -> List[Dict]:
    try:
        resident = db.get_resident_by_unit(unit)
        if not resident:
            return []
        return db.get_packages_for_resident(resident['id'])
    except Exception as e:
        logger.error(f"get_packages_for_user error: {e}")
        return []

class PackageMatcher:
    @staticmethod
    def match(packages, query):
        if not packages or not query:
            return None
        try:
            query = query.lower().strip()
            query_clean = ''.join(c for c in query if c.isalnum())

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
    tracking_value = get_slot_value(handler_input, 'tracking')
    if tracking_value:
        found = PackageMatcher.match(packages, tracking_value)
        return ("resolved", found) if found else ("not_found", None)

    carrier_value = get_slot_value(handler_input, 'carrier')
    if carrier_value:
        found = PackageMatcher.match(packages, carrier_value)
        return ("resolved", found) if found else ("not_found", None)

    return ("no_selector", None)

def get_user_configuration(unit: str) -> Optional[Dict]:
    try:
        resident = db.get_resident_by_unit(unit)
        if resident:
            return {
                "unit": resident.get('unit'),
                "opted_alexa": resident.get('opted_in', False),
                "alexa_user_id": resident.get('alexa_user_id')
            }
        return None
    except Exception as e:
        logger.error(f"get_user_configuration error: {e}")
        return None

# ============================================
# WEBHOOK HANDLER
# ============================================

def handle_package_event(event: Dict, context: Any) -> Dict:
    logger.info(f"📦 Webhook event received")
    data = event.get('data', {})
    
    account_id = data.get('account_id')
    unit = data.get('unit')
    package_id = data.get('package_id')
    carrier = data.get('carrier', 'courier')
    tracking_number = data.get('tracking_number')
    compartment = data.get('compartment')
    delivered_at_raw = data.get('delivered_at')
    description = data.get('description')
    
    # ✅ Convert datetime to MySQL format
    delivered_at = parse_iso_to_mysql_datetime(delivered_at_raw)
    
    logger.info(f"🔍 Parsed: account_id={account_id}, tracking={tracking_number}, delivered_at={delivered_at}")

    if not account_id or not tracking_number:
        return {"status": "error", "message": "Missing account_id or tracking_number"}

    # Get resident
    resident = None
    if account_id:
        resident = db.get_resident_by_account_id(account_id)
    if not resident and unit:
        resident = db.get_resident_by_unit(unit)
    if not resident:
        return {"status": "error", "message": f"No resident found"}

    alexa_user_id = resident.get('alexa_user_id')
    if not alexa_user_id:
        return {"status": "skipped", "reason": "No Alexa User ID linked"}

    # ✅ Save/Update package
    package_row, is_new = db.save_or_update_package(
        resident_id=resident['id'],
        tracking_number=tracking_number,
        carrier=carrier,
        package_id=package_id,
        compartment=compartment,
        delivered_at=delivered_at,
        unit=unit,
        description=description
    )
    
    if not package_row:
        return {"status": "error", "message": "Failed to save package"}

    logger.info(f"📦 Package saved: id={package_row.get('id')}, is_new={is_new}")

    # ✅ Send notification if new or reminder
    should_notify = is_new
    if not should_notify:
        waiting_days = calculate_waiting_days(delivered_at)
        reminder_days = [3, 5, 7, 10, 14, 21, 30]
        if waiting_days in reminder_days:
            # Check if already notified today
            last_notified = package_row.get('last_notified_at')
            if not last_notified:
                should_notify = True
            else:
                try:
                    if isinstance(last_notified, str):
                        last_dt = datetime.strptime(last_notified, '%Y-%m-%d %H:%M:%S')
                    else:
                        last_dt = last_notified
                    if last_dt.date() != datetime.now().date():
                        should_notify = True
                except Exception:
                    should_notify = True

    if should_notify:
        result = alexa_client.send_notification(
            alexa_user_id, carrier, package_id, tracking_number,
            compartment, delivered_at_raw, unit
        )
        if result.get('status') == 'success':
            db.mark_package_notified(package_row['id'])
            db.log_notification(package_row['id'], "sent", "Success")
            return {"status": "success", "package_id": package_id, "message": "Notification sent"}
        else:
            # ✅ Log failure
            db.log_notification(package_row['id'], "failed", result.get('message'))
            return {"status": "error", "package_id": package_id, "error": result.get('message')}

    logger.info(f"ℹ️ Package {tracking_number} already notified today, skipping")
    return {"status": "no_action", "reason": "already_notified_today"}

# ============================================
# INTENT HANDLERS
# ============================================

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        global CURRENT_USER_ID, CURRENT_UNIT
        CURRENT_USER_ID = handler_input.request_envelope.context.system.user.user_id
        
        resident = db.get_resident_by_alexa_id(CURRENT_USER_ID)
        if resident:
            CURRENT_UNIT = resident.get('unit', "4B")
        else:
            CURRENT_UNIT = USER_ID_TO_UNIT.get(CURRENT_USER_ID, "4B")
        
        logger.info(f"🚀 User {CURRENT_USER_ID[:20]}... mapped to unit {CURRENT_UNIT}")

        session_attr = handler_input.attributes_manager.session_attributes
        session_attr.pop('current_package', None)

        packages = get_packages_for_user(CURRENT_UNIT)
        speak_output = get_package_summary(packages) if packages else "Welcome to Notifii Alert. You have no packages at the moment."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response

class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        packages = get_packages_for_user(CURRENT_UNIT)
        if not packages:
            return handler_input.response_builder.speak("You have no packages right now.").response
        speak_output = get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Would you like details about any package?").response

class PackageDetailsIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageDetailsIntent")(handler_input)

    def handle(self, handler_input):
        packages = get_packages_for_user(CURRENT_UNIT)
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        tracking_value = get_slot_value(handler_input, 'tracking')
        
        # ✅ If user said "more information" without specific tracking
        if not tracking_value:
            # If only one package, give its details
            if len(packages) == 1:
                session_attr['current_package'] = packages[0]
                speak_output = format_package_details(packages[0])
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
            
            # Multiple packages - ask which one
            speak_output = "Which package would you like more information about? " + get_package_summary(packages)
            return handler_input.response_builder.speak(speak_output).ask("Please specify the tracking number or carrier.").response
        
        # ✅ Find the specific package
        found = PackageMatcher.match(packages, tracking_value)
        if found:
            session_attr['current_package'] = found
            speak_output = format_package_details(found)
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
        
        speak_output = "I couldn't find a package matching that. " + get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response

class CompartmentInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CompartmentInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = get_packages_for_user(CURRENT_UNIT)
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        tracking_value = get_slot_value(handler_input, 'tracking')
        
        # ✅ If no tracking specified, use the most recent package
        if not tracking_value:
            found = packages[0] if packages else None
        else:
            found = PackageMatcher.match(packages, tracking_value)
        
        if found:
            compartment = found.get('compartment', 'unknown compartment')
            carrier = found.get('carrier', 'unknown carrier')
            speak_output = f"The {carrier} package is stored in compartment {compartment}."
            session_attr['current_package'] = found
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
        
        speak_output = "I couldn't find that package. " + get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Which package would you like to know about?").response

class DeliveryInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("DeliveryInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = get_packages_for_user(CURRENT_UNIT)
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        tracking_value = get_slot_value(handler_input, 'tracking')
        
        if not tracking_value:
            found = packages[0] if packages else None
        else:
            found = PackageMatcher.match(packages, tracking_value)
        
        if found:
            delivered_at = found.get('delivered_at')
            if delivered_at:
                try:
                    if isinstance(delivered_at, str):
                        dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S')
                    else:
                        dt = delivered_at
                    # ✅ Convert UTC to local timezone (IST)
                    delivered_str = dt.strftime('%B %d, %Y at %I:%M %p IST')
                    waiting_days = calculate_waiting_days(delivered_at)
                    if waiting_days == 0:
                        time_str = "today"
                    elif waiting_days == 1:
                        time_str = "yesterday"
                    else:
                        time_str = f"{waiting_days} days ago"
                    speak_output = f"The {found.get('carrier', 'unknown carrier')} package was delivered on {delivered_str} ({time_str})."
                except Exception as e:
                    logger.error(f"Date parsing error: {e}")
                    speak_output = f"The package was delivered on {delivered_at}"
            else:
                speak_output = "I don't have a delivery date for that package."
            session_attr['current_package'] = found
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
        
        speak_output = "I couldn't find that package. " + get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Which package would you like to know about?").response

class WhichPackageIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("WhichPackageIntent")(handler_input)

    def handle(self, handler_input):
        packages = get_packages_for_user(CURRENT_UNIT)
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
        packages = get_packages_for_user(CURRENT_UNIT)
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
        packages = get_packages_for_user(CURRENT_UNIT)
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
        packages = get_packages_for_user(CURRENT_UNIT)
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
        packages = get_packages_for_user(CURRENT_UNIT)
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
                if isinstance(delivered_at, str):
                    dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S')
                else:
                    dt = delivered_at
                delivered_str = dt.strftime('%B %d, %Y at %I:%M %p')
            except Exception:
                delivered_str = str(delivered_at)
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
        packages = get_packages_for_user(CURRENT_UNIT)
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

# ============================================
# SKILL BUILDER REGISTRATION
# ============================================
sb = SkillBuilder()
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