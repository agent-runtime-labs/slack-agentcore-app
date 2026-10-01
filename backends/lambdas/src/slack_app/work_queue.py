"""Hand-off from the fast Slack-facing handler to the slow workers.

AWS: SQS FIFO queue -> agent_worker Lambda, and the knowledge-index standard queue (with
a delay) -> knowledge_indexer Lambda.
Local (Tilt): a background thread or timer calls the handler directly.
"""

import json
import logging
import os
import threading
from functools import lru_cache

import boto3

from slack_app.config import is_local

logger = logging.getLogger(__name__)

# Set on a job the OAuth callback queues again after the user connected an account
# (see pending_auth.PendingAuth.resume_job).
RESUMED_AFTER_AUTH = "resumed_after_auth"


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


def enqueue_knowledge(job: dict, delay_seconds: int) -> None:
    """Hand-off to the knowledge indexer: a standard SQS queue with a delay (knowledge/events.py).

    Locally, a timer calls the indexer in this process after the same delay.
    """
    queue_url = os.getenv("KNOWLEDGE_QUEUE_URL")
    if queue_url:
        _sqs().send_message(QueueUrl=queue_url, MessageBody=json.dumps(job), DelaySeconds=delay_seconds)
        return

    if not is_local():
        raise RuntimeError("KNOWLEDGE_QUEUE_URL is not set")

    from slack_app.handlers import knowledge_indexer  # local import: avoid a cycle at module load

    event = {"Records": [{"messageId": f"local-{job.get('trigger_ts') or job['kind']}", "body": json.dumps(job)}]}
    timer = threading.Timer(delay_seconds, knowledge_indexer.handler, args=(event, None))
    timer.daemon = True
    timer.start()
