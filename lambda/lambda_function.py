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
    """Get a summary of all packages"""
    if not packages:
        return "You have no packages right now."
    
    total = len(packages)
    
    # Group by carrier for cleaner summary
    carrier_counts = {}
    for p in packages:
        carrier = p.get('carrier', 'unknown')
        carrier_counts[carrier] = carrier_counts.get(carrier, 0) + 1
    
    # Build the summary
    parts = []
    for carrier, count in carrier_counts.items():
        parts.append(f"{count} from {carrier}")
    
    if total == 1:
        return f"You have a package: {', '.join(parts)}. If you want to know more about this package, just ask me."
    else:
        return f"You have {total} packages: {', '.join(parts)}. If you want to know more about any package, just ask me."

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
        
        # Reset conversation state
        session_attr = handler_input.attributes_manager.session_attributes
        session_attr['conversation_state'] = CONVERSATION_STATE['IDLE']
        
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
        
        # Get session attributes to track state
        session_attr = handler_input.attributes_manager.session_attributes
        conversation_state = session_attr.get('conversation_state', CONVERSATION_STATE['IDLE'])
        
        # Get carrier from slot
        try:
            carrier_slot = handler_input.request_envelope.request.intent.slots.get('carrier', {})
            carrier_value = carrier_slot.get('value', '').lower().strip() if carrier_slot else ''
            logger.info(f"PackageDetailsIntent - Carrier value: {carrier_value}")
            logger.info(f"Conversation state: {conversation_state}")
        except:
            carrier_value = ''
        
        # If we were asking which package and user responded with carrier
        if conversation_state == CONVERSATION_STATE['ASKING_WHICH_PACKAGE'] and carrier_value:
            # Try to find matching package
            found_package = self.find_matching_package(packages, carrier_value)
            
            if found_package:
                speak_output = format_package_details(found_package)
                session_attr['current_package'] = found_package
                session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
                return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response
            else:
                # Show available options
                carrier_list = sorted(set(p.get('carrier', 'unknown') for p in packages))
                speak_output = f"I couldn't find a package matching '{carrier_value}'. You have packages from {', '.join(carrier_list)}. Please say the carrier name or tracking number."
                return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response
        
        # If no carrier specified
        if not carrier_value:
            if len(packages) == 1:
                found_package = packages[0]
                speak_output = format_package_details(found_package)
                session_attr['current_package'] = found_package
                session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
            else:
                # Build list of unique carriers
                carrier_list = sorted(set(p.get('carrier', 'unknown') for p in packages))
                speak_output = f"You have packages from {', '.join(carrier_list)}. Which one would you like details about?"
                session_attr['conversation_state'] = CONVERSATION_STATE['ASKING_WHICH_PACKAGE']
                return handler_input.response_builder.speak(speak_output).ask("Please say the carrier name or tracking number.").response
        else:
            # Try to find matching package
            found_package = self.find_matching_package(packages, carrier_value)
            
            if found_package:
                speak_output = format_package_details(found_package)
                session_attr['current_package'] = found_package
                session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
            else:
                carrier_list = sorted(set(p.get('carrier', 'unknown') for p in packages))
                speak_output = f"I couldn't find a package matching '{carrier_value}'. You have packages from {', '.join(carrier_list)}. Please say the carrier name or tracking number."
                session_attr['conversation_state'] = CONVERSATION_STATE['ASKING_WHICH_PACKAGE']
                return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response
    
    def find_matching_package(self, packages, query):
        """Find a package using intelligent matching without hardcoding"""
        query = query.lower().strip()
        
        # Clean query - remove spaces, dots, special characters
        query_clean = ''.join(c for c in query if c.isalnum())
        
        logger.info(f"Searching for: {query} (cleaned: {query_clean})")
        logger.info(f"Available packages: {[(p.get('carrier'), p.get('tracking_number')) for p in packages]}")
        
        # Strategy 1: Direct match after cleaning
        for p in packages:
            carrier = p.get('carrier', '').lower().strip()
            carrier_clean = ''.join(c for c in carrier if c.isalnum())
            tracking = p.get('tracking_number', '').lower().strip()
            
            logger.info(f"Comparing with carrier: {carrier} (cleaned: {carrier_clean})")
            
            # Check if query matches any field
            if (query_clean == carrier_clean or 
                query_clean in carrier_clean or 
                carrier_clean in query_clean or
                query in carrier or 
                carrier in query or
                query in tracking or 
                tracking in query):
                logger.info(f"Found match: {p}")
                return p
        
        # Strategy 2: Try matching by parts (for "d h l" -> "dhl")
        query_parts = query.split()
        query_parts_clean = [''.join(c for c in part if c.isalnum()) for part in query_parts if part]
        
        for p in packages:
            carrier = p.get('carrier', '').lower().strip()
            carrier_clean = ''.join(c for c in carrier if c.isalnum())
            
            # Check if any part matches
            for part in query_parts_clean:
                if part and (part in carrier_clean or carrier_clean in part):
                    logger.info(f"Found match by part: {p}")
                    return p
        
        # Strategy 3: Try initials match (for "DHL", "UPS", "FEDEX")
        if len(query_clean) <= 4:
            for p in packages:
                carrier = p.get('carrier', '').lower().strip()
                carrier_clean = ''.join(c for c in carrier if c.isalnum())
                # Check if query is initials (e.g., "dhl" matches "DHL Express")
                if query_clean == carrier_clean[:len(query_clean)]:
                    logger.info(f"Found match by initials: {p}")
                    return p
        
        # Strategy 4: Try fuzzy match on carrier names (basic version)
        for p in packages:
            carrier = p.get('carrier', '').lower().strip()
            carrier_clean = ''.join(c for c in carrier if c.isalnum())
            # Check if any common words match
            common_words = ['express', 'logistics', 'delivery', 'services', 'parcel']
            for word in common_words:
                if word in carrier_clean and word in query_clean:
                    logger.info(f"Found match by common word: {p}")
                    return p
        
        return None

class WhichPackageIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("WhichPackageIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages."
            return handler_input.response_builder.speak(speak_output).response
        
        # Get the carrier from the user's response
        try:
            carrier_slot = handler_input.request_envelope.request.intent.slots.get('carrier', {})
            carrier_value = carrier_slot.get('value', '').lower().strip() if carrier_slot else ''
            logger.info(f"WhichPackageIntent - Carrier value: {carrier_value}")
        except:
            carrier_value = ''
        
        if not carrier_value:
            carrier_list = sorted(set(p.get('carrier', 'unknown') for p in packages))
            speak_output = f"Which package would you like details about? You have packages from {', '.join(carrier_list)}."
            return handler_input.response_builder.speak(speak_output).ask("Please say the carrier name or tracking number.").response
        
        # Find matching package using the same matcher
        matcher = PackageDetailsIntentHandler()
        found_package = matcher.find_matching_package(packages, carrier_value)
        
        if found_package:
            speak_output = format_package_details(found_package)
            session_attr = handler_input.attributes_manager.session_attributes
            session_attr['current_package'] = found_package
            session_attr['conversation_state'] = CONVERSATION_STATE['SHOWING_DETAILS']
        else:
            carrier_list = sorted(set(p.get('carrier', 'unknown') for p in packages))
            speak_output = f"I couldn't find a package matching '{carrier_value}'. You have packages from {', '.join(carrier_list)}. Please say the carrier name or tracking number."
            session_attr = handler_input.attributes_manager.session_attributes
            session_attr['conversation_state'] = CONVERSATION_STATE['ASKING_WHICH_PACKAGE']
            return handler_input.response_builder.speak(speak_output).ask("Which package would you like details about?").response
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response


class CarrierInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CarrierInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
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
        
        # Get from session or latest
        session_attr = handler_input.attributes_manager.session_attributes
        package = session_attr.get('current_package', packages[-1])
        
        tracking = package.get('tracking_number', 'no tracking number available')
        speak_output = f"The tracking number is {tracking}."
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class CompartmentInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CompartmentInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        # Get from session or latest
        session_attr = handler_input.attributes_manager.session_attributes
        package = session_attr.get('current_package', packages[-1])
        
        compartment = package.get('compartment', 'unknown compartment')
        speak_output = f"This package is stored in compartment {compartment}."
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class DeliveryInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("DeliveryInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        # Get from session or latest
        session_attr = handler_input.attributes_manager.session_attributes
        package = session_attr.get('current_package', packages[-1])
        
        delivered_at = package.get('delivered_at', 'recently')
        if delivered_at and delivered_at != 'recently':
            try:
                dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
                delivered_str = dt.strftime('%B %d, %Y at %I:%M %p')
            except:
                delivered_str = delivered_at
        else:
            delivered_str = 'recently'
        
        speak_output = f"This package was delivered on {delivered_str}."
        
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
        
        # Get the user's utterance
        try:
            utterance = handler_input.request_envelope.request.intent.name
            logger.info(f"Fallback intent triggered with utterance: {utterance}")
        except:
            pass
        
        # Build dynamic package listing
        if len(packages) == 1:
            package = packages[0]
            carrier = package.get('carrier', 'unknown carrier')
            tracking = package.get('tracking_number', 'no tracking number')
            compartment = package.get('compartment', 'unknown compartment')
            
            speak_output = f"You have one package from {carrier} with tracking number {tracking}, stored in compartment {compartment}. Would you like to know more about it?"
        else:
            # Group packages by carrier for cleaner presentation
            carrier_groups = {}
            for package in packages:
                carrier = package.get('carrier', 'unknown carrier')
                if carrier not in carrier_groups:
                    carrier_groups[carrier] = []
                carrier_groups[carrier].append(package)
            
            # Build description with count per carrier
            parts = []
            for carrier, pkgs in carrier_groups.items():
                if len(pkgs) == 1:
                    tracking = pkgs[0].get('tracking_number', 'no tracking number')
                    parts.append(f"one from {carrier} with tracking number {tracking}")
                else:
                    # Multiple packages from same carrier
                    tracking_numbers = [p.get('tracking_number', 'no tracking number') for p in pkgs]
                    parts.append(f"{len(pkgs)} from {carrier} with tracking numbers {', '.join(tracking_numbers)}")
            
            # Join with commas and "and"
            if len(parts) == 1:
                package_list = parts[0]
            elif len(parts) == 2:
                package_list = f"{parts[0]} and {parts[1]}"
            else:
                package_list = ", ".join(parts[:-1]) + f", and {parts[-1]}"
            
            speak_output = f"You have {len(packages)} packages: {package_list}. You can ask me about a specific package's carrier, tracking number, compartment, or when it was delivered."
        
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
sb.add_request_handler(SkillPermissionChangedHandler())
sb.add_request_handler(SkillDisabledHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(ProactiveSubscriptionChangedHandler())
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(PackageDetailsIntentHandler())
sb.add_request_handler(CarrierInquiryIntentHandler())
sb.add_request_handler(TrackingInquiryIntentHandler())
sb.add_request_handler(CompartmentInquiryIntentHandler())
sb.add_request_handler(DeliveryInquiryIntentHandler())  # NEW
sb.add_request_handler(ExitIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())

sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()