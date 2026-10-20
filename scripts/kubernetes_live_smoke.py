"""Exercise Mitra's real protocol/client against the independently running lab."""
import argparse
import http.cookiejar
import socket
import json
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--lab-root', required=True, type=Path, help='Trusted k8s-auth-lab checkout; synthetic test users only')
arguments = parser.parse_args()
lab = arguments.lab_root.resolve(strict=True)
app = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(lab / 'scripts'))
sys.path.insert(0, str(app / 'backends/agents/slack_agent/src'))
from common import load_config, STATE, private_write
from verify_access import Callback, Redirect, LoginForm
from k8s_auth.settings import Settings
from k8s_auth.protocol import Protocol
from k8s_access import user_client, bounded_read
from kubernetes.client.exceptions import ApiException

cfg = load_config()
settings = Settings(cluster='local-lab', issuer=cfg['identity_url']+'/pinniped',
    audience=cfg['audience'], concierge=cfg['proxy_url'], ca=(STATE/'ca.crt').read_text(),
    authenticator='lab-jwt', client_id=cfg['client_id'], secret_arn='local-smoke-secret',
    table='unused-local-smoke', redirect_uri=cfg['redirect_uri'],
    issuer_ca=(STATE/'ca.crt').read_text() if cfg['local_identity'] else '')
secret = (STATE/'mitra-client-secret.json').read_text()
passwords = json.loads((STATE/'test-secrets.json').read_text())

class SecretBoundary:
    def get_secret_value(self, **kwargs):
        assert kwargs == {'SecretId': 'local-smoke-secret'}
        return {'SecretString': secret}

class ProviderForm(LoginForm):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attributes):
        super().handle_starttag(tag, attributes)
        if tag == 'a' and dict(attributes).get('href'):
            self.links.append(dict(attributes)['href'])

def browser_code(url, payload, username):
    context = ssl.create_default_context(cadata=settings.issuer_ca) if settings.issuer_ca else ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        Redirect(settings.redirect_uri, cfg['identity_url']))
    with opener.open(url, timeout=30) as response:
        form = ProviderForm()
        form.feed(response.read().decode())
    if not form.action:
        candidates = [urllib.parse.urljoin(url, link) for link in form.links]
        candidates = [link for link in candidates if link.startswith(cfg['identity_url']+'/pinniped/oauth2/authorize?')
            and urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get('pinniped_idp_name') == ['Keycloak Lab']]
        if len(candidates) != 1:
            raise RuntimeError('Expected a single synthetic identity-provider selection')
        with opener.open(candidates[0], timeout=30) as response:
            form = ProviderForm()
            form.feed(response.read().decode())
    if not form.action or not form.action.startswith(cfg['identity_url']+'/realms/lab/'):
        raise RuntimeError('Expected synthetic Keycloak login form')
    data = urllib.parse.urlencode(dict(form.fields, username=username,
        password=passwords[username], credentialId='')).encode()
    try:
        opener.open(urllib.request.Request(form.action, data=data), timeout=30)
    except Callback as callback:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(callback.url).query)
        if query.get('state') != [payload['state']] or 'error' in query:
            raise RuntimeError('Unexpected callback state or error') from None
        return query['code'][0]
    raise RuntimeError('Expected browser callback')

results = []
phase = 'starting'
try:
    with patch('k8s_auth.protocol.boto3.client', return_value=SecretBoundary()):
        for username, allowed_team in [('alice','a'),('bob','b'),('charlie',None)]:
            protocol = Protocol(settings)
            phase = username + ': consent/discovery'
            url, payload = protocol.consent()
            phase = username + ': browser login'
            code = browser_code(url, payload, username)
            phase = username + ': verified code exchange'
            session = protocol.exchange_code(payload, code)
            assert session['username'] == username
            phase = username + ': verified refresh'
            session = protocol.refresh(session)
            phase = username + ': verified token exchange and mTLS issuance'
            credential = protocol.credential(session)
            phase = username + ': Python Kubernetes client RBAC matrix'
            with user_client(settings, credential) as api:
                for team in ('a','b'):
                    ns = 'lab-team-'+team
                    for operation in ('pods','deployments','events'):
                        try:
                            data = bounded_read(api,operation,ns)
                            assert team == allowed_team, 'Unexpected access'
                        except ApiException as error:
                            assert team != allowed_team and error.status == 403
                    if team == allowed_team:
                        pods = bounded_read(api,'pods',ns)['items']
                        logs = bounded_read(api,'logs',ns,pod=pods[0]['name'],tail_lines=5)
                        assert 'synthetic team-'+team in logs['logs']
            def reject_untrusted_tls(url):
                endpoint = urllib.parse.urlsplit(url)
                try:
                    with socket.create_connection((endpoint.hostname, endpoint.port or 443), timeout=10) as connection:
                        with ssl.create_default_context().wrap_socket(connection, server_hostname=endpoint.hostname):
                            pass
                except ssl.SSLCertVerificationError:
                    return
                raise RuntimeError('Untrusted certificate unexpectedly accepted')
            phase = username + ': untrusted issuer CA rejected'
            if cfg['local_identity']:
                reject_untrusted_tls(settings.issuer)
            phase = username + ': untrusted proxy CA rejected'
            reject_untrusted_tls(settings.concierge)
            results.append({'user':username,'real_mitra_protocol':'passed',
                'python_client_namespace_rbac':'passed','untrusted_proxy_rejected':True,
                'untrusted_issuer_rejected':True if cfg['local_identity'] else 'publicly_trusted_issuer'})
            print(json.dumps(results[-1]),flush=True)
    private_write(STATE/'mitra-protocol-acceptance.json',json.dumps(results,indent=2))
except Exception as error:
    # Deliberately exclude exception text and response bodies, which can contain credentials.
    print(json.dumps({'failed_phase':phase,'error_type':type(error).__name__}),flush=True)
    raise SystemExit(1)
