import json
import logging
import os
from lambda_function import handle_package_event

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def webhook_handler(event, context):
    """
    AWS API Gateway webhook handler for Notifii package events.
    """
    logger.info(f"Received webhook: {event}")
    
    # Parse the webhook body
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        body = event.get('body', {})
    
    # Validate webhook signature (if configured)
    # Verify webhook secret from Notifii
    webhook_secret = event.get('headers', {}).get('X-Webhook-Secret')
    expected_secret = os.environ.get('NOTIFII_WEBHOOK_SECRET')
    
    if expected_secret and webhook_secret != expected_secret:
        logger.warning("Invalid webhook signature")
        return {
            'statusCode': 401,
            'body': json.dumps({'status': 'error', 'message': 'Invalid signature'})
        }
    
    # Process the event
    result = handle_package_event(body, context)
    
    return {
        'statusCode': 200,
        'body': json.dumps(result)
    }