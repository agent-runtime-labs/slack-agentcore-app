"""Pinniped confidential OIDC client, RFC8693, and Concierge credential exchange.

Reference: https://pinniped.dev/docs/howto/configure-auth-for-webapps/
All exceptions crossing this boundary omit remote response bodies and credentials.
"""

import base64
import hashlib
import hmac
import json
import secrets
import ssl
import time
from datetime import datetime
from urllib.parse import urlencode, urlsplit

import boto3
import httpx
import jwt
from botocore.config import Config

from .settings import AuthFailure

SCOPES = "openid offline_access username groups pinniped:request-audience"
MAX_RESPONSE = 1024 * 1024


class Protocol:
    def __init__(self, settings):
        self.settings = settings
        self._metadata = None

    def _request(self, method, url, *, cluster=False, **kwargs):
        verify = ssl.create_default_context()
        ca = self.settings.ca if cluster else self.settings.issuer_ca
        if ca:
            verify.load_verify_locations(cadata=ca)
        try:
            with httpx.Client(verify=verify, timeout=10, follow_redirects=False, trust_env=False) as client:
                with client.stream(method, url, **kwargs) as response:
                    if response.status_code not in (200, 201):
                        raise AuthFailure()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE:
                            raise AuthFailure()
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise AuthFailure()
                    return result
        except Exception:
            raise AuthFailure() from None

    def discover(self):
        if self._metadata is not None:
            return self._metadata
        metadata = self._request("GET", self.settings.issuer + "/.well-known/openid-configuration")
        if metadata.get("issuer") != self.settings.issuer:
            raise AuthFailure()
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = urlsplit(metadata.get(key, ""))
            expected = urlsplit(self.settings.issuer)
            if (value.scheme, value.netloc) != (expected.scheme, expected.netloc) or value.fragment or value.username:
                raise AuthFailure()
        self._metadata = metadata
        return metadata

    def consent(self):
        metadata = self.discover()
        verifier, state, nonce = [secrets.token_urlsafe(32) for _ in range(3)]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = dict(response_type="code", response_mode="query", client_id=self.settings.client_id,
                      redirect_uri=self.settings.redirect_uri, scope=SCOPES, state=state, nonce=nonce,
                      code_challenge=challenge, code_challenge_method="S256")
        return metadata["authorization_endpoint"] + "?" + urlencode(params), {
            "state": state, "nonce": nonce, "code_verifier": verifier,
            "fingerprint": self.settings.fingerprint,
        }

    def _token(self, fields):
        try:
            secret = json.loads(boto3.client("secretsmanager", config=Config(connect_timeout=2, read_timeout=3,
                retries={"total_max_attempts": 1})).get_secret_value(
                SecretId=self.settings.secret_arn)["SecretString"])["client_secret"]
            if not isinstance(secret, str) or not secret:
                raise AuthFailure()
            return self._request("POST", self.discover()["token_endpoint"], data=fields,
                                 auth=(self.settings.client_id, secret))
        except Exception:
            raise AuthFailure() from None

    def verify_id(self, token, audience, *, nonce=None, subject=None, access_token=None):
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ("ES256", "RS256"):
                raise AuthFailure()
            keys = self._request("GET", self.discover()["jwks_uri"])["keys"]
            kid = header.get("kid")
            if "kid" in header and (not isinstance(kid, str) or not kid):
                raise AuthFailure()
            # Pinniped 0.47 omits kid on signed ID tokens. Only one compatible
            # issuer key may be selected then; a supplied unknown kid never falls back.
            matching = [key for key in keys if (kid is None or key.get("kid") == kid)
                        and key.get("use", "sig") == "sig" and key.get("alg", header["alg"]) == header["alg"]]
            if len(matching) != 1:
                raise AuthFailure()
            key = jwt.PyJWK.from_dict(matching[0])
            if key.algorithm_name != header["alg"]:
                raise AuthFailure()
            claims = jwt.decode(token, key.key,
                                algorithms=[header["alg"]], audience=audience, issuer=self.settings.issuer,
                                options={"require": ["iss", "sub", "aud", "exp", "iat"]}, leeway=5)
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise AuthFailure()
            if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
                raise AuthFailure()
            if subject is not None and claims["sub"] != subject:
                raise AuthFailure()
            # The authorized party remains this OIDC client after audience
            # exchange; the cluster is the recipient (aud), not the client (azp).
            if "azp" in claims and claims["azp"] != self.settings.client_id:
                raise AuthFailure()
            if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and "azp" not in claims:
                raise AuthFailure()
            if access_token and "at_hash" in claims:
                expected = base64.urlsafe_b64encode(hashlib.sha256(access_token.encode()).digest()[:16]).rstrip(b"=").decode()
                if not hmac.compare_digest(expected, claims["at_hash"]):
                    raise AuthFailure()
            return claims
        except Exception:
            raise AuthFailure() from None

    def _session(self, response, **validation):
        try:
            if response.get("token_type", "").lower() != "bearer":
                raise AuthFailure()
            if not all(isinstance(response.get(k), str) and response[k] for k in ("access_token", "refresh_token", "id_token")):
                raise AuthFailure()
            if "scope" in response and not set(SCOPES.split()).issubset(set(response["scope"].split())):
                raise AuthFailure()
            claims = self.verify_id(response["id_token"], self.settings.client_id,
                                    access_token=response["access_token"], **validation)
            expires = min(int(claims["exp"]), int(time.time()) + int(response["expires_in"]))
            if expires <= time.time() + 5:
                raise AuthFailure()
            return dict(access_token=response["access_token"], refresh_token=response["refresh_token"],
                        expires_at=expires, subject=claims["sub"], username=str(claims.get("username", claims["sub"]))[:256])
        except Exception:
            raise AuthFailure() from None

    def exchange_code(self, payload, code):
        if payload.get("fingerprint") != self.settings.fingerprint:
            raise AuthFailure()
        response = self._token(dict(grant_type="authorization_code", code=code,
                                    redirect_uri=self.settings.redirect_uri, code_verifier=payload["code_verifier"]))
        return self._session(response, nonce=payload["nonce"])

    def refresh(self, record):
        response = self._token(dict(grant_type="refresh_token", refresh_token=record["refresh_token"]))
        return self._session(response, subject=record["subject"])

    def credential(self, record):
        response = self._token(dict(grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
            subject_token=record["access_token"], subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            requested_token_type="urn:ietf:params:oauth:token-type:jwt", audience=self.settings.audience))
        # Actual Supervisor implements RFC8693's access_token response field.
        if (response.get("issued_token_type") != "urn:ietf:params:oauth:token-type:jwt"
                or response.get("token_type") != "N_A"):
            raise AuthFailure()
        cluster_token = response.get("access_token", "")
        self.verify_id(cluster_token, self.settings.audience, subject=record["subject"])
        result = self._request("POST", self.settings.concierge + "/apis/login.concierge.pinniped.dev/v1alpha1/tokencredentialrequests",
            cluster=True, json={"apiVersion": "login.concierge.pinniped.dev/v1alpha1", "kind": "TokenCredentialRequest",
                "spec": {"token": cluster_token, "authenticator": {"apiGroup": "authentication.concierge.pinniped.dev",
                    "kind": "JWTAuthenticator", "name": self.settings.authenticator}}})
        credential = result.get("status", {}).get("credential", {})
        try:
            expires = datetime.fromisoformat(credential["expirationTimestamp"].replace("Z", "+00:00")).timestamp()
            if expires <= time.time() + 5:
                raise AuthFailure()
            if not credential.get("token") and not (credential.get("clientCertificateData") and credential.get("clientKeyData")):
                raise AuthFailure()
            return credential
        except Exception:
            raise AuthFailure() from None
