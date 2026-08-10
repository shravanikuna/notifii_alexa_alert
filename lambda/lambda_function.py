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

def format_package_details(package: Dict, index: int = None) -> str:
    """Format full package details with optional index"""
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
    
    prefix = f"Package {index + 1}: " if index is not None else ""
    return f"{prefix}Package from {carrier}, tracking number {tracking}, stored in compartment {compartment}, delivered on {delivered_str}"

def get_package_summary(packages: List[Dict]) -> str:
    """Get a summary of all packages with their positions"""
    if not packages:
        return "You have no packages right now."
    
    total = len(packages)
    
    # Build detailed summary with package numbers
    parts = []
    for i, p in enumerate(packages, 1):
        carrier = p.get('carrier', 'unknown')
        parts.append(f"Package {i} from {carrier}")
    
    if total == 1:
        return f"You have 1 package: {parts[0]}. If you want to know more about this package, just ask me."
    else:
        return f"You have {total} packages: {', '.join(parts)}. If you want to know more about any package, just ask me by saying 'tell me about package 1' or 'tell me about the FedEx package'."

def find_package_by_carrier(packages: List[Dict], carrier_query: str) -> Optional[Dict]:
    """Find a package by carrier name (case-insensitive)"""
    carrier_query = carrier_query.lower().strip()
    for p in packages:
        if carrier_query in p.get('carrier', '').lower():
            return p
    return None

def find_package_by_tracking(packages: List[Dict], tracking_query: str) -> Optional[Dict]:
    """Find a package by tracking number (partial match)"""
    tracking_query = tracking_query.lower().strip()
    for p in packages:
        if tracking_query in p.get('tracking_number', '').lower():
            return p
    return None

def find_package_by_position(packages: List[Dict], position: str) -> Optional[Dict]:
    """Find package by position (first, last, 1st, 2nd, etc.)"""
    position = position.lower().strip()
    
    if position in ['first', '1st', '1']:
        return packages[0] if packages else None
    elif position in ['second', '2nd', '2']:
        return packages[1] if len(packages) > 1 else None
    elif position in ['third', '3rd', '3']:
        return packages[2] if len(packages) > 2 else None
    elif position in ['fourth', '4th', '4']:
        return packages[3] if len(packages) > 3 else None
    elif position in ['fifth', '5th', '5']:
        return packages[4] if len(packages) > 4 else None
    elif position in ['last', 'final']:
        return packages[-1] if packages else None
    else:
        # Try to extract number from position string
        try:
            import re
            numbers = re.findall(r'\d+', position)
            if numbers:
                idx = int(numbers[0]) - 1
                if 0 <= idx < len(packages):
                    return packages[idx]
        except:
            pass
    return None

def determine_package_context(handler_input, packages: List[Dict]) -> Optional[Dict]:
    """Determine which package the user is asking about based on slots and session"""
    if not packages:
        return None
    
    session_attr = handler_input.attributes_manager.session_attributes
    current_package = session_attr.get('current_package')
    
    # Check if user specified a carrier
    try:
        carrier_slot = handler_input.request_envelope.request.intent.slots.get('carrier', {})
        if carrier_slot and carrier_slot.get('value'):
            carrier_value = carrier_slot.get('value', '').strip()
            if carrier_value:
                found = find_package_by_carrier(packages, carrier_value)
                if found:
                    session_attr['current_package'] = found
                    return found
    except:
        pass
    
    # Check if user specified a position (first, second, etc.)
    try:
        position_slot = handler_input.request_envelope.request.intent.slots.get('position', {})
        if position_slot and position_slot.get('value'):
            position_value = position_slot.get('value', '').strip()
            if position_value:
                found = find_package_by_position(packages, position_value)
                if found:
                    session_attr['current_package'] = found
                    return found
    except:
        pass
    
    # Check if user specified a tracking number
    try:
        tracking_slot = handler_input.request_envelope.request.intent.slots.get('tracking', {})
        if tracking_slot and tracking_slot.get('value'):
            tracking_value = tracking_slot.get('value', '').strip()
            if tracking_value:
                found = find_package_by_tracking(packages, tracking_value)
                if found:
                    session_attr['current_package'] = found
                    return found
    except:
        pass
    
    # Return current package from session or latest
    if current_package and current_package in packages:
        return current_package
    else:
        # Default to latest
        session_attr['current_package'] = packages[-1]
        return packages[-1]

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
        return handler_input.response_builder.speak(speak_output).ask("Which package would you like to know more about?").response


class PackageDetailsIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageDetailsIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to get details about."
            return handler_input.response_builder.speak(speak_output).response
        
        # Determine which package the user is asking about
        selected_package = determine_package_context(handler_input, packages)
        
        if selected_package:
            # Find the index of the selected package
            try:
                index = packages.index(selected_package)
                speak_output = format_package_details(selected_package, index)
            except ValueError:
                speak_output = format_package_details(selected_package)
        else:
            speak_output = "I couldn't find that package. You can ask about a specific package by saying 'tell me about the FedEx package' or 'tell me about package 2'."
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else about this package?").response


class CarrierInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CarrierInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        # Determine which package the user is asking about
        selected_package = determine_package_context(handler_input, packages)
        
        if selected_package:
            carrier = selected_package.get('carrier', 'unknown carrier')
            speak_output = f"This package was delivered by {carrier}."
        else:
            speak_output = "I couldn't find that package. Please specify which package you're asking about."
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class TrackingInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("TrackingInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        # Determine which package the user is asking about
        selected_package = determine_package_context(handler_input, packages)
        
        if selected_package:
            tracking = selected_package.get('tracking_number', 'no tracking number available')
            speak_output = f"The tracking number is {tracking}."
        else:
            speak_output = "I couldn't find that package. Please specify which package you're asking about."
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class CompartmentInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("CompartmentInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        # Determine which package the user is asking about
        selected_package = determine_package_context(handler_input, packages)
        
        if selected_package:
            compartment = selected_package.get('compartment', 'unknown compartment')
            speak_output = f"This package is stored in compartment {compartment}."
        else:
            speak_output = "I couldn't find that package. Please specify which package you're asking about."
        
        return handler_input.response_builder.speak(speak_output).ask("Would you like to know anything else?").response


class DeliveryInquiryIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("DeliveryInquiryIntent")(handler_input)

    def handle(self, handler_input):
        packages = LATEST_PACKAGES.get(CURRENT_UNIT, [])
        if not packages:
            speak_output = "You have no packages to inquire about."
            return handler_input.response_builder.speak(speak_output).response
        
        # Determine which package the user is asking about
        selected_package = determine_package_context(handler_input, packages)
        
        if selected_package:
            delivered_at = selected_package.get('delivered_at', 'recently')
            if delivered_at and delivered_at != 'recently':
                try:
                    dt = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
                    delivered_str = dt.strftime('%B %d, %Y at %I:%M %p')
                except:
                    delivered_str = delivered_at
            else:
                delivered_str = 'recently'
            speak_output = f"This package was delivered on {delivered_str}."
        else:
            speak_output = "I couldn't find that package. Please specify which package you're asking about."
        
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
        speak_output = "You can ask me about your packages. For example, you can say 'what packages do I have?', 'tell me about the FedEx package', 'tell me about package 2', or ask for specific details like carrier, tracking number, compartment, or delivery date."
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
        
        if len(packages) == 1:
            speak_output = "I understand you have one package. You can ask about its carrier, tracking number, compartment, or when it was delivered. Just say 'tell me about my package' or ask a specific question."
        else:
            # List all packages with their positions
            parts = []
            for i, p in enumerate(packages, 1):
                parts.append(f"Package {i} from {p.get('carrier', 'unknown')}")
            speak_output = f"I understand you have {len(packages)} packages: {', '.join(parts)}. You can ask about a specific package by saying 'tell me about package 1' or 'tell me about the FedEx package'."
        
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
sb.add_request_handler(DeliveryInquiryIntentHandler())
sb.add_request_handler(ExitIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())

sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()