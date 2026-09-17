"""Hand-off from the fast Slack-facing handler to the slow agent worker.

AWS: SQS FIFO queue -> agent_worker Lambda.
Local (Tilt): a background thread calls the worker handler directly.
"""

import json
import logging
import os
import threading
from functools import lru_cache

import boto3

from slack_app.config import is_local

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _sqs():
    return boto3.client("sqs")


def enqueue(job: dict, group_id: str, dedup_id: str) -> None:
    queue_url = os.getenv("PROCESSING_QUEUE_URL")
    if queue_url:
        _sqs().send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(job),
            MessageGroupId=group_id,
            MessageDeduplicationId=dedup_id,
        )
        return

    if not is_local():
        raise RuntimeError("PROCESSING_QUEUE_URL is not set")

    from slack_app.handlers import agent_worker  # local import: avoid a cycle at module load

    event = {"Records": [{"messageId": dedup_id, "body": json.dumps(job)}]}
    threading.Thread(target=agent_worker.handler, args=(event, None), daemon=True).start()
    logger.info("Dispatched job %s to in-process worker", dedup_id)
