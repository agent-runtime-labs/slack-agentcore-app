"""The worker's timeouts live in three files and must stay in step.

    agent_client.TIMEOUT_SECONDS  <  agent_worker_fn timeout  <=  queue visibility / 6

If the agent call's read timeout is not below the worker Lambda's timeout, the Lambda
can be killed first and the user's placeholder never gets an answer. (This is a
floor, not a full budget: the read timeout is per socket read, and the worker also
triages and posts to Slack, so keep headroom between the two.)

If the queue's visibility timeout is under six times the worker timeout (AWS's guidance
for SQS event sources), a message still being worked on can be delivered again and
answered twice. Nothing here calls AWS: the values are read from the committed
Terraform and Python source.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF_APP = ROOT / "infra-as-code" / "tf-app"
AGENT_CLIENT = ROOT / "backends" / "lambdas" / "src" / "slack_app" / "agent_client.py"


def _block(text: str, header: str) -> str:
    """Body of the top-level HCL block that starts with `header {` (up to the closing `}` at column 0)."""
    match = re.search(re.escape(header) + r"\s*\{\n(.*?)^\}", text, re.S | re.M)
    assert match, f"no `{header}` block"
    return match.group(1)


def _number(block: str, name: str) -> int:
    match = re.search(rf"^\s*{name}\s*=\s*(\d+)\s*$", block, re.M)
    assert match, f"no literal `{name} = <number>`"
    return int(match.group(1))


def worker_timeout() -> int:
    lambdas = (TF_APP / "lambdas.tf").read_text()
    return _number(_block(lambdas, 'module "agent_worker_fn"'), "timeout")


def queue_visibility_timeout() -> int:
    stores = (TF_APP / "data-stores.tf").read_text()
    return _number(_block(stores, 'resource "aws_sqs_queue" "processing"'), "visibility_timeout_seconds")


def agent_call_timeout() -> int:
    for node in ast.parse(AGENT_CLIENT.read_text()).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(getattr(t, "id", None) == "TIMEOUT_SECONDS" for t in targets):
            return ast.literal_eval(node.value)
    raise AssertionError("no TIMEOUT_SECONDS in agent_client.py")


def test_the_worker_is_the_queues_consumer():
    # Guards the pairing below: if the event source moves to another function, re-point this test.
    lambdas = (TF_APP / "lambdas.tf").read_text()
    mapping = _block(lambdas, 'resource "aws_lambda_event_source_mapping" "agent_worker"')
    assert "aws_sqs_queue.processing.arn" in mapping
    assert "module.agent_worker_fn.function_arn" in mapping


def test_queue_visibility_is_at_least_six_worker_timeouts():
    assert queue_visibility_timeout() >= 6 * worker_timeout()


def test_agent_read_timeout_is_below_the_worker_lambda_timeout():
    assert agent_call_timeout() < worker_timeout()
