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
from ask_sdk_model.ui import SimpleCard

# ============================================
# LOGGING
# ============================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================
# CONFIGURATION - Environment Variables
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
CONVERSATION_STATE = {
    'ASKING_WHICH_PACKAGE': 'asking_which_package',
    'SHOWING_DETAILS': 'showing_details',
    'IDLE': 'idle'
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
                    "state": {
                        "status": "ORDER_DELIVERED",
                        "deliveredOn": delivered_at or now
                    },
                    "order": {
                        "seller": {
                            "name": "localizedattribute:sellerName"
                        },
                        "orderId": package_id,
                        "trackingNumber": tracking_number,
                        "delivery": {
                            "compartment": compartment or "unknown",
                            "unit": unit or "unknown"
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
    active_user_id = os.environ.get('ALEXA_USER_ID', '').strip()
    configs = {
        "4B": {"unit": "4B", "opted_alexa": True, "alexa_user_id": active_user_id},
        "2A": {"unit": "2A", "opted_alexa": True, "alexa_user_id": active_user_id}
    }
    return configs.get(unit)

# ============================================
# HELPER FUNCTIONS
# ============================================

def get_slot_value(handler_input, slot_name: str) -> Optional[str]:
    try:
        slots = handler_input.request_envelope.request.intent.slots
        if not slots or slot_name not in slots:
            return None
        slot = slots[slot_name]
        value = slot.value
        return str(value).strip() if value else None
    except Exception as e:
        logger.error(f"get_slot_value error for '{slot_name}': {e}")
        return None

def format_all_package_details(packages: List[Dict]) -> str:
    """Full details for every package — used when no selector given and multiple exist."""
    parts = [format_package_details(p) for p in packages]
    return " Also, ".join(parts)

def format_package_details(package: Dict) -> str:
    """Format full package details"""
    carrier = package.get('carrier', 'unknown carrier')
    tracking = package.get('tracking_number', 'no tracking number')
    compartment = package.get('compartment', 'unknown compartment')
    delivered_at = package.get('delivered_at', 'recently')
    
    # Format the delivered date nicely
    if delivered_at and delivered_at != 'recently':
        try:
            dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
            delivered_str = dt.strftime('%B %d, %Y at %I:%M %p')
        except:
            delivered_str = delivered_at
    else:
        delivered_str = 'recently'
    
    return f"Package from {carrier}, tracking number {tracking}, stored in compartment {compartment}, delivered on {delivered_str}"

def get_package_summary(packages: List[Dict]) -> str:
    """Get a summary of all packages with tracking numbers"""
    if not packages:
        return "You have no packages right now."
    
    total = len(packages)
    
    # Build package descriptions
    package_descriptions = []
    for p in packages:
        carrier = p.get('carrier', 'unknown')
        tracking = p.get('tracking_number', 'no tracking number')
        package_descriptions.append(f"from {carrier} with tracking number {tracking}")
    
    if total == 1:
        return f"You have a package {package_descriptions[0]}. If you want to know more about this package, just ask me."
    elif total == 2:
        return f"You have {package_descriptions[0]} and {package_descriptions[1]}. If you want to know more about any package, just ask me."
    else:
        package_str = ", ".join(package_descriptions[:-1]) + f", and {package_descriptions[-1]}"
        return f"You have {package_str}. If you want to know more about any package, just ask me."

def format_available_packages(packages, query=None):
    """Format available packages with tracking numbers"""
    package_list = []
    for p in packages:
        carrier = p.get('carrier', 'unknown')
        tracking = p.get('tracking_number', 'no tracking number')
        package_list.append(f"{carrier} (tracking: {tracking})")
    
    if query:
        prefix = f"I couldn't find a package matching '{query}'. "
    else:
        prefix = ""
    
    if len(package_list) == 1:
        return f"{prefix}You have one package: {package_list[0]}. Would you like details about it?"
    elif len(package_list) == 2:
        return f"{prefix}You have packages: {package_list[0]} and {package_list[1]}. Which one would you like details about?"
    else:
        package_str = ", ".join(package_list[:-1]) + f", and {package_list[-1]}"
        return f"{prefix}You have packages: {package_str}. Which one would you like details about?"

def find_package_by_carrier(packages: List[Dict], carrier_query: str) -> Optional[Dict]:
    """Find a package by carrier name (case-insensitive)"""
    carrier_query = carrier_query.lower()
    for p in packages:
        if carrier_query in p.get('carrier', '').lower():
            return p
    return None

def find_package_by_tracking(packages: List[Dict], tracking_query: str) -> Optional[Dict]:
    """Find a package by tracking number (partial match)"""
    tracking_query = tracking_query.lower()
    for p in packages:
        if tracking_query in p.get('tracking_number', '').lower():
            return p
    return None

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
    
    # Store full package details
    package_info = {
        "package_id": package_id,
        "carrier": carrier,
        "tracking_number": tracking_number,
        "compartment": compartment,
        "delivered_at": delivered_at,
        "unit": unit
    }
    
    LATEST_PACKAGES.setdefault(unit, []).append(package_info)
    logger.info(f"📦 Added package. Total packages for unit {unit}: {len(LATEST_PACKAGES[unit])}")

    debug_info = {
        "active_alexa_user_id_present": bool(alexa_user_id),
        "active_alexa_user_id_length": len(alexa_user_id) if alexa_user_id else 0,
        "active_alexa_user_id_preview": alexa_user_id[:15] + "..." if alexa_user_id else None,
    }

    if not alexa_user_id:
        return {"status": "skipped", "reason": "No Alexa User ID linked", "debug": debug_info}

    logger.info(f"RAW USERID REPR: {repr(alexa_user_id)}")

    # Send notification with all package details
    result = alexa_client.send_notification(
        alexa_user_id, 
        carrier, 
        package_id,
        tracking_number,
        compartment,
        delivered_at,
        unit
    )

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


class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        global LATEST_ALEXA_USER_ID, CURRENT_UNIT
        LATEST_ALEXA_USER_ID = handler_input.request_envelope.context.system.user.user_id
        CURRENT_UNIT = USER_ID_TO_UNIT.get(LATEST_ALEXA_USER_ID, "4B")
        logger.info(f"🚀 User {LATEST_ALEXA_USER_ID[:20]}... mapped to unit {CURRENT_UNIT}")

        # Reset conversation state
        session_attr = handler_input.attributes_manager.session_attributes
        session_attr['conversation_state'] = CONVERSATION_STATE['IDLE']

        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if packages:
            speak_output = get_package_summary(packages)
        else:
            speak_output = "Welcome to Notifii Alert. Your account is connected for package updates. You have no packages at the moment."

        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response

class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages right now."
            return handler_input.response_builder.speak(speak_output).response
        
        speak_output = get_package_summary(packages)
        return handler_input.response_builder.speak(speak_output).ask("Would you like details about any package?").response


class PackageDetailsIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageDetailsIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to get details about."
            return handler_input.response_builder.speak(speak_output).response

        session_attr = handler_input.attributes_manager.session_attributes

        # 1. Tracking number — most specific, always wins
        tracking_value = get_slot_value(handler_input, 'tracking')
        if tracking_value:
            found_package = PackageMatcher.match(packages, tracking_value)
            if found_package:
                speak_output = format_package_details(found_package)
                session_attr['current_package'] = found_package
                session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response
            else:
                speak_output = f"I couldn't find a package with tracking number {tracking_value}."
                return handler_input.response_builder.speak(speak_output).ask("Would you like to hear about all your packages instead?").response

        # 2. Carrier
        carrier_value = get_slot_value(handler_input, 'carrier')
        if carrier_value:
            found_package = PackageMatcher.match(packages, carrier_value.lower())
            if found_package:
                speak_output = format_package_details(found_package)
                session_attr['current_package'] = found_package
                session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response

        # 3. No selector given — single package: just give it
        if len(packages) == 1:
            found_package = packages[0]
            speak_output = format_package_details(found_package)
            session_attr['current_package'] = found_package
            session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response

        # 4. No selector, multiple packages — give ALL details (per spec)
        speak_output = format_all_package_details(packages)
        session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response

def format_available_packages(packages, query=None):
    """Format available packages with tracking numbers"""
    package_list = []
    for p in packages:
        carrier = p.get('carrier', 'unknown')
        tracking = p.get('tracking_number', 'no tracking number')
        package_list.append(f"{carrier} (tracking: {tracking})")
    
    if query:
        prefix = f"I couldn't find a package matching '{query}'. "
    else:
        prefix = ""
    
    if len(package_list) == 1:
        return f"{prefix}You have one package: {package_list[0]}. Would you like details about it?"
    elif len(package_list) == 2:
        return f"{prefix}You have packages: {package_list[0]} and {package_list[1]}. Which one would you like details about?"
    else:
        package_str = ", ".join(package_list[:-1]) + f", and {package_list[-1]}"
        return f"{prefix}You have packages: {package_str}. Which one would you like details about?"

# Add this to PackageMatcher class
@staticmethod
def format_available_packages(packages, query=None):
    """Format available packages with tracking numbers"""
    package_list = []
    for p in packages:
        carrier = p.get('carrier', 'unknown')
        tracking = p.get('tracking_number', 'no tracking number')
        package_list.append(f"{carrier} (tracking: {tracking})")
    
    if query:
        prefix = f"I couldn't find a package matching '{query}'. "
    else:
        prefix = ""
    
    if len(package_list) == 1:
        return f"{prefix}You have one package: {package_list[0]}. Would you like details about it?"
    elif len(package_list) == 2:
        return f"{prefix}You have packages: {package_list[0]} and {package_list[1]}. Which one would you like details about?"
    else:
        package_str = ", ".join(package_list[:-1]) + f", and {package_list[-1]}"
        return f"{prefix}You have packages: {package_str}. Which one would you like details about?"
class WhichPackageIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("WhichPackageIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages."
            return handler_input.response_builder.speak(speak_output).response
        
        # Get the carrier from the user's response
        carrier_value = None
        try:
            carrier_value = get_slot_value(handler_input, 'carrier')
            if carrier_value:
                carrier_value = carrier_value.lower()
                logger.info(f"WhichPackageIntent - Carrier: {carrier_value}")
        except Exception as e:
            logger.error(f"Error in WhichPackageIntent: {e}")
        
        if not carrier_value:
            speak_output = format_available_packages(packages)
            session_attr = handler_input.attributes_manager.session_attributes
            session_attr['conversation_state'] = CONVERSATION_STATE['ASKING_WHICH_PACKAGE']
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response
        
        # Find matching package
        found_package = PackageMatcher.match(packages, carrier_value)
        
        if found_package:
            speak_output = format_package_details(found_package)
            session_attr = handler_input.attributes_manager.session_attributes
            session_attr['current_package'] = found_package
            session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
            return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response
        else:
            speak_output = format_available_packages(packages, carrier_value)
            session_attr = handler_input.attributes_manager.session_attributes
            session_attr['conversation_state'] = CONVERSATION_STATE['ASKING_WHICH_PACKAGE']
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response
    
class CarrierInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CarrierInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        session_attr = handler_input.attributes_manager.session_attributes
        
        # Check tracking first
        tracking_value = get_slot_value(handler_input, 'tracking')
        if tracking_value:
            matched = PackageMatcher.match(packages, tracking_value)
            if matched:
                found_package = matched
        
        # Get from session or latest
        session_attr = handler_input.attributes_manager.session_attributes
        package = session_attr.get('current_package', packages[-1])
        
        carrier = package.get('carrier', 'unknown carrier')
        speak_output = f"This package was delivered by {carrier}."
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class TrackingInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("TrackingInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        session_attr = handler_input.attributes_manager.session_attributes
        
        # Check tracking first
        tracking_value = get_slot_value(handler_input, 'tracking')
        if tracking_value:
            matched = PackageMatcher.match(packages, tracking_value)
            if matched:
                found_package = matched
        
        # Try to get carrier from slots
        carrier_value = None
        try:
                  carrier_value = get_slot_value(handler_input, 'carrier')
                  if carrier_value:
                   carrier_value = carrier_value.lower()
        except:
            pass
        
        # Find package
        found_package = None
        if carrier_value:
            found_package = PackageMatcher.match(packages, carrier_value)
        
        if not found_package:
            found_package = session_attr.get('current_package', packages[-1])
        
        tracking = found_package.get('tracking_number', 'no tracking number available')
        carrier = found_package.get('carrier', '')
        
        speak_output = f"The tracking number for the {carrier} package is {tracking}."
        session_attr['current_package'] = found_package
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response

class CompartmentInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CompartmentInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response

        session_attr = handler_input.attributes_manager.session_attributes
        found_package = None

        tracking_value = get_slot_value(handler_input, 'tracking')
        if tracking_value:
            found_package = PackageMatcher.match(packages, tracking_value)

        if not found_package:
            carrier_value = get_slot_value(handler_input, 'carrier')
            if carrier_value:
                found_package = PackageMatcher.match(packages, carrier_value.lower())

        if not found_package:
            found_package = session_attr.get('current_package', packages[-1])

        compartment = found_package.get('compartment', 'unknown compartment')
        speak_output = f"This package is stored in compartment {compartment}."
        session_attr['current_package'] = found_package

        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
class PackageMatcher:
    """Handles matching packages without hardcoding carriers"""
    
    @staticmethod
    def match(packages, query):
        """Main matching method"""
        if not packages or not query:
            return None
        
        try:
            query = query.lower().strip()
            query_clean = ''.join(c for c in query if c.isalnum())
            
            logger.info(f"PackageMatcher - Query: '{query}', Clean: '{query_clean}'")
            logger.info(f"Available packages: {[(p.get('carrier'), p.get('tracking_number')) for p in packages]}")
            
            # Strategy 1: Direct match
            for p in packages:
                carrier = p.get('carrier', '').lower().strip()
                carrier_clean = ''.join(c for c in carrier if c.isalnum())
                if query == carrier or query_clean == carrier_clean:
                    logger.info(f"Direct match: {p}")
                    return p
            
            # Strategy 2: Partial match
            for p in packages:
                carrier = p.get('carrier', '').lower().strip()
                carrier_clean = ''.join(c for c in carrier if c.isalnum())
                if (query in carrier or carrier in query or
                    query_clean in carrier_clean or carrier_clean in query_clean):
                    logger.info(f"Partial match: {p}")
                    return p
            
            # Strategy 3: Tracking number match
            for p in packages:
                tracking = p.get('tracking_number', '').lower().strip()
                tracking_clean = ''.join(c for c in tracking if c.isalnum())
                if (query in tracking or tracking in query or
                    query_clean in tracking_clean or tracking_clean in query_clean):
                    logger.info(f"Tracking match: {p}")
                    return p
            
            # Strategy 4: Word match
            query_words = query.split()
            for p in packages:
                carrier = p.get('carrier', '').lower().strip()
                for word in query_words:
                    if len(word) > 1 and (word in carrier or carrier in word):
                        logger.info(f"Word match: {p}")
                        return p
            
            return None
        except Exception as e:
            logger.error(f"PackageMatcher error: {e}")
            return None
class DeliveryInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("DeliveryInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        session_attr = handler_input.attributes_manager.session_attributes
        
        # Check tracking first
        tracking_value = get_slot_value(handler_input, 'tracking')
        if tracking_value:
            matched = PackageMatcher.match(packages, tracking_value)
            if matched:
                found_package = matched
        
        # Try to get carrier from slots
        carrier_value = None
        try:
             carrier_value = get_slot_value(handler_input, 'carrier')
             if carrier_value:
              carrier_value = carrier_value.lower()
        except:
            pass
        
        # Find package
        found_package = None
        if carrier_value:
            found_package = PackageMatcher.match(packages, carrier_value)
        
        if not found_package:
            found_package = session_attr.get('current_package', packages[-1])
        
        delivered_at = found_package.get('delivered_at', 'recently')
        carrier = found_package.get('carrier', 'unknown carrier')
        
        if delivered_at and delivered_at != 'recently':
            try:
                dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
                delivered_str = dt.strftime('%B %d, %Y at %I:%M %p')
            except:
                delivered_str = delivered_at
        else:
            delivered_str = 'recently'
        
        speak_output = f"The {carrier} package was delivered on {delivered_str}."
        session_attr['current_package'] = found_package
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response
class ExitIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("ExitIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "Goodbye! Have a great day."
        return handler_input.response_builder.speak(speak_output).set_should_end_session(True).response


class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "You can ask me about your packages. For example, specific details like carrier, tracking number, compartment, or delivery date."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class CancelAndStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return (
            ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or
            ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input)
        )

    def handle(self, handler_input):
        speak_output = "Goodbye! Have a great day."
        return handler_input.response_builder.speak(speak_output).set_should_end_session(True).response


class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])

        if not packages:
            speak_output = "You have no packages right now. Would you like to know anything else?"
            return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response

        session_attr = handler_input.attributes_manager.session_attributes

        carrier_value = None
        try:
            slots = handler_input.request_envelope.request.intent.slots
            if slots:
                for slot_name, slot in slots.items():
                    value = getattr(slot, 'value', None)
                    if value:
                        potential = str(value).strip().lower()
                        for p in packages:
                            carrier = p.get('carrier', '').lower()
                            if potential in carrier or carrier in potential:
                                carrier_value = potential
                                logger.info(f"Found carrier in fallback: {carrier_value}")
                                break
                        if carrier_value:
                            break
        except Exception as e:
            logger.error(f"Error in fallback: {e}")

        if carrier_value:
            found_package = PackageMatcher.match(packages, carrier_value)
            if found_package:
                speak_output = format_package_details(found_package)
                session_attr['current_package'] = found_package
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response

        package_list = []
        for p in packages:
            carrier = p.get('carrier', 'unknown carrier')
            tracking = p.get('tracking_number', 'no tracking number')
            package_list.append(f"one from {carrier} with tracking number {tracking}")

        if len(package_list) == 1:
            speak_output = f"You have {package_list[0]}. You can ask me about the carrier, tracking number, compartment, or when it was delivered."
        else:
            package_str = ", ".join(package_list[:-1]) + f", and {package_list[-1]}"
            speak_output = f"You have {len(packages)} packages: {package_str}. You can ask me about a specific package's carrier, tracking number, compartment, or when it was delivered."

        return handler_input.response_builder.speak(speak_output).ask("What would you like to know?").response
class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(f"Error handling request: {exception}", exc_info=True)
        speak_output = "Sorry, I had trouble processing your request. Please try again."
        return handler_input.response_builder.speak(speak_output).response


class SkillPermissionChangedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AlexaSkillEvent.SkillPermissionChanged")(handler_input)

    def handle(self, handler_input):
        logger.info("Permission Changed")
        logger.info(json.dumps(handler_input.request_envelope.to_dict(), indent=2))
        return handler_input.response_builder.response

class SkillDisabledHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("AlexaSkillEvent.SkillDisabled")(handler_input)

    def handle(self, handler_input):
        logger.info("Skill Disabled")
        logger.info(json.dumps(handler_input.request_envelope.to_dict(), indent=2))
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
# Add to your skill builder registration
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