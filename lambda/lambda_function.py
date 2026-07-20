import logging
import json
import os
import time
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
# CONFIGURATION
# ============================================
class Config:
    """Configuration settings from environment variables"""
    
    # Notifii API
    NOTIFII_API_URL = os.environ.get('NOTIFII_API_URL', 'https://api.notifii.com/v1')
    NOTIFII_API_KEY = os.environ.get('NOTIFII_API_KEY', '')
    
    # Alexa Skill Messaging (from Build > Permissions > Alexa Skill Messaging)
    ALEXA_CLIENT_ID = os.environ.get('ALEXA_CLIENT_ID', '')
    ALEXA_CLIENT_SECRET = os.environ.get('ALEXA_CLIENT_SECRET', '')
    
    # Proactive Events - Development or Production
    ALEXA_API_URL = os.environ.get('ALEXA_API_URL', 'https://api.amazonalexa.com/v1/proactiveEvents/stages/development')
    
    # Reminder Schedule (in days)
    REMINDER_DAY_1 = 3
    REMINDER_DAY_2 = 5
    REMINDER_DAY_3 = 7

config = Config()


# ============================================
# NOTIFII API INTEGRATION
# ============================================

class NotifiiAPIClient:
    """Client for interacting with Notifii API"""
    
    def __init__(self):
        self.base_url = config.NOTIFII_API_URL
        self.api_key = config.NOTIFII_API_KEY
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        })
    
    def get_user_configuration(self, unit: str) -> Optional[Dict]:
        """Fetch user configuration from Notifii API"""
        try:
            response = self.session.get(f'{self.base_url}/residents/{unit}')
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to fetch user config: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error fetching user config: {str(e)}")
            return None
    
    def get_package_details(self, package_id: str) -> Optional[Dict]:
        """Fetch package details from Notifii API"""
        try:
            response = self.session.get(f'{self.base_url}/packages/{package_id}')
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to fetch package: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching package: {str(e)}")
            return None
    
    def mark_package_picked_up(self, package_id: str) -> bool:
        """Mark package as picked up in Notifii"""
        try:
            response = self.session.put(f'{self.base_url}/packages/{package_id}/pickup')
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error marking pickup: {str(e)}")
            return False
    
    def refresh_user_token(self, user_id: str) -> Optional[Dict]:
        """Refresh OAuth token for a user"""
        try:
            response = self.session.post(f'{self.base_url}/residents/{user_id}/refresh-token')
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Token refresh failed: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Token refresh error: {str(e)}")
            return None

notifii_client = NotifiiAPIClient()

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
                logger.info("Successfully obtained proactive events token")
                return response.json().get("access_token")
            else:
                logger.error(f"Token request failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Token request error: {str(e)}")
            return None
    
    def send_notification(self, alexa_user_id: str, seller_name: str, status: str = "ORDER_DELIVERED", reference_id: str = None) -> Dict:
        """Send a proactive notification to a user"""
        token = self.get_token()
        if not token:
            return {"status": "error", "message": "Failed to get access token"}
        
        reference_id = reference_id or f"notifii_{uuid.uuid4()}"
        now = datetime.utcnow()
        now_str = now.isoformat() + "Z"
        
        payload = {
            "timestamp": now_str,
            "referenceId": reference_id,
            "expiryTime": (now + timedelta(hours=24)).isoformat() + "Z",
            "event": {
                "name": "AMAZON.OrderStatus.Updated",
                "payload": {
                    "state": {
                        "status": status,
                        "deliveredOn": now_str
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
        
        logger.info(f"Sending payload: {json.dumps(payload, indent=2)}")
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(self.api_url, json=payload, headers=headers)
            if response.status_code == 202:
                logger.info(f"✅ Notification sent successfully to {alexa_user_id}")
                return {"status": "success", "code": 202, "referenceId": reference_id}
            else:
                logger.error(f"❌ Notification failed: {response.status_code} - {response.text}")
                return {"status": "error", "code": response.status_code, "message": response.text}
        except Exception as e:
            logger.error(f"Notification error: {str(e)}")
            return {"status": "error", "message": str(e)}

alexa_client = AlexaProactiveEventsClient()


# ============================================
# AI CONTEXT ANALYZER
# ============================================

class AIPackageAnalyzer:
    """AI Agent for contextual analysis of package data"""
    
    def __init__(self, package_data: Dict, user_config: Dict):
        self.package = package_data
        self.user_config = user_config
        self.waiting_days = self._calculate_waiting_days()
    
    def _calculate_waiting_days(self) -> int:
        delivered_at = self.package.get('delivered_at')
        if not delivered_at:
            return 0
        try:
            delivered = datetime.fromisoformat(delivered_at.replace('Z', '+00:00'))
            now = datetime.now(delivered.tzinfo)
            return (now - delivered).days
        except:
            return 0
    
    def _get_time_of_day(self) -> str:
        hour = datetime.now().hour
        if 5 <= hour < 12: return "morning"
        elif 12 <= hour < 17: return "afternoon"
        elif 17 <= hour < 21: return "evening"
        else: return "night"
    
    def _get_urgency(self) -> str:
        days = self.waiting_days
        if days >= 7: return "critical"
        elif days >= 5: return "high"
        elif days >= 3: return "medium"
        else: return "low"
    
    def _generate_carrier_name(self) -> str:
        carrier = self.package.get('carrier', 'unknown')
        carrier_map = {
            'FDX': 'FedEx', 'UPS': 'UPS', 'USPS': 'USPS', 'AMZN': 'Amazon', 'DHL': 'DHL'
        }
        return carrier_map.get(carrier, carrier)
    
    def analyze(self) -> Dict:
        return {
            'carrier': self._generate_carrier_name(),
            'compartment': self.package.get('compartment', 'locker'),
            'waiting_days': self.waiting_days,
            'urgency': self._get_urgency(),
            'time_of_day': self._get_time_of_day(),
            'package_id': self.package.get('package_id'),
            'unit': self.user_config.get('unit')
        }
    
    def generate_notification_message(self) -> str:
        context = self.analyze()
        if context['waiting_days'] == 0:
            return f"Your package from {context['carrier']} has arrived in {context['compartment']}."
        elif context['waiting_days'] < 3:
            return f"Your package from {context['carrier']} is ready for pickup in {context['compartment']}."
        elif context['waiting_days'] == 3:
            return f"Your package from {context['carrier']} has been waiting for 3 days in {context['compartment']}. Please pick it up."
        elif context['waiting_days'] == 5:
            return f"Your package from {context['carrier']} has been waiting for 5 days. Please collect it from {context['compartment']}."
        elif context['waiting_days'] >= 7:
            return f"URGENT: Your package from {context['carrier']} has been waiting for {context['waiting_days']} days. Please collect it immediately."
        else:
            return f"Your package from {context['carrier']} is ready for pickup in {context['compartment']}."
    
    def get_delivery_status(self) -> str:
        return "ORDER_DELIVERED"


# ============================================
# WEBHOOK HANDLER
# ============================================

def handle_package_event(event: Dict, context: Any) -> Dict:
    """Main handler for package delivery events from Notifii"""
    logger.info(f"Received package event: {event}")
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
    
    user_config = notifii_client.get_user_configuration(unit)
    if not user_config:
        logger.error(f"User not found for unit: {unit}")
        return {"status": "error", "message": "User not found"}
    
    if not user_config.get('opted_alexa', False):
        logger.info(f"User {unit} has not opted into Alexa notifications")
        return {"status": "skipped", "reason": "User not opted in"}
    
    alexa_user_id = user_config.get('alexa_user_id')
    if not alexa_user_id:
        logger.info(f"User {unit} has no Alexa ID linked")
        return {"status": "skipped", "reason": "No Alexa ID linked"}
    
    package_details = notifii_client.get_package_details(package_id) or package_data
    
    analyzer = AIPackageAnalyzer(package_details, user_config)
    message = analyzer.generate_notification_message()
    status = analyzer.get_delivery_status()
    context_info = analyzer.analyze()
    
    result = alexa_client.send_notification(
        alexa_user_id=alexa_user_id,
        seller_name=context_info['carrier'],
        status=status,
        reference_id=f"notifii_{package_id}"
    )
    
    if result.get('status') == 'success':
        return {
            "status": "success", "package_id": package_id, "unit": unit,
            "notification": {"alexa_user_id": alexa_user_id, "message": message, "sent_at": datetime.utcnow().isoformat()}
        }
    else:
        return {"status": "error", "package_id": package_id, "unit": unit, "error": result.get('message')}


# ============================================
# ALEXA SKILL INTENT HANDLERS
# ============================================

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        
        print("\n========================================================")
        print(f"🚀 LAUNCH ROUTE USER ID: {user_id}")
        print("========================================================\n")
        
        speak_output = "Welcome to Notiffi Alert. You can ask about your packages, locker access, or mailroom hours."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        user_id = handler_input.request_envelope.context.system.user.user_id
        
        print("\n========================================================")
        print(f"🚀 INTERCEPTED FALLBACK ROUTE USER ID: {user_id}")
        print("========================================================\n")
        
        speak_output = "I'm sorry, I didn't understand. You can ask about your packages, locker access, or mailroom hours."
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


# ============================================
# SKILL BUILDER
# ============================================

sb = SkillBuilder()

# Order matters: Register explicit intents before global fallback handlers
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(LockerAccessIntentHandler())
sb.add_request_handler(MailroomHoursIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())  # Intercept routing safety valve placed cleanly at the end

lambda_handler = sb.lambda_handler()