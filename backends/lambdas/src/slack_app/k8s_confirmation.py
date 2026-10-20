"""Handle verified Slack button actions without involving the model.

Caller MUST verify the raw Slack request signature before dispatching here.
"""

import json
import logging
import re
from urllib.parse import parse_qs

from slack_app.apigw import json_response
from slack_app.identity import runtime_user_id
from slack_app.k8s_auth.settings import Settings
from slack_app.k8s_auth.store import Store, AWS_TIMEOUTS
from slack_app.work_queue import RESUMED_AFTER_AUTH, enqueue

logger = logging.getLogger(__name__)


def confirm(body):
    result = "This confirmation has expired or does not belong to you. Ask Mitra for a new login link."
    try:
        payload = json.loads(parse_qs(body, max_num_fields=10).get("payload", [""])[0])
        actions = payload.get("actions", [])
        settings = Settings.load()
        if settings and payload.get("type") == "block_actions" and len(actions) == 1:
            action = actions[0]
            code = action.get("value", "")
            team, user = payload.get("team", {}).get("id"), payload.get("user", {}).get("id")
            if (action.get("action_id") == "k8s_confirm" and re.fullmatch(r"[A-Za-z0-9_-]{43}", code)
                    and team and user):
                row = Store(settings).activate(runtime_user_id(team, user), code)
                if row:
                    result = "Kubernetes identity connected. Ask your question again."
                    if row.get("resume_job"):
                        job = {**json.loads(row["resume_job"]), RESUMED_AFTER_AUTH: True}
                        # Reject any corrupted/mismatched resume job before enqueue.
                        if job.get("team_id") == team and job.get("user") == user:
                            enqueue(job, group_id=f"{job['channel']}-{job['thread_ts']}", dedup_id=f"k8s-{code}", sdk_config=AWS_TIMEOUTS)
                            result = "Kubernetes identity connected. Mitra is answering your question in the original thread."
    except Exception:
        logger.warning("Kubernetes account confirmation failed")
    return json_response(200, {"response_type": "ephemeral", "replace_original": True, "text": result})
