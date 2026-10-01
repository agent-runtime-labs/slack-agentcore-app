"""In-memory stand-in for the boto3 s3vectors client (S3 Vectors has no local emulator)."""

import copy
import hashlib
import math
import re


class FakeS3Vectors:
    def __init__(self):
        self.vectors: dict[str, dict] = {}
        self.calls: list[str] = []

    def put_vectors(self, vectorBucketName, indexName, vectors):  # noqa: N803 - mirrors boto3
        self.calls.append("put")
        for vector in vectors:
            self.vectors[vector["key"]] = copy.deepcopy(vector)
        return {}

    def get_vectors(self, vectorBucketName, indexName, keys, returnMetadata=False, returnData=False):  # noqa: N803
        self.calls.append("get")
        return {"vectors": [self._view(self.vectors[key], returnMetadata, returnData) for key in keys if key in self.vectors]}

    def delete_vectors(self, vectorBucketName, indexName, keys):  # noqa: N803
        self.calls.append("delete")
        for key in keys:
            self.vectors.pop(key, None)
        return {}

    def list_vectors(  # noqa: N803
        self, vectorBucketName, indexName, maxResults, returnMetadata=False, returnData=False, nextToken=None
    ):
        self.calls.append("list")
        keys = sorted(self.vectors)
        start = int(nextToken or 0)
        page = keys[start : start + maxResults]
        response = {"vectors": [self._view(self.vectors[key], returnMetadata, returnData) for key in page]}
        if start + maxResults < len(keys):
            response["nextToken"] = str(start + maxResults)
        return response

    def query_vectors(  # noqa: N803
        self, vectorBucketName, indexName, topK, queryVector, filter=None, returnMetadata=False, returnDistance=False  # noqa: A002
    ):
        self.calls.append("query")
        query = queryVector["float32"]
        hits = [
            (1 - _cosine(query, vector["data"]["float32"]), vector)
            for vector in self.vectors.values()
            if filter is None or matches(vector["metadata"], filter)
        ]
        hits.sort(key=lambda hit: hit[0])
        out = []
        for distance, vector in hits[:topK]:
            item = {"key": vector["key"]}
            if returnMetadata:
                item["metadata"] = copy.deepcopy(vector["metadata"])
            if returnDistance:
                item["distance"] = distance
            out.append(item)
        return {"vectors": out, "distanceMetric": "cosine"}

    @staticmethod
    def _view(vector, metadata, data):
        item = {"key": vector["key"]}
        if metadata:
            item["metadata"] = copy.deepcopy(vector["metadata"])
        if data:
            item["data"] = copy.deepcopy(vector["data"])
        return item


def matches(metadata: dict, condition: dict) -> bool:
    """S3 Vectors' metadata filter language: $and, $or, $eq, $ne, $in, $nin, $exists."""
    for field, test in condition.items():
        if field == "$and":
            if not all(matches(metadata, part) for part in test):
                return False
        elif field == "$or":
            if not any(matches(metadata, part) for part in test):
                return False
        else:
            if not isinstance(test, dict):
                test = {"$eq": test}
            if not all(_holds(operator, operand, metadata, field) for operator, operand in test.items()):
                return False
    return True


def _holds(operator: str, operand, metadata: dict, field: str) -> bool:
    value = metadata.get(field)
    if operator == "$eq":
        return value == operand
    if operator == "$ne":
        return value != operand
    if operator == "$in":
        return value in operand
    if operator == "$nin":
        return value not in operand
    if operator == "$exists":
        return (field in metadata) == operand
    raise ValueError(f"unsupported operator {operator}")


def fake_embedding(text: str, dimensions: int = 256) -> list[float]:
    """Bag of words, hashed: texts sharing words are close, as with a real embedding model."""
    vector = [0.0] * dimensions
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % dimensions] += 1
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norms = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norms if norms else 0.0
