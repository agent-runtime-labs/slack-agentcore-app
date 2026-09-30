"""Titan Text Embeddings v2: 1024 dimensions, normalised, to match the index (cosine distance).

The agent embeds search queries with the same model (past_threads.py in the agent).
"""

import json
import os
from functools import lru_cache

import boto3
from botocore.config import Config

DEFAULT_MODEL_ID = "amazon.titan-embed-text-v2:0"
DIMENSIONS = 1024
# Titan v2 takes up to 8,192 tokens. A summary is far shorter; this only guards the call.
MAX_CHARS = 20_000


def embedding_model_id() -> str:
    return os.getenv("KNOWLEDGE_EMBEDDING_MODEL_ID", DEFAULT_MODEL_ID)


@lru_cache(maxsize=1)
def _bedrock():
    return boto3.client("bedrock-runtime", config=Config(read_timeout=20, retries={"total_max_attempts": 3}))


def embed(text: str) -> list[float]:
    response = _bedrock().invoke_model(
        modelId=embedding_model_id(),
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": text[:MAX_CHARS], "dimensions": DIMENSIONS, "normalize": True}),
    )
    return json.loads(response["body"].read())["embedding"]
