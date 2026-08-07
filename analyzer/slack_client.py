import logging

import requests

logger = logging.getLogger(__name__)


def send_notification(webhook_url, event, ai_analysis):
    text = f"*{event.error_type}* on `{event.server_id}`\n{event.message}"
    if ai_analysis:
        text += f"\n\n*분석*: {ai_analysis}"

    try:
        requests.post(webhook_url, json={"text": text}, timeout=5)
    except requests.RequestException as e:
        logger.warning("failed to send Slack notification: %s", e)
