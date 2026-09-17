"""Slack <-> Amazon Bedrock AgentCore integration (Lambda handlers + local server)."""

import logging
import os

logging.getLogger().setLevel(os.getenv("LOG_LEVEL", "INFO"))
if not logging.getLogger().handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
