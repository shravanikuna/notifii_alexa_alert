import logging
import json
import os
import uuid
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

# ============================================
# LOGGING CONFIGURATION
# ============================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================
# CONFIGURATION - Environment Variables
# ============================================

class Config:
    """Configuration settings from environment variables"""
    
    # Alexa Skill Messaging (from Build > Permissions > Alexa Skill Messaging)
    ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '')
    ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '')
    
    # Proactive Events - Development or Production
    ALEXA_API_URL = os.environ.get('ALEXA_API_URL', 'https://api.amazonalexa.com/v1/proactiveEvents/stages/development')

config = Config()


# ============================================
# ALEXA PROACTIVE EVENTS INTEGRATION
# ============================================

class AlexaProactiveEventsClient:
    """Client for sending proactive events to Alexa"""
    
    def __init__(self):
        self.client_id = config.ALEXA_CLIENT_ID
        self.client_secret = config.ALEXA_CLIENT_SECRET
        self.api_url = config.ALEXA_API_URL
    
    def get_token(self) -> Optional[str]:
        """Get access token for Proactive Events API"""
        token_url = "https://api.amazon.com/auth/o2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "alexa::proactive_events"
        }
        
        try:
            response = requests.post(token_url, data=payload)
            if response.status_code == 200:
                logger.info("✅ Successfully obtained proactive events token")
                return response.json().get("access_token")
            else:
                logger.error(f"❌ Token request failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Token request error: {str(e)}")
            return None
    
    def send_notification(self, alexa_user_id: str, seller_name: str, package_id: str) -> Dict:
        """Send a proactive notification to a user"""
        
        token = self.get_token()
        if not token:
            return {"status": "error", "message": "Failed to get access token"}
        
        # Clean ISO timestamps
        now_clean = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        expiry_clean = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        payload = {
            "timestamp": now_clean,
            "referenceId": f"notifii_{package_id}_{int(datetime.utcnow().timestamp())}",
            "expiryTime": expiry_clean,
            "event": {
                "name": "AMAZON.OrderStatus.Updated",
                "payload": {
                    "state": {
                        "status": "ORDER_DELIVERED"
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
                    "sellerName": seller_name
                }
            ],
            "relevantAudience": {
                "type": "Unicast",
                "payload": {
                    "user": alexa_user_id
                }
            }
        }
        
        logger.info(f"🚀 Sending payload: {json.dumps(payload, indent=2)}")
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(self.api_url, json=payload, headers=headers)
            
            if response.status_code == 202:
                logger.info(f"✅ Notification sent successfully to {alexa_user_id}")
                return {"status": "success", "code": 202}
            else:
                logger.error(f"❌ Notification failed: {response.status_code} - {response.text}")
                return {"status": "error", "code": response.status_code, "message": response.text}
                
        except Exception as e:
            logger.error(f"Notification error: {str(e)}")
            return {"status": "error", "message": str(e)}

alexa_client = AlexaProactiveEventsClient()


# ============================================
# WEBHOOK HANDLER (Entry Point for Package Events)
# ============================================

# Mock user data for POC - In production, fetch from Notifii API
MOCK_USER_CONFIGS = {
    "4B": {
        "unit": "4B",
        "user_id": "U123",
        "opted_alexa": True,
        "alexa_user_id": "amzn1.ask.account.YOUR_USER_ID_HERE",  # ⚠️ REPLACE WITH YOUR REAL USER ID
        "name": "John Doe"
    }
}

def get_user_configuration(unit: str) -> Optional[Dict]:
    """Fetch user configuration - Replace with Notifii API in production"""
    return MOCK_USER_CONFIGS.get(unit)

def handle_package_event(event: Dict, context: Any) -> Dict:
    """Main handler for package delivery events from Notifii"""
    logger.info(f"📦 Received package event: {event}")
    
    try:
        package_data = event.get('data', {})
        unit = package_data.get('unit')
        package_id = package_data.get('package_id')
        
        if not unit or not package_id:
            logger.error("Missing required fields: unit or package_id")
            return {"status": "error", "message": "Missing required fields"}
    
    except Exception as e:
        logger.error(f"Error parsing webhook: {str(e)}")
        return {"status": "error", "message": str(e)}
    
    # Fetch user configuration
    user_config = get_user_configuration(unit)
    if not user_config:
        logger.error(f"User not found for unit: {unit}")
        return {"status": "error", "message": "User not found"}
    
    # Check if user has opted into Alexa
    if not user_config.get('opted_alexa', False):
        logger.info(f"User {unit} has not opted into Alexa notifications")
        return {"status": "skipped", "reason": "User not opted in"}
    
    # Get Alexa user ID
    alexa_user_id = user_config.get('alexa_user_id')
    if not alexa_user_id:
        logger.info(f"User {unit} has no Alexa ID linked")
        return {"status": "skipped", "reason": "No Alexa ID linked"}
    
    # Get carrier name
    carrier = package_data.get('carrier', 'courier')
    
    # Send proactive notification
    result = alexa_client.send_notification(
        alexa_user_id=alexa_user_id,
        seller_name=carrier,
        package_id=package_id
    )
    
    if result.get('status') == 'success':
        logger.info(f"✅ Notification sent successfully for package {package_id}")
        return {
            "status": "success",
            "package_id": package_id,
            "unit": unit,
            "notification": {
                "alexa_user_id": alexa_user_id,
                "message": f"Your package from {carrier} has arrived.",
                "sent_at": datetime.utcnow().isoformat()
            }
        }
    else:
        logger.error(f"❌ Notification failed for package {package_id}: {result}")
        return {
            "status": "error",
            "package_id": package_id,
            "unit": unit,
            "error": result.get('message')
        }


# ============================================
# ALEXA SKILL INTENT HANDLERS (Voice Interaction)
# ============================================

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        print("\n" + "=" * 60)
        print(f"🚀 FOUND USER ID: {user_id}")
        print("=" * 60 + "\n")
        
        speak_output = "Welcome to Notifii Alert. You can ask about your packages, locker access, or mailroom hours."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class PackageStatusIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        logger.info(f"PackageStatusIntent triggered by user: {user_id}")
        speak_output = "You have 2 packages waiting. One from FedEx arrived today, and one from UPS arrived yesterday."
        return handler_input.response_builder.speak(speak_output).response


class LockerAccessIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("LockerAccessIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "Your package is in locker B4. Please use access code 12345."
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
        speak_output = "You can ask me about your packages, locker access, or mailroom hours. What would you like to know?"
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class CancelAndStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or \
               ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input)

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
# SKILL BUILDER
# ============================================

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(LockerAccessIntentHandler())
sb.add_request_handler(MailroomHoursIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())

lambda_handler = sb.lambda_handler()