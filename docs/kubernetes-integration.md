# Kubernetes access as the Slack requester

Kubernetes tools are disabled by default. The independently managed `k8s-auth-lab`
repository owns Rancher Desktop provisioning, Keycloak, Pinniped, namespaces, RBAC,
workloads and tunnels. This repository only consumes the connection contract and
runs the integration in AWS AgentCore. No Rancher admin kubeconfig is loaded.

The lab must first pass its Alice/Bob/unbound-user `kubectl` access matrix. Use
synthetic workloads and logs: Mitra deliberately posts query results into the shared
requesting Slack thread, whose members can have different Kubernetes permissions.

## Connection contract

Set Terraform variable `kubernetes` to an object with these properties, then build
both application images. Leave the variable `null` to disable the feature.

| Terraform property | Runtime variable | Meaning |
| --- | --- | --- |
| `cluster_id` | `K8S_CLUSTER_ID` | Stable lowercase cluster identifier, e.g. `local-lab` |
| `issuer_url` | `K8S_ISSUER_URL` | Exact HTTPS Supervisor FederationDomain issuer |
| `issuer_ca_b64` | `K8S_ISSUER_CA_B64` | Optional base64 PEM issuer CA; empty uses system trust |
| `audience` | `K8S_AUDIENCE` | Audience on the cluster JWTAuthenticator |
| `concierge_url` | `K8S_CONCIERGE_URL` | HTTPS impersonation proxy URL; non-default ports supported |
| `concierge_ca_b64` | `K8S_CONCIERGE_CA_B64` | Base64 PEM proxy root; empty uses system trust |
| `authenticator_name` | `K8S_AUTHENTICATOR_NAME` | JWTAuthenticator metadata.name |
| `oidc_client_id` | `K8S_OIDC_CLIENT_ID` | Dedicated `client.oauth.pinniped.dev-...` OIDCClient |
| `oidc_secret_arn` | `K8S_OIDC_SECRET_ARN` | Existing Secrets Manager secret with JSON `{"client_secret":"..."}` |

Terraform also supplies `K8S_ENABLED=true`, the encrypted `K8S_TOKEN_TABLE`, and the
existing exact `OAUTH2_RETURN_URL`. Populate secret values out of band; never put
client secrets, user tokens, or passwords into Terraform state or tfvars.
Changing the issuer, audience, client, endpoint, redirect URI or CA invalidates
previous connections. No live AWS deployment is performed by adding this code.

For a new deployment, first deploy with `kubernetes = null`, then read the
`kubernetes_callback_url` output and register
that exact value in the lab. The lab's loopback callback is only for local protocol
tests and must not be used for AWS. Enable the feature only after the callback,
public tunnel endpoints, and secret have been configured. Supply the connection
settings through a separate local variable file and pass it explicitly to both
Terraform plan and apply:

```sh
AWS_PROFILE=personal ./infra-as-code/tf-wrapper.sh dev plan \
  -var-file=/absolute/path/to/mitra-kubernetes.tfvars.json
```

Replace `plan` with `apply` after reviewing the plan. The variable file contains
connection metadata and a Secrets Manager ARN; the client secret stays in Secrets
Manager. Keep environment-specific tunnel URLs and CA data out of shared defaults.

Register the callback URL from `kubernetes_callback_url` on the lab's dedicated
Pinniped OIDCClient. Allow authorization_code, refresh_token and
`urn:ietf:params:oauth:grant-type:token-exchange`, and the scopes `openid`,
`offline_access`, `username`, `groups`, `pinniped:request-audience`.
Enable Slack Interactivity and set its request URL to the `slack_interactions_url`
Terraform output (`POST /slack/interactions`). Browser OAuth routes stay unchanged.
The sample Slack manifest contains a placeholder interaction URL to replace.

## Login and request lifecycle

1. A signed Slack event identifies the workspace and user. The IAM-only runtime
   receives that derived identity; the model never supplies an identity or token.
2. A Kubernetes tool without a connection sends a private ten-minute link through
   the existing pending-auth flow. The dedicated Pinniped confidential client uses
   state, OIDC nonce, PKCE S256, and client_secret_basic.
3. The callback validates state/cookie, issuer, ID-token signature/audience/expiry
   and nonce, consumes the link, and stores **staged** credentials for ten minutes.
   It does not activate credentials or resume the query.
4. The original requester receives a private button showing the IdP username.
   Only a Slack-signed action from the same workspace/user atomically activates
   that staged connection. Forwarding the browser link cannot activate it.
5. Confirmation resumes the original question in its shared thread. Connections
   are keyed by workspace/user and configured cluster, independently of threads.
6. Every read refreshes the Supervisor identity, persists the newly rotated refresh
   token, exchanges the opaque access token for a cluster JWT, then requests a
   short-lived Concierge credential. RBAC at Kubernetes decides access.

Refresh uses a durable version and owner lock, including across runtime containers.
A competing request asks the user to retry. An abandoned refresh is never retried
with the same token after its lock expires; a fresh browser login is required.
A newly confirmed identity fences any old in-flight refresh. Active connections
expire after a day of inactivity; pending rows and credentials have DynamoDB TTLs.

The upstream token-exchange implementation returns the JWT in RFC8693's
`access_token` field with `issued_token_type=urn:ietf:params:oauth:token-type:jwt`
and `token_type=N_A` (some prose documentation incorrectly says `id_token`). The
client verifies its signature, issuer, audience and original subject before sending
it to the Concierge TokenCredentialRequest API.

Pinniped v0.47.0 signs tokens without a `kid` header. Such tokens are accepted only
when the trusted issuer publishes exactly one compatible signing key; signatures
are always verified, and unknown supplied or ambiguous keys fail. After audience
exchange, `aud` identifies the cluster while `azp` still identifies Mitra's OIDC
client. Both are checked separately.

## TLS and operation boundaries

Current Pinniped returns a short-lived **mTLS client certificate/key**, not a bearer
credential. The Python Kubernetes client uses private per-request files (directory
0700, files 0600), removes them on exit, and never mutates global client defaults.
Future bearer responses are supported too. The client validates server certificates,
uses the configured CA, and never falls back to an in-cluster service account.

The cluster tunnel must preserve TLS end to end, such as ngrok **TCP passthrough**
with a Concierge server certificate matching the external hostname. A normal ngrok
HTTPS endpoint terminates TLS at the edge and cannot preserve mTLS client identity.
Identity endpoints can use normal HTTPS tunnels. Do not expose the direct Kubernetes
API or Keycloak administration routes. Both legs need valid TLS and hostname checks;
no `insecure-skip-tls-verify` workaround is supported.

The sole model-facing tool permits namespace-scoped pod, deployment and event lists
(up to 50 records), and non-streaming pod logs (1–200 lines, at most 16,000 bytes).
Pod/deployment outputs project only a small status field allowlist. There is no shell,
arbitrary API path, kubeconfig exec, secrets tool, write, exec or all-namespaces read.
A 403 is an RBAC denial. A 401 requires a fresh login. Tunnel/TLS errors expose only a
safe connection error; remote response bodies and tokens never enter model context
or logs. Login and confirmation content stays ephemeral/private, including dry runs.

## Validation

Run `make test`. Kubernetes tests cover real signed JWT validation, PKCE/nonce/state,
issuer/audience/subject checks, credential exchange shape, atomic DynamoDB activation,
refresh fencing, request identity isolation, signed Slack ownership confirmation,
403/401 behavior, log bounds and mTLS file cleanup. Protocol/store source is mirrored
between the two independent Docker build contexts; a parity test prevents drift.

A live acceptance pass must also test Alice/Bob allowed and forbidden namespaces,
an unbound user, expiration, changed group membership, two users in one thread,
a stopped tunnel, and an invalid CA. Unit tests do not establish internal EKS or
corporate Azure AD compatibility. Those remain later integration stages.

To repeat the actual protocol/client check against a trusted local lab checkout:

```sh
uv run --no-project --python 3.13 \
  --with-requirements backends/agents/slack_agent/requirements.txt \
  python scripts/kubernetes_live_smoke.py --lab-root ../k8s-auth-lab
```

This uses the synthetic lab's browser login, issuer/JWKS, refresh and cluster-token
exchange, mTLS proxy and Kubernetes API. Only the Secrets Manager lookup is replaced
with the lab's private client-secret file. It checks all three users, namespace
denials, bounded logs and rejection of an untrusted proxy certificate; it writes
non-secret results to the lab's `.state/mitra-protocol-acceptance.json`. It does not
deploy to AWS or send Slack messages. Its browser helper intercepts the redirect
before visiting the configured callback, so it does not validate cloud callback
handling or Slack delivery.

Validated on 2026-10-20: 466 app regression tests passed, Terraform validation and
formatting passed with its backend disabled, and the actual client passed against
Rancher Desktop both locally and over ngrok HTTPS/TCP. Alice reads only team A,
Bob only team B, and Charlie neither. The standalone lab's 22 tests and real
three-user kubectl matrix also passed. The live script substitutes only Secrets
Manager. The deployed Slack flow was subsequently confirmed working by the operator;
repeat the namespace matrix through separate Slack accounts after deployment.

The runtime IAM module also has mocked Terraform regression tests:

```sh
terraform -chdir=infra-as-code/tf-modules/aws/agentcore-runtime init -backend=false
terraform -chdir=infra-as-code/tf-modules/aws/agentcore-runtime test
```

These tests plan with a mocked AWS provider and create no AWS resources. They verify
that additional IAM statements retain optional fields and scalar/list resources,
that an empty extension works, and that non-sequence input is rejected.

References: [Pinniped web applications](https://pinniped.dev/docs/howto/configure-auth-for-webapps/),
[Pinniped API](https://github.com/vmware/pinniped/blob/main/generated/latest/README.adoc),
[Supervisor token-exchange implementation](https://github.com/vmware/pinniped/blob/main/internal/federationdomain/endpoints/tokenexchange/token_exchange.go).
