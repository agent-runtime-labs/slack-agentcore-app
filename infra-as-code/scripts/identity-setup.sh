#!/usr/bin/env bash
# AgentCore Identity resources that Terraform can't manage well:
#   - the LinkedIn OAuth2 credential provider (built-in LinkedinOauth2 vendor;
#     keeps the client secret out of Terraform state)
#   - the GitHub OAuth2 credential provider (built-in GithubOauth2 vendor)
#   - a workload identity for local development (Tilt)
#
# Usage:
#   identity-setup.sh linkedin-provider   create/update provider, print the redirect URL for LinkedIn
#   identity-setup.sh github-provider     create/update provider, print the redirect URL for GitHub
#   identity-setup.sh local-workload      create/update the local workload identity
#   identity-setup.sh show                show all three
#
# Env: AWS_PROFILE, AWS_REGION, LINKEDIN_PROVIDER_NAME, LINKEDIN_CLIENT_ID,
#      LINKEDIN_CLIENT_SECRET, GITHUB_PROVIDER_NAME, GITHUB_CLIENT_ID,
#      GITHUB_CLIENT_SECRET, LOCAL_WORKLOAD_NAME, LOCAL_OAUTH_RETURN_URL
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PROVIDER_NAME="${LINKEDIN_PROVIDER_NAME:-slack-agent-linkedin}"
GITHUB_PROVIDER_NAME="${GITHUB_PROVIDER_NAME:-slack-agent-github}"
WORKLOAD_NAME="${LOCAL_WORKLOAD_NAME:-slack-agent-local}"
LOCAL_RETURN_URL="${LOCAL_OAUTH_RETURN_URL:-http://localhost:8081/oauth2/callback}"

cli() { aws bedrock-agentcore-control --region "${REGION}" "$@"; }

linkedin_provider() {
  : "${LINKEDIN_CLIENT_ID:?set LINKEDIN_CLIENT_ID}" "${LINKEDIN_CLIENT_SECRET:?set LINKEDIN_CLIENT_SECRET}"

  # Pass the secret via a private temp file, not argv (visible in `ps`).
  local input
  input="$(mktemp)"
  chmod 600 "${input}"
  trap 'rm -f "${input}"' RETURN
  python3 - "${input}" <<'PY'
import json, os, sys
json.dump({
    "name": os.environ.get("LINKEDIN_PROVIDER_NAME", "slack-agent-linkedin"),
    "credentialProviderVendor": "LinkedinOauth2",
    "oauth2ProviderConfigInput": {
        "linkedinOauth2ProviderConfig": {
            "clientId": os.environ["LINKEDIN_CLIENT_ID"],
            "clientSecret": os.environ["LINKEDIN_CLIENT_SECRET"],
        }
    },
}, open(sys.argv[1], "w"))
PY

  if cli get-oauth2-credential-provider --name "${PROVIDER_NAME}" >/dev/null 2>&1; then
    echo "Updating credential provider ${PROVIDER_NAME}"
    cli update-oauth2-credential-provider --cli-input-json "file://${input}" >/dev/null
  else
    echo "Creating credential provider ${PROVIDER_NAME}"
    cli create-oauth2-credential-provider --cli-input-json "file://${input}" >/dev/null
  fi
  show_provider
}

show_provider() {
  local callback
  callback="$(cli get-oauth2-credential-provider --name "${PROVIDER_NAME}" --query callbackUrl --output text)"
  echo
  echo "Credential provider: ${PROVIDER_NAME}"
  echo "Add this to LinkedIn app > Auth > Authorized redirect URLs:"
  echo "  ${callback}"
}

github_provider() {
  : "${GITHUB_CLIENT_ID:?set GITHUB_CLIENT_ID}" "${GITHUB_CLIENT_SECRET:?set GITHUB_CLIENT_SECRET}"

  local input
  input="$(mktemp)"
  chmod 600 "${input}"
  trap 'rm -f "${input}"' RETURN
  python3 - "${input}" <<'PY'
import json, os, sys
json.dump({
    "name": os.environ.get("GITHUB_PROVIDER_NAME", "slack-agent-github"),
    "credentialProviderVendor": "GithubOauth2",
    "oauth2ProviderConfigInput": {
        "githubOauth2ProviderConfig": {
            "clientId": os.environ["GITHUB_CLIENT_ID"],
            "clientSecret": os.environ["GITHUB_CLIENT_SECRET"],
        }
    },
}, open(sys.argv[1], "w"))
PY

  if cli get-oauth2-credential-provider --name "${GITHUB_PROVIDER_NAME}" >/dev/null 2>&1; then
    echo "Updating credential provider ${GITHUB_PROVIDER_NAME}"
    cli update-oauth2-credential-provider --cli-input-json "file://${input}" >/dev/null
  else
    echo "Creating credential provider ${GITHUB_PROVIDER_NAME}"
    cli create-oauth2-credential-provider --cli-input-json "file://${input}" >/dev/null
  fi
  show_github_provider
}

show_github_provider() {
  local callback
  callback="$(cli get-oauth2-credential-provider --name "${GITHUB_PROVIDER_NAME}" --query callbackUrl --output text)"
  echo
  echo "Credential provider: ${GITHUB_PROVIDER_NAME}"
  echo "Add this to GitHub OAuth App > Authorization callback URL:"
  echo "  ${callback}"
}

local_workload() {
  if cli get-workload-identity --name "${WORKLOAD_NAME}" >/dev/null 2>&1; then
    echo "Updating workload identity ${WORKLOAD_NAME}"
    cli update-workload-identity --name "${WORKLOAD_NAME}" \
      --allowed-resource-oauth2-return-urls "${LOCAL_RETURN_URL}" >/dev/null
  else
    echo "Creating workload identity ${WORKLOAD_NAME}"
    cli create-workload-identity --name "${WORKLOAD_NAME}" \
      --allowed-resource-oauth2-return-urls "${LOCAL_RETURN_URL}" >/dev/null
  fi
  show_workload
}

show_workload() {
  echo
  echo "Workload identity: ${WORKLOAD_NAME}"
  cli get-workload-identity --name "${WORKLOAD_NAME}" \
    --query '{arn: workloadIdentityArn, returnUrls: allowedResourceOauth2ReturnUrls}' --output json
}

case "${1:-}" in
  linkedin-provider) linkedin_provider ;;
  github-provider) github_provider ;;
  local-workload) local_workload ;;
  show) show_provider; show_github_provider; show_workload ;;
  *)
    sed -n '2,16p' "$0"
    exit 1
    ;;
esac
