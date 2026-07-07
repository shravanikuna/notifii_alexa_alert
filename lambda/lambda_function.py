import logging
import ask_sdk_core.utils as ask_utils
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================
# 1. CUSTOM INTENT HANDLERS
# ============================================

class PackageStatusIntentHandler(AbstractRequestHandler):
    """Handler for Package Status Intent"""
    
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("PackageStatusIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "You have 2 packages waiting. One from FedEx arrived today, and one from UPS arrived yesterday."
        
        # Log the user ID for debugging (you'll get this from the request)
        user_id = handler_input.request_envelope.context.system.user.user_id
        logger.info(f"PackageStatusIntent triggered by user: {user_id}")
        
        return handler_input.response_builder.speak(speak_output).response


class LockerAccessIntentHandler(AbstractRequestHandler):
    """Handler for Locker Access Intent"""
    
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("LockerAccessIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "Your package is in locker B4. Please use access code 12345."
        return handler_input.response_builder.speak(speak_output).response


class MailroomHoursIntentHandler(AbstractRequestHandler):
    """Handler for Mailroom Hours Intent"""
    
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("MailroomHoursIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "The mailroom is open from 8 AM to 8 PM, Monday through Friday."
        return handler_input.response_builder.speak(speak_output).response


class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch"""
    
    def can_handle(self, handler_input):
        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        speak_output = "Welcome to Notiffi Alert. You can ask about your packages, locker access, or mailroom hours."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class HelpIntentHandler(AbstractRequestHandler):
    """Handler for Help Intent"""
    
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "You can ask me about your packages, locker access, or mailroom hours. What would you like to know?"
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


class CancelAndStopIntentHandler(AbstractRequestHandler):
    """Handler for Cancel and Stop Intents"""
    
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or \
               ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "Goodbye!"
        return handler_input.response_builder.speak(speak_output).response


class FallbackIntentHandler(AbstractRequestHandler):
    """Handler for Fallback Intent"""
    
    def can_handle(self, handler_input):
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        speak_output = "I'm sorry, I didn't understand that. You can ask about your packages, locker access, or mailroom hours."
        return handler_input.response_builder.speak(speak_output).ask("How can I help you?").response


# ============================================
# 2. SKILL BUILDER - REGISTER ALL HANDLERS
# ============================================

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(PackageStatusIntentHandler())
sb.add_request_handler(LockerAccessIntentHandler())
sb.add_request_handler(MailroomHoursIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())

# ============================================
# 3. LAMBDA HANDLER (Entry Point)
# ============================================

lambda_handler = sb.lambda_handler()