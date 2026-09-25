# -*- mode: Python -*-
# Local development: agent + Slack handlers on a local Kubernetes cluster.
#   tilt up        -> http://localhost:10350
load('ext://namespace', 'namespace_create', 'namespace_inject')
load('ext://dotenv', 'dotenv')

allow_k8s_contexts(['rancher-desktop', 'docker-desktop', 'orbstack', 'kind-kind', 'minikube'])

if os.path.exists('.env'):
    dotenv('.env')

NAMESPACE = 'slack-agentcore'
AWS_PROFILE = os.getenv('AWS_PROFILE', '')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
SLACK_DRY_RUN = os.getenv('SLACK_DRY_RUN', 'true')
LOCAL_BASE_URL = 'http://localhost:8081'
# CIMD authorization servers fetch our client metadata document themselves, so testing
# that flow locally needs a public https URL -- point PUBLIC_BASE_URL at your ngrok
# tunnel (and re-run `make identity-setup` so the workload identity allows it).
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', LOCAL_BASE_URL)

namespace_create(NAMESPACE)

# ---------------------------------------------------------------------------
# Config and secrets
# ---------------------------------------------------------------------------
def k8s_object(kind, name, data):
    body = {'apiVersion': 'v1', 'kind': kind, 'metadata': {'name': name, 'namespace': NAMESPACE}}
    body['data' if kind == 'ConfigMap' else 'stringData'] = data
    k8s_yaml(encode_yaml(body))

k8s_object('ConfigMap', 'app-config', {
    'APP_ENV': 'local',
    'LOG_LEVEL': os.getenv('LOG_LEVEL', 'INFO'),
    'AWS_REGION': AWS_REGION,
    'AWS_DEFAULT_REGION': AWS_REGION,
    'MODEL_ID': os.getenv('MODEL_ID', 'us.anthropic.claude-haiku-4-5-20251001-v1:0'),
    'LINKEDIN_PROVIDER_NAME': os.getenv('LINKEDIN_PROVIDER_NAME', 'slack-agent-linkedin'),
    'GITHUB_PROVIDER_NAME': os.getenv('GITHUB_PROVIDER_NAME', 'slack-agent-github'),
    'LOCAL_WORKLOAD_NAME': os.getenv('LOCAL_WORKLOAD_NAME', 'slack-agent-local'),
    'OAUTH2_RETURN_URL': PUBLIC_BASE_URL + '/oauth2/callback',
    # The browser doing consent runs on this machine, so localhost works even
    # when Slack events arrive through an ngrok tunnel.
    'PUBLIC_BASE_URL': PUBLIC_BASE_URL,
    # CIMD: empty CIMD_TOKEN_TABLE disables the CIMD tools (see cimd/providers.py).
    'CIMD_PROVIDERS': os.getenv('CIMD_PROVIDERS', 'linear,notion'),
    'CIMD_TOKEN_TABLE': os.getenv('CIMD_TOKEN_TABLE', ''),
    'CIMD_CLIENT_ID': PUBLIC_BASE_URL + '/oauth2/client-metadata.json',
    'CIMD_MODEL_ID': os.getenv('CIMD_MODEL_ID', 'us.anthropic.claude-haiku-4-5-20251001-v1:0'),
    'COOKIE_SECURE': 'false',
    'SLACK_DRY_RUN': SLACK_DRY_RUN,
    'OTEL_SDK_DISABLED': 'true',
})

# Short-lived credentials from your AWS profile (never your raw key files).
profile_flag = ' --profile ' + AWS_PROFILE if AWS_PROFILE else ''
aws_env = str(local('aws configure export-credentials --format env-no-export' + profile_flag, quiet=True, echo_off=True))
aws_creds = {}
for line in aws_env.strip().splitlines():
    key, _, value = line.partition('=')
    if key.startswith('AWS_'):
        aws_creds[key] = value
k8s_object('Secret', 'aws-credentials', aws_creds)

k8s_object('Secret', 'slack-credentials', {
    'SLACK_BOT_TOKEN': os.getenv('SLACK_BOT_TOKEN', ''),
    'SLACK_SIGNING_SECRET': os.getenv('SLACK_SIGNING_SECRET', 'local-dev-signing-secret'),
})

k8s_yaml(namespace_inject(kustomize('./infra-as-code/k8s/tilt'), NAMESPACE))

# ---------------------------------------------------------------------------
# Images with live update
# ---------------------------------------------------------------------------
docker_build(
    'slack-agent',
    './backends/agents/slack_agent',
    target='localdev',
    ignore=['tests', '.venv', '.pytest_cache'],
    live_update=[
        fall_back_on(['./backends/agents/slack_agent/requirements.txt']),
        sync('./backends/agents/slack_agent/src', '/app/src'),
    ],
)

docker_build(
    'slack-app',
    './backends/lambdas',
    target='localdev',
    ignore=['tests', '.venv', '.pytest_cache'],
    live_update=[
        fall_back_on(['./backends/lambdas/requirements.txt', './backends/lambdas/requirements-local.txt']),
        sync('./backends/lambdas/src', '/app/src'),
    ],
)

k8s_resource('slack-agent', port_forwards='8080:8080', labels=['backend'],
             objects=['app-config:configmap', 'aws-credentials:secret'])
k8s_resource('slack-app', port_forwards='8081:8081', labels=['backend'], resource_deps=['slack-agent'],
             objects=['slack-credentials:secret'])

# ---------------------------------------------------------------------------
# Tests and helpers (buttons in the Tilt UI)
# ---------------------------------------------------------------------------
UV = 'uv run --quiet --no-project --python 3.13 '

local_resource(
    'unit-tests',
    cmd=UV + '--with-requirements backends/lambdas/requirements-dev.txt pytest -q backends/lambdas/tests && ' +
        UV + '--with-requirements backends/agents/slack_agent/requirements-dev.txt pytest -q backends/agents/slack_agent/tests',
    deps=['backends/lambdas/src', 'backends/lambdas/tests', 'backends/agents/slack_agent/src', 'backends/agents/slack_agent/tests'],
    labels=['test'],
)

local_resource(
    'send-test-mention',
    cmd=UV + 'python scripts/send_test_event.py --text "What is my LinkedIn name?"',
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    resource_deps=['slack-app'],
    labels=['test'],
)

local_resource(
    'ngrok-tunnel',
    serve_cmd='ngrok http 8081 --log=stdout',
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=['tunnel'],
    links=[link('http://localhost:4040', 'ngrok inspector')],
)
