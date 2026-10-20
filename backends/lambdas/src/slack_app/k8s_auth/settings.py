"""Administrator-controlled cluster contract. No kubeconfig execution or ambient credentials."""

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


class AuthFailure(Exception):
    """A safe, deliberately detail-free authentication failure."""


class Busy(Exception):
    """Another request owns the rotating refresh token."""


def https(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Kubernetes authentication endpoints must be configured HTTPS URLs")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    cluster: str
    issuer: str
    audience: str
    concierge: str
    ca: str
    authenticator: str
    client_id: str
    secret_arn: str
    table: str
    redirect_uri: str
    issuer_ca: str = ""

    @classmethod
    def load(cls):
        if os.getenv("K8S_ENABLED", "false").lower() != "true":
            return None
        values = {name: os.environ[f"K8S_{key}"] for name, key in {
            "cluster": "CLUSTER_ID", "issuer": "ISSUER_URL", "audience": "AUDIENCE",
            "concierge": "CONCIERGE_URL", "authenticator": "AUTHENTICATOR_NAME",
            "client_id": "OIDC_CLIENT_ID", "secret_arn": "OIDC_SECRET_ARN", "table": "TOKEN_TABLE",
        }.items()}
        if not all(values.values()) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", values["cluster"]):
            raise ValueError("Incomplete Kubernetes integration configuration")
        values["issuer"] = https(values["issuer"])
        values["concierge"] = https(values["concierge"])
        ca = base64.b64decode(os.getenv("K8S_CONCIERGE_CA_B64", ""), validate=True).decode("ascii")
        if ca and "-----BEGIN CERTIFICATE-----" not in ca:
            raise ValueError("Kubernetes CA must be a PEM certificate bundle")
        issuer_ca = base64.b64decode(os.getenv("K8S_ISSUER_CA_B64", ""), validate=True).decode("ascii")
        if issuer_ca and "-----BEGIN CERTIFICATE-----" not in issuer_ca:
            raise ValueError("Issuer CA must be a PEM certificate bundle")
        return cls(**values, ca=ca, issuer_ca=issuer_ca, redirect_uri=https(os.environ["OAUTH2_RETURN_URL"]))

    @property
    def fingerprint(self) -> str:
        fields = [self.cluster, self.issuer, self.audience, self.concierge, self.ca,
                  self.authenticator, self.client_id, self.redirect_uri, self.issuer_ca]
        return hashlib.sha256(json.dumps(fields).encode()).hexdigest()
