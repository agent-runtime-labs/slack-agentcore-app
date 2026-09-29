"""The S3 Vectors index that holds thread summaries, used directly (no Knowledge Base).

A summary is short and self-contained, so there is nothing to chunk: each thread has one
vector for its summary and up to three for side points, on stable keys (see
knowledge.vector_keys), so updating a thread overwrites it and deleting it is exact.

Filterable metadata: team_id, channel_id, thread_key, visibility, updated_at, status,
schema_version. Non-filterable (declared on the index in Terraform): summary, permalink,
channel_name, participants, last_message_ts.
"""

import os
from collections.abc import Iterator
from functools import lru_cache

import boto3

from slack_app.knowledge import channel_of, vector_keys

# API limits: PutVectors and DeleteVectors take up to 500 vectors, GetVectors 100.
_WRITE_BATCH = 500
_READ_BATCH = 100
_LIST_PAGE = 500


class KnowledgeStore:
    def __init__(self, client, bucket: str, index: str):
        self._client = client
        self._where = {"vectorBucketName": bucket, "indexName": index}

    def get(self, keys: list[str], *, data: bool = False) -> dict[str, dict]:
        """key -> {"metadata": ..., "data": ... if asked} for the keys that exist."""
        found = {}
        for batch in _batches(keys, _READ_BATCH):
            response = self._client.get_vectors(**self._where, keys=batch, returnMetadata=True, returnData=data)
            for vector in response.get("vectors") or []:
                found[vector["key"]] = vector
        return found

    def put(self, vectors: list[dict]) -> None:
        """vectors: {"key", "embedding": [float], "metadata": {...}}. Overwrites existing keys."""
        items = [
            {"key": vector["key"], "data": {"float32": vector["embedding"]}, "metadata": vector["metadata"]}
            for vector in vectors
        ]
        for batch in _batches(items, _WRITE_BATCH):
            self._client.put_vectors(**self._where, vectors=batch)

    def delete(self, keys: list[str]) -> None:
        for batch in _batches(keys, _WRITE_BATCH):
            self._client.delete_vectors(**self._where, keys=batch)

    def list(self, *, metadata: bool = False) -> Iterator[dict]:
        """Every vector in the index ({"key", "metadata"?}), without its embedding."""
        token = None
        while True:
            kwargs = {"nextToken": token} if token else {}
            response = self._client.list_vectors(
                **self._where, maxResults=_LIST_PAGE, returnMetadata=metadata, returnData=False, **kwargs
            )
            yield from response.get("vectors") or []
            token = response.get("nextToken")
            if not token:
                return

    def delete_thread(self, thread_key: str) -> int:
        existing = list(self.get(vector_keys(thread_key)))
        if existing:
            self.delete(existing)
        return len(existing)

    def delete_channel(self, team_id: str, channel: str) -> int:
        keys = [vector["key"] for vector in self.list() if channel_of(vector["key"]) == (team_id, channel)]
        if keys:
            self.delete(keys)
        return len(keys)


def _batches(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


@lru_cache(maxsize=1)
def knowledge_store() -> KnowledgeStore:
    bucket = os.getenv("KNOWLEDGE_VECTOR_BUCKET")
    if not bucket:
        # S3 Vectors has no local emulator: under Tilt, point this at a real dev bucket.
        raise RuntimeError("KNOWLEDGE_VECTOR_BUCKET is not set")
    return KnowledgeStore(boto3.client("s3vectors"), bucket, os.getenv("KNOWLEDGE_INDEX", "threads"))
