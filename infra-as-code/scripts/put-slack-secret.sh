#!/usr/bin/env bash
# Store Slack credentials in the Secrets Manager secret created by Terraform.
#
#   SLACK_BOT_TOKEN=xoxb-... SLACK_SIGNING_SECRET=... ./put-slack-secret.sh dev
set -euo pipefail

env_name="${1:?usage: $0 <env>}"
: "${SLACK_BOT_TOKEN:?set SLACK_BOT_TOKEN}" "${SLACK_SIGNING_SECRET:?set SLACK_SIGNING_SECRET}"

cd "$(dirname "$0")/.."
secret_arn="$(./tf-wrapper.sh "${env_name}" output -raw slack_secret_arn)"

input="$(mktemp)"
chmod 600 "${input}"
trap 'rm -f "${input}"' EXIT

python3 - "${input}" "${secret_arn}" <<'PY'
import json, os, sys
json.dump({
    "SecretId": sys.argv[2],
    "SecretString": json.dumps({
        "bot_token": os.environ["SLACK_BOT_TOKEN"],
        "signing_secret": os.environ["SLACK_SIGNING_SECRET"],
    }),
}, open(sys.argv[1], "w"))
PY

aws secretsmanager put-secret-value --region "${AWS_REGION:-us-east-1}" --cli-input-json "file://${input}" >/dev/null
echo "Slack credentials stored in ${secret_arn}"
