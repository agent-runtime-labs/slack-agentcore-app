"""Encrypted DynamoDB vault with atomic account confirmation and refresh fencing.

Rows: (user_id = verified slack-workspace-user, cluster = configured cluster or
cluster#pending#random-code). No process-global credential cache.
"""

import secrets
import time

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from botocore.config import Config

# Signed Slack buttons must acknowledge within three seconds. No SDK retry loop.
AWS_TIMEOUTS = Config(connect_timeout=0.3, read_timeout=0.4, retries={"total_max_attempts": 1})

from .settings import AuthFailure, Busy


def conditional(error):
    return error.response["Error"]["Code"] in ("ConditionalCheckFailedException", "TransactionCanceledException")


class Store:
    def __init__(self, settings):
        self.settings = settings
        self.table = boto3.resource("dynamodb", config=AWS_TIMEOUTS).Table(settings.table)

    def get(self, user_id):
        record = self.table.get_item(Key={"user_id": user_id, "cluster": self.settings.cluster},
                                     ConsistentRead=True).get("Item")
        if (not record or record.get("fingerprint") != self.settings.fingerprint
                or int(record.get("ttl", 0)) <= time.time()):
            return None
        return record

    def stage(self, user_id, session, resume_job):
        code = secrets.token_urlsafe(32)
        item = dict(session, user_id=user_id, cluster=self.settings.cluster + "#pending#" + code,
                    fingerprint=self.settings.fingerprint, ttl=int(time.time()) + 600, resume_job=resume_job,
                    version=secrets.token_hex(16))
        self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(user_id)")
        return code

    def activate(self, user_id, code):
        key = {"user_id": user_id, "cluster": self.settings.cluster + "#pending#" + code}
        row = self.table.get_item(Key=key, ConsistentRead=True).get("Item")
        if not row or int(row["ttl"]) <= time.time() or row["fingerprint"] != self.settings.fingerprint:
            return None
        active = {k: v for k, v in row.items() if k != "resume_job"}
        active.update(cluster=self.settings.cluster, ttl=int(time.time()) + 86400)
        serializer = TypeSerializer()
        encode = lambda item: {k: serializer.serialize(v) for k, v in item.items()}
        try:
            boto3.client("dynamodb", config=AWS_TIMEOUTS).transact_write_items(TransactItems=[
                {"Put": {"TableName": self.settings.table, "Item": encode(active)}},
                {"Delete": {"TableName": self.settings.table, "Key": encode(key),
                    "ConditionExpression": "attribute_exists(user_id) AND #ttl > :now AND #version = :version",
                    "ExpressionAttributeNames": {"#ttl": "ttl", "#version": "version"},
                    "ExpressionAttributeValues": encode({":now": int(time.time()), ":version": row["version"]})}},
            ])
        except ClientError as error:
            if conditional(error):
                return None
            raise
        return row

    def lock(self, user_id, row):
        owner = secrets.token_hex(16)
        try:
            self.table.update_item(Key={"user_id": user_id, "cluster": self.settings.cluster},
                UpdateExpression="SET lock_owner = :owner, lock_until = :until",
                ConditionExpression="#version = :version AND attribute_not_exists(lock_until)",
                ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues={":owner": owner, ":until": int(time.time()) + 90,
                                           ":version": row["version"]})
        except ClientError as error:
            if conditional(error):
                raise Busy() from None
            raise
        return owner

    def save_refresh(self, user_id, row, owner, session):
        updated = dict(session, user_id=user_id, cluster=self.settings.cluster, fingerprint=self.settings.fingerprint,
                       ttl=int(time.time()) + 86400, version=secrets.token_hex(16))
        try:
            self.table.put_item(Item=updated, ConditionExpression="#version = :version AND lock_owner = :owner",
                ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues={":version": row["version"], ":owner": owner})
        except ClientError as error:
            if conditional(error):
                raise AuthFailure() from None
            raise
        return updated

    def invalidate(self, user_id, row, owner=None):
        condition = "#version = :version"
        values = {":version": row["version"]}
        if owner:
            condition += " AND lock_owner = :owner"
            values[":owner"] = owner
        try:
            self.table.delete_item(Key={"user_id": user_id, "cluster": self.settings.cluster},
                ConditionExpression=condition, ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues=values)
        except ClientError as error:
            if not conditional(error):
                raise
