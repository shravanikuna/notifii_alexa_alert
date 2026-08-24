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
                logger.info(f"✅ Token obtained (expires in {expires_in}s)")
                return self._cached_token
            logger.error(f"❌ Token failed: {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"❌ Token fetch error: {str(e)}")
            return None

    def send_notification(self, alexa_user_id, carrier, package_id, tracking_number=None,
                          compartment=None, delivered_at=None, account_id=None) -> Dict:
        token = self.get_token()
        if not token:
            return {"status": "error", "message": "Failed to obtain token"}
        
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
                        "delivery": {"compartment": compartment or "unknown"}
                    }
                }
            },
            "localizedAttributes": [{"locale": "en-US", "sellerName": carrier}],
            "relevantAudience": {"type": "Unicast", "payload": {"user": alexa_user_id}}
        }
        
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        logger.info(f"📤 Sending notification to Alexa")
        
        try:
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 202:
                logger.info(f"✅ Notification sent to {alexa_user_id}")
                return {"status": "success", "code": 202}
            logger.error(f"❌ API error: {response.status_code} - {response.text}")
            return {"status": "error", "code": response.status_code, "message": response.text}
        except Exception as e:
            logger.error(f"❌ Send error: {str(e)}")
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
    except Exception:
        return None

def calculate_waiting_days(delivered_at: Optional[str]) -> int:
    if not delivered_at:
        return 0
    try:
        dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
        now = datetime.now(dt.tzinfo)
        return (now - dt).days
    except Exception:
        return 0

def get_package_summary(packages: List[Dict]) -> str:
    if not packages:
        return "You have no packages right now."
    
    if len(packages) == 1:
        p = packages[0]
        return f"You have a package from {p.get('carrier', 'unknown carrier')} with tracking number {p.get('tracking_number', 'unknown')}."
    
    descriptions = [f"from {p.get('carrier', 'unknown')} with tracking number {p.get('tracking_number', 'unknown')}" for p in packages]
    if len(descriptions) == 2:
        return f"You have packages {descriptions[0]} and {descriptions[1]}."
    return f"You have packages {', '.join(descriptions[:-1])}, and {descriptions[-1]}."

def format_package_details(package: Dict) -> str:
    carrier = package.get('carrier', 'unknown carrier')
    tracking = package.get('tracking_number', 'unknown')
    compartment = package.get('compartment', 'unknown')
    delivered_at = package.get('delivered_at')
    
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
    
    return f"Package from {carrier}, tracking number {tracking}, in compartment {compartment}, delivered on {delivered_str}"

# ============================================
# WEBHOOK HANDLER
# ============================================

def handle_package_event(event: Dict, context: Any) -> Dict:
    logger.info(f"📦 Webhook received")
    
    data = event.get('data', {})
    account_id = data.get('account_id')
    package_id = data.get('package_id')
    tracking_number = data.get('tracking_number')
    carrier = data.get('carrier', 'courier')
    compartment = data.get('compartment')
    delivered_at_raw = data.get('delivered_at')
    delivered_at = parse_iso_to_mysql_datetime(delivered_at_raw)
    
    logger.info(f"🔍 account_id={account_id}, tracking={tracking_number}, carrier={carrier}")
    
    if not account_id or not tracking_number:
        return {"status": "error", "message": "Missing account_id or tracking_number"}
    
    # Get resident by account_id
    resident = db.get_resident_by_account_id(account_id)
    if not resident:
        return {"status": "error", "message": f"No resident found for account_id: {account_id}"}
    
    alexa_user_id = resident.get('alexa_user_id')
    resident_id = resident['id']
    
    # Save or update package by tracking_number
    package_row, is_new = db.save_or_update_package(
        resident_id=resident_id,
        tracking_number=tracking_number,
        carrier=carrier,
        package_id=package_id,
        compartment=compartment,
        delivered_at=delivered_at
    )
    
    if not package_row:
        return {"status": "error", "message": "Failed to save package"}
    
    # If no Alexa linked, log and skip
    if not alexa_user_id:
        db.log_notification(package_row['id'], "failed", "No Alexa account linked")
        return {"status": "skipped", "reason": "no_alexa_link"}
    
    # If new package, send notification
    if is_new:
        result = alexa_client.send_notification(
            alexa_user_id, carrier, package_id, tracking_number,
            compartment, delivered_at_raw, account_id
        )
        if result.get('status') == 'success':
            db.mark_package_notified(package_row['id'])
            db.log_notification(package_row['id'], "sent", "Initial delivery notification")
            return {"status": "success", "message": "New package notification sent", "tracking": tracking_number}
        db.log_notification(package_row['id'], "failed", result.get('message'))
        return {"status": "error", "message": result.get('message')}
    
    # Existing package - check reminder days
    days_waiting = calculate_waiting_days(delivered_at_raw)
    reminder_thresholds = [3, 5, 7]
    current_reminder_count = package_row.get('reminder_count', 0)
    
    if days_waiting in reminder_thresholds and current_reminder_count < reminder_thresholds.index(days_waiting) + 1:
        result = alexa_client.send_notification(
            alexa_user_id, carrier, package_id, tracking_number,
            compartment, delivered_at_raw, account_id
        )
        if result.get('status') == 'success':
            db.increment_reminder(package_row['id'])
            db.log_notification(package_row['id'], "sent", f"Day {days_waiting} reminder")
            return {"status": "success", "message": f"Day {days_waiting} reminder sent", "tracking": tracking_number}
        db.log_notification(package_row['id'], "failed", result.get('message'))
        return {"status": "error", "message": result.get('message')}
    
    logger.info(f"ℹ️ Package {tracking_number} already notified, no reminder needed")
    return {"status": "no_action", "reason": "already_notified"}

# ============================================
# INTENT HANDLERS
# ============================================

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        alexa_user_id = handler_input.request_envelope.context.system.user.user_id
        logger.info(f"🚀 Launch by user: {alexa_user_id[:30]}...")
        
        resident = db.get_resident_by_alexa_id(alexa_user_id)
        
        if not resident:
            speak_output = "Welcome to Notifii Alert. Your account is not linked yet. Please enable notifications in the Notifii app."
            return handler_input.response_builder.speak(speak_output).response
        
        resident_id = resident['id']
        packages = db.get_packages_for_resident(resident_id)
        
        if packages:
            speak_output = "Welcome to Notifii Alert. " + get_package_summary(packages)
        else:
            speak_output = "Welcome to Notifii Alert. You have no packages right now."
        
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response

class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        alexa_user_id = handler_input.request_envelope.context.system.user.user_id
        resident = db.get_resident_by_alexa_id(alexa_user_id)
        
        if not resident:
            return handler_input.response_builder.speak("Please link your account in the Notifii app first.").response
        
        packages = db.get_packages_for_resident(resident['id'])
        speak_output = get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Would you like details about any package?").response

class CompartmentInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CompartmentInquiryIntent")(handler_input)

    def handle(self, handler_input):
        alexa_user_id = handler_input.request_envelope.context.system.user.user_id
        resident = db.get_resident_by_alexa_id(alexa_user_id)
        
        if not resident:
            return handler_input.response_builder.speak("Please link your account first.").response
        
        packages = db.get_packages_for_resident(resident['id'])
        if not packages:
            return handler_input.response_builder.speak("You have no packages.").response
        
        latest = packages[0]
        speak_output = f"Your package is in compartment {latest.get('compartment', 'unknown')}."
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response

class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)
    
    def handle(self, handler_input):
        speak_output = "You can ask about your packages, compartment location, or package details."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response

class CancelAndStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or \
               ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input)
    
    def handle(self, handler_input):
        return handler_input.response_builder.speak("Goodbye!").set_should_end_session(True).response

class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)
    
    def handle(self, handler_input):
        return handler_input.response_builder.speak("I didn't understand. You can ask about your packages.").ask("How can I help?").response

# ============================================
# SKILL BUILDER
# ============================================

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(CompartmentInquiryIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())

lambda_handler = sb.lambda_handler()