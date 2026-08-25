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

class Config:
    ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '').strip()
    ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '').strip()
    ALEXA_API_URL = os.environ.get('ALEXA_API_URL', '').strip()

config = Config()
SESSION_GAP_THRESHOLD_DAYS = 2


def parse_iso_to_mysql_datetime(iso_string: Optional[str]) -> Optional[str]:
    if not iso_string:
        return None
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Failed to parse datetime '{iso_string}': {e}")
        return None

def calculate_waiting_days(delivered_at: Optional[str]) -> int:
    """FIXED: compares calendar dates, not raw elapsed hours, so 'yesterday' never shows as 'today'."""
    if not delivered_at:
        return 0
    try:
        dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S') if isinstance(delivered_at, str) else delivered_at
        days = (datetime.now().date() - dt.date()).days
        return days if days > 0 else 0
    except Exception as e:
        logger.error(f"calculate_waiting_days error: {e}")
        return 0


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


def get_slot_value(handler_input, slot_name: str) -> Optional[str]:
    try:
        intent = handler_input.request_envelope.request.intent
        if not intent or not intent.slots:
            return None
        slot = intent.slots.get(slot_name)
        if not slot:
            return None
        value = slot.value
        if not value:
            if slot.resolutions and slot.resolutions.resolutions_per_authority:
                for resolution in slot.resolutions.resolutions_per_authority:
                    if resolution.values and resolution.values[0].value:
                        value = resolution.values[0].value.name
                        break
        logger.info(f"🔍 Slot '{slot_name}' value: {value}")
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
            dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S') if isinstance(delivered_at, str) else delivered_at
            delivered_str = dt.strftime('%B %d, %Y at %I:%M %p')
            waiting_days = calculate_waiting_days(delivered_at)
            time_str = "today" if waiting_days == 0 else ("yesterday" if waiting_days == 1 else f"{waiting_days} days ago")
        except Exception:
            delivered_str = str(delivered_at)
            time_str = ""
    else:
        delivered_str = 'recently'
        time_str = ""

    return f"Package from {carrier}, tracking number {tracking}, stored in compartment {compartment}, delivered on {delivered_str} ({time_str})."

def format_full_package_details(package: Dict) -> str:
    carrier = package.get('carrier', 'unknown carrier')
    tracking = package.get('tracking_number', 'unknown')
    package_id = package.get('package_id', 'unknown')
    compartment = package.get('compartment', 'unknown')
    delivered_at = package.get('delivered_at')

    parts = [f"Package ID is {package_id}", f"delivered by {carrier}",
             f"with tracking number {tracking}", f"stored in compartment {compartment}"]

    if delivered_at:
        try:
            dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S') if isinstance(delivered_at, str) else delivered_at
            parts.append(f"delivered on {dt.strftime('%B %d, %Y')} at {dt.strftime('%I:%M %p')}")
        except Exception:
            parts.append(f"delivered on {delivered_at}")

    return "Your package: " + ", ".join(parts) + "."

def get_package_summary(packages: List[Dict]) -> str:
    if not packages:
        return "You have no packages right now."
    descriptions = []
    for p in packages:
        carrier = p.get('carrier', 'unknown')
        tracking = p.get('tracking_number', 'unknown')
        waiting_days = calculate_waiting_days(p.get('delivered_at'))
        waiting_str = "arrived today" if waiting_days == 0 else ("arrived 1 day ago" if waiting_days == 1 else f"arrived {waiting_days} days ago")
        descriptions.append(f"from {carrier} with tracking {tracking} ({waiting_str})")

    if len(descriptions) == 1:
        return f"You have a package {descriptions[0]}."
    elif len(descriptions) == 2:
        return f"You have packages {descriptions[0]} and {descriptions[1]}."
    return f"You have packages {', '.join(descriptions[:-1])}, and {descriptions[-1]}."

def get_current_resident(handler_input) -> Optional[Dict]:
    alexa_user_id = handler_input.request_envelope.context.system.user.user_id
    return db.get_resident_by_alexa_id(alexa_user_id)

def get_packages_for_resident_safe(resident: Optional[Dict]) -> List[Dict]:
    if not resident:
        return []
    return db.get_packages_for_resident(resident['id'])

def get_status_report_packages(resident: Optional[Dict]) -> List[Dict]:
    """
    Returns packages that should be announced to the user:
    - Unheard packages (never heard by user)
    - OR packages that have been heard but have new notifications (reminders)
    """
    if not resident:
        return []
    
    resident_id = resident['id']
    
    # Get ALL packages for this resident
    all_packages = db.get_packages_for_resident(resident_id)
    
    # Filter: Only packages that are either:
    # 1. Not heard yet (heard_at IS NULL)
    # 2. Have been notified recently (last_notified_at > heard_at)
    unheard_or_new = []
    
    for p in all_packages:
        heard_at = p.get('heard_at')
        last_notified_at = p.get('last_notified_at')
        
        # If never heard, include it
        if heard_at is None:
            unheard_or_new.append(p)
            continue
        
        # If heard but there's a new notification (reminder) after the last hear time
        if last_notified_at and heard_at:
            try:
                last_notified_dt = datetime.strptime(last_notified_at, '%Y-%m-%d %H:%M:%S') if isinstance(last_notified_at, str) else last_notified_at
                heard_dt = datetime.strptime(heard_at, '%Y-%m-%d %H:%M:%S') if isinstance(heard_at, str) else heard_at
                
                # If there's a new notification after the user heard it, include it
                if last_notified_dt > heard_dt:
                    unheard_or_new.append(p)
            except Exception:
                # If we can't parse dates, include it to be safe
                unheard_or_new.append(p)
    
    # Mark all returned packages as heard (user is about to hear them)
    if unheard_or_new:
        db.mark_packages_heard([p['id'] for p in unheard_or_new])
    
    db.update_last_session(resident_id)
    
    return unheard_or_new

class PackageMatcher:
    @staticmethod
    def match(packages, query):
        if not packages or not query:
            return None
        try:
            query = query.lower().strip()
            query_clean = ''.join(c for c in query if c.isalnum())
            logger.info(f"🔍 PackageMatcher - Query: '{query}', Clean: '{query_clean}'")

            for p in packages:
                tracking = (p.get('tracking_number') or '').lower().strip()
                tracking_clean = ''.join(c for c in tracking if c.isalnum())
                if query == tracking or query_clean == tracking_clean:
                    return p
                if query_clean.isdigit() and query_clean == tracking_clean:
                    return p
                if tracking_clean and (tracking_clean in query_clean or query_clean in tracking_clean):
                    return p
                carrier = (p.get('carrier') or '').lower().strip()
                carrier_clean = ''.join(c for c in carrier if c.isalnum())
                if query == carrier or query_clean == carrier_clean:
                    return p

            if query_clean:
                word_to_number = {
                    'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
                    'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
                    'zero': '0', 'oh': '0', 'eleven': '11', 'twelve': '12'
                }
                words = query.split()
                numeric_query = ''.join(word_to_number.get(w, w) for w in words)
                numeric_query = ''.join(c for c in numeric_query if c.isalnum())
                for p in packages:
                    tracking = (p.get('tracking_number') or '').lower().strip()
                    tracking_clean = ''.join(c for c in tracking if c.isalnum())
                    if numeric_query == tracking_clean:
                        return p

            logger.info(f"❌ No match found for query: '{query}'")
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

    delivered_at = parse_iso_to_mysql_datetime(delivered_at_raw)
    logger.info(f"🔍 Parsed: account_id={account_id}, tracking={tracking_number}, delivered_at={delivered_at}")

    if not account_id or not tracking_number:
        return {"status": "error", "message": "Missing account_id or tracking_number"}

    resident = None
    if account_id:
        resident = db.get_resident_by_account_id(account_id)
    if not resident and unit:
        resident = db.get_resident_by_unit(unit)
    if not resident:
        return {"status": "error", "message": "No resident found"}

    if not resident.get('opted_in', False):
        logger.info(f"Resident {resident.get('id')} not opted in — skipping notification, saving package only")

    alexa_user_id = resident.get('alexa_user_id')

    package_row, is_new = db.save_or_update_package(
        resident_id=resident['id'], tracking_number=tracking_number, carrier=carrier,
        package_id=package_id, compartment=compartment, delivered_at=delivered_at,
        unit=unit, description=description
    )
    if not package_row:
        return {"status": "error", "message": "Failed to save package"}

    logger.info(f"📦 Package saved: id={package_row.get('id')}, is_new={is_new}")

    if not alexa_user_id or not resident.get('opted_in', False):
        db.log_notification(package_row['id'], "skipped", "No Alexa link or not opted in")
        return {"status": "skipped", "reason": "No Alexa User ID linked or not opted in"}

    should_notify = is_new
    if not should_notify:
        waiting_days = calculate_waiting_days(delivered_at)
        reminder_days = [3, 5, 7, 10, 14, 21, 30]
        if waiting_days in reminder_days:
            last_notified = package_row.get('last_notified_at')
            if not last_notified:
                should_notify = True
            else:
                try:
                    last_dt = datetime.strptime(last_notified, '%Y-%m-%d %H:%M:%S') if isinstance(last_notified, str) else last_notified
                    if last_dt.date() != datetime.now().date():
                        should_notify = True
                except Exception:
                    should_notify = True
            if should_notify:
                db.increment_reminder(package_row['id'])

    if should_notify:
        result = alexa_client.send_notification(
            alexa_user_id, carrier, package_id, tracking_number, compartment, delivered_at_raw, unit
        )
        if result.get('status') == 'success':
            db.mark_package_notified(package_row['id'])
            db.log_notification(package_row['id'], "sent", "Success")
            return {"status": "success", "package_id": package_id, "message": "Notification sent"}
        else:
            db.log_notification(package_row['id'], "failed", result.get('message'))
            return {"status": "error", "package_id": package_id, "error": result.get('message')}

    logger.info(f"ℹ️ Package {tracking_number} already notified today, skipping")
    db.log_notification(package_row['id'], "skipped", "already_notified_today")
    return {"status": "no_action", "reason": "already_notified_today"}


class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        resident = get_current_resident(handler_input)
        session_attr = handler_input.attributes_manager.session_attributes
        session_attr.pop('current_package', None)

        packages = get_status_report_packages(resident)

        if packages:
            welcome = "Welcome to Notifii Alert."
            package_list = []
            for p in packages:
                carrier = p.get('carrier', 'unknown carrier')
                tracking = p.get('tracking_number', 'unknown')
                delivered_at = p.get('delivered_at')
                if delivered_at:
                    try:
                        dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S') if isinstance(delivered_at, str) else delivered_at
                        package_list.append(f"from {carrier} with tracking {tracking} delivered on {dt.strftime('%B %d, %Y')} at {dt.strftime('%I:%M %p')}")
                    except Exception:
                        package_list.append(f"from {carrier} with tracking {tracking}")
                else:
                    package_list.append(f"from {carrier} with tracking {tracking}")

            if len(package_list) == 1:
                speak_output = f"{welcome} You have a new package {package_list[0]}. You can ask for more details."
            elif len(package_list) == 2:
                speak_output = f"{welcome} You have new packages {package_list[0]} and {package_list[1]}. You can ask for more details."
            else:
                speak_output = f"{welcome} You have new packages {', '.join(package_list[:-1])}, and {package_list[-1]}. You can ask for more details."
        else:
            speak_output = "Welcome to Notifii Alert. You have no new notifications right now."

        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        resident = get_current_resident(handler_input)
        packages = get_status_report_packages(resident)
        if not packages:
            return handler_input.response_builder.speak("You don't have any new notifications right now.").response
        speak_output = get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Would you like details about any package?").response


class PackageDetailsIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageDetailsIntent")(handler_input)

    def handle(self, handler_input):
        resident = get_current_resident(handler_input)
        packages = get_packages_for_resident_safe(resident)
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        tracking_value = get_slot_value(handler_input, 'tracking')
        logger.info(f"🔍 PackageDetailsIntent - tracking_value: '{tracking_value}'")

        if tracking_value:
            found = PackageMatcher.match(packages, tracking_value)
            if found:
                session_attr['current_package'] = found
                speak_output = format_full_package_details(found)
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
            else:
                speak_output = f"I couldn't find a package matching '{tracking_value}'. " + get_package_summary(packages)
                return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response

        if len(packages) == 1:
            found = packages[0]
            session_attr['current_package'] = found
            speak_output = format_full_package_details(found)
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response

        speak_output = get_package_summary(packages) + " Which package would you like more details about? You can say the tracking number or carrier name."
        return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response


class DeliveryInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("DeliveryInquiryIntent")(handler_input)

    def handle(self, handler_input):
        resident = get_current_resident(handler_input)
        packages = get_packages_for_resident_safe(resident)
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        tracking_value = get_slot_value(handler_input, 'tracking')

        def speak_delivery(found):
            delivered_at = found.get('delivered_at')
            carrier = found.get('carrier', 'unknown carrier')
            if delivered_at:
                try:
                    dt = datetime.strptime(delivered_at, '%Y-%m-%d %H:%M:%S') if isinstance(delivered_at, str) else delivered_at
                    return f"The {carrier} package was delivered on {dt.strftime('%B %d, %Y')} at {dt.strftime('%I:%M %p')}."
                except Exception:
                    return f"The {carrier} package was delivered on {delivered_at}"
            return f"I don't have a delivery date for the {carrier} package."

        if tracking_value:
            found = PackageMatcher.match(packages, tracking_value)
            if found:
                session_attr['current_package'] = found
                return handler_input.response_builder.speak(speak_delivery(found)).ask("Would you like to know anything else?").response
            speak_output = f"I couldn't find a package with tracking number {tracking_value}. " + get_package_summary(packages)
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like to know about?").response

        if len(packages) == 1:
            found = packages[0]
            session_attr['current_package'] = found
            return handler_input.response_builder.speak(speak_delivery(found)).ask("Would you like to know anything else?").response

        speak_output = "You have multiple packages. Which one would you like the delivery date for? " + get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Please specify the tracking number or carrier.").response


class WhichPackageIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("WhichPackageIntent")(handler_input)

    def handle(self, handler_input):
        resident = get_current_resident(handler_input)
        packages = get_packages_for_resident_safe(resident)
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
        resident = get_current_resident(handler_input)
        packages = get_packages_for_resident_safe(resident)
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        carrier_value = get_slot_value(handler_input, 'carrier')

        if carrier_value:
            found = PackageMatcher.match(packages, carrier_value)
            if found:
                carrier = found.get('carrier', 'unknown carrier')
                speak_output = f"That package was delivered by {carrier}."
                session_attr['current_package'] = found
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
            speak_output = f"I couldn't find a package matching '{carrier_value}'. " + get_package_summary(packages)
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like to know about?").response

        if len(packages) == 1:
            found = packages[0]
            carrier = found.get('carrier', 'unknown carrier')
            speak_output = f"This package was delivered by {carrier}."
            session_attr['current_package'] = found
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response

        speak_output = "You have multiple packages. Which one would you like the carrier for? " + get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Please specify the tracking number or carrier.").response


class TrackingInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("TrackingInquiryIntent")(handler_input)

    def handle(self, handler_input):
        resident = get_current_resident(handler_input)
        packages = get_packages_for_resident_safe(resident)
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        tracking_value = get_slot_value(handler_input, 'tracking')

        if tracking_value:
            found = PackageMatcher.match(packages, tracking_value)
            if found:
                tracking = found.get('tracking_number', 'unknown')
                carrier = found.get('carrier', 'unknown carrier')
                speak_output = f"The tracking number for the {carrier} package is {tracking}."
                session_attr['current_package'] = found
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
            speak_output = f"I couldn't find a package with tracking number {tracking_value}. " + get_package_summary(packages)
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like to know about?").response

        if len(packages) == 1:
            found = packages[0]
            tracking = found.get('tracking_number', 'unknown')
            carrier = found.get('carrier', 'unknown carrier')
            speak_output = f"The tracking number for the {carrier} package is {tracking}."
            session_attr['current_package'] = found
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response

        speak_output = "You have multiple packages. Which one would you like the tracking number for? " + get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Please specify the tracking number or carrier.").response


class CompartmentInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CompartmentInquiryIntent")(handler_input)

    def handle(self, handler_input):
        resident = get_current_resident(handler_input)
        packages = get_packages_for_resident_safe(resident)
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response

        session_attr = handler_input.attributes_manager.session_attributes
        tracking_value = get_slot_value(handler_input, 'tracking')

        if tracking_value:
            found = PackageMatcher.match(packages, tracking_value)
            if found:
                compartment = found.get('compartment', 'unknown compartment')
                carrier = found.get('carrier', 'unknown carrier')
                speak_output = f"The {carrier} package is stored in compartment {compartment}."
                session_attr['current_package'] = found
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
            speak_output = f"I couldn't find a package with tracking number {tracking_value}. " + get_package_summary(packages)
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like to know about?").response

        if len(packages) == 1:
            found = packages[0]
            compartment = found.get('compartment', 'unknown compartment')
            carrier = found.get('carrier', 'unknown carrier')
            speak_output = f"The {carrier} package is stored in compartment {compartment}."
            session_attr['current_package'] = found
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response

        speak_output = "You have multiple packages. Which one would you like the location for? " + get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Please specify the tracking number or carrier.").response


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
        resident = get_current_resident(handler_input)
        packages = get_packages_for_resident_safe(resident)
        if not packages:
            return handler_input.response_builder.speak("You have no packages right now. Would you like to know anything else?").ask("How can I help you?").response
        speak_output = get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("What would you like to know?").response


class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True
    def handle(self, handler_input, exception):
        logger.error(f"Error handling request: {exception}", exc_info=True)
        return handler_input.response_builder.speak("Sorry, I had trouble processing your request. Please try again.").ask("What would you like to know?").response


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