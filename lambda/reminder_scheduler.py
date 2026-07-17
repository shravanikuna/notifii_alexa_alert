import json
import logging
from lambda_function import process_reminders

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def scheduled_handler(event, context):
    """
    AWS CloudWatch Events scheduled handler for reminders.
    Triggers daily at configured time.
    """
    logger.info("Running reminder scheduler")
    
    result = process_reminders(event, context)
    
    return {
        'statusCode': 200,
        'body': json.dumps(result)
    }