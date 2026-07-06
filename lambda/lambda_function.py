import logging
import os
import http.client
import json
import requests
import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

# ====================================================================
# 1. ALEXA REQUEST HANDLERS
# ====================================================================

class PackageStatusIntentHandler(AbstractRequestHandler):
    """Handler for Package Status Intent"""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "You have 2 packages waiting. One from FedEx arrived today, and one from UPS arrived yesterday."
        return handler_input.response_builder.speak(speak_output).response

class MailroomHoursIntentHandler(AbstractRequestHandler):
    """Handler for Mailroom Hours Intent"""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("MailroomHoursIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "The mailroom is open from 8 AM to 8 PM, Monday through Friday."
        return handler_input.response_builder.speak(speak_output).response

class LockerAccessIntentHandler(AbstractRequestHandler):
    """Handler for Locker Access Intent"""
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("LockerAccessIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "Your package is in locker B4. Please use access code 12345."
        return handler_input.response_builder.speak(speak_output).response

class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch"""
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        speak_output = "Welcome to Notiffi Alert. You can ask about your packages, locker access, or mailroom hours."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


# ====================================================================
# 2. PROACTIVE NOTIFICATIONS & UTILITY LOGIC
# ====================================================================

def send_package_notification(alexa_user_id, seller_name, status):
    # Step 1: Get access token
    token_url = "https://api.amazon.com/auth/o2/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": "YOUR_CLIENT_ID",         # Get from Build > Permissions
        "client_secret": "YOUR_CLIENT_SECRET", # Get from Build > Permissions
        "scope": "alexa::proactive_events"
    }
    
    response = requests.post(token_url, data=payload)
    if response.status_code != 200:
        return {"error": "Failed to get token"}
    
    access_token = response.json().get("access_token")
    
    # Step 2: Build notification payload
    event_payload = {
        "timestamp": "2026-07-02T10:00:00.00Z",
        "referenceId": "notifii_pkg_12345",
        "expiryTime": "2026-07-03T10:00:00.00Z",
        "event": {
            "name": "AMAZON.OrderStatus.Updated",
            "payload": {
                "state": {
                    "status": "ORDER_DELIVERED",
                    "deliveredOn": "2026-07-02T10:00:00.00Z"
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
    
    # Step 3: Send notification
    api_url = "https://api.amazonalexa.com/v1/proactiveEvents/stages/development"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(api_url, json=event_payload, headers=headers)
    return {"status_code": response.status_code}


def get_proactive_events_token(client_id, client_secret):
    conn = http.client.HTTPSConnection("api.amazon.com")
    payload = f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}&scope=alexa::proactive_events"
    headers = {'Content-Type': "application/x-www-form-urlencoded;charset=UTF-8"}
    conn.request("POST", "/auth/o2/token", payload, headers)
    res = conn.getresponse()
    data = res.read()
    return json.loads(data.decode("utf-8"))


# ====================================================================
# 3. SKILL ROUTING & EXPORT
# ====================================================================

sb = SkillBuilder()

# Register intent handlers to the blueprint
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(LockerAccessIntentHandler())
sb.add_request_handler(MailroomHoursIntentHandler())

# The main entrypoint mapping Alexa requests to our skill backend
lambda_handler = sb.lambda_handler()