#!/usr/bin/env bash
# CloudWatch Transaction Search: what the agent's traces need to show up in
# CloudWatch -> GenAI Observability -> Bedrock AgentCore. It is an ACCOUNT + REGION wide
# setting (every app in the account that sends X-Ray spans is affected), so it lives here
# and not in Terraform.
#
# Usage:
#   transaction-search.sh status                     show the current state (read-only)
#   transaction-search.sh enable  [-i PERCENT]       send X-Ray spans to CloudWatch Logs
#   transaction-search.sh disable                    send them back to X-Ray
#
# Options:
#   -r REGION   AWS region           (default: $AWS_REGION, $AWS_DEFAULT_REGION, then us-east-1)
#   -p PROFILE  AWS CLI profile      (default: $AWS_PROFILE, then the CLI's default)
#   -i PERCENT  enable only: indexing rate, 0-100, the share of traces searchable as
#               summaries. Spans are stored whatever it is. Left unchanged if omitted
#               (an account's default is 1).
#   -y          don't ask for confirmation (it shows the profile, account and region first)
#   -n          dry run: print what would change, change nothing
#
# enable:  allows X-Ray to write to the aws/spans and /aws/application-signals/data log
#          groups, then sets the trace segment destination to CloudWatchLogs and waits
#          until it is ACTIVE. Safe to run again.
# disable: sets the destination back to XRay and removes the log policy enable added.
#          Spans already in aws/spans stay until the log group's retention deletes them.
#          Nothing else is removed, and the indexing rate is left as it is.
set -euo pipefail

POLICY_NAME="TransactionSearchAccessPolicy" # also the name used by AWS's CloudFormation sample
WAIT_SECONDS=300

region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
profile="${AWS_PROFILE:-}"
indexing=""
assume_yes=false
dry_run=false

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
die() { echo "error: $*" >&2; exit 1; }

command="${1:-}"
[[ -n "${command}" ]] && shift || true
case "${command}" in status | enable | disable) ;; -h | --help | "") usage; exit 0 ;; *) usage >&2; die "unknown command '${command}'" ;; esac

while getopts "r:p:i:ynh" opt; do
  case "${opt}" in
    r) region="${OPTARG}" ;;
    p) profile="${OPTARG}" ;;
    i) indexing="${OPTARG}" ;;
    y) assume_yes=true ;;
    n) dry_run=true ;;
    h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

if [[ -n "${indexing}" ]]; then
  [[ "${command}" == "enable" ]] || die "-i only applies to enable"
  [[ "${indexing}" =~ ^([0-9]+)(\.[0-9]+)?$ ]] && awk "BEGIN{exit !(${indexing} <= 100)}" || die "-i must be a number from 0 to 100"
fi

aws_cli() {
  if [[ -n "${profile}" ]]; then aws --profile "${profile}" --region "${region}" "$@"; else aws --region "${region}" "$@"; fi
}

# Changes go through here so -n can show them without making them.
change() {
  if "${dry_run}"; then echo "  [dry run] aws $*"; else aws_cli "$@" >/dev/null; fi
}

account="$(aws_cli sts get-caller-identity --query Account --output text)" || die "can't reach AWS with these credentials"

destination() { aws_cli xray get-trace-segment-destination --query '[Destination, Status]' --output text; }

confirm() {
  "${assume_yes}" && return 0
  "${dry_run}" && return 0
  [[ -t 0 ]] || die "not a terminal: pass -y to confirm"
  echo "This changes Transaction Search for EVERYTHING in this account:"
  echo "  profile ${profile:-${AWS_PROFILE:-default}}, account ${account}, region ${region}"
  read -r -p "Continue? [y/N] " answer
  [[ "${answer}" == [yY] || "${answer}" == [yY][eE][sS] ]] || die "aborted"
}

policy_document() {
  python3 - "${region}" "${account}" <<'PY'
import json, sys
region, account = sys.argv[1:3]
print(json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Sid": "TransactionSearchXRayAccess",
        "Effect": "Allow",
        "Principal": {"Service": "xray.amazonaws.com"},
        "Action": "logs:PutLogEvents",
        "Resource": [
            f"arn:aws:logs:{region}:{account}:log-group:aws/spans:*",
            f"arn:aws:logs:{region}:{account}:log-group:/aws/application-signals/data:*",
        ],
        "Condition": {
            "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{region}:{account}:*"},
            "StringEquals": {"aws:SourceAccount": account},
        },
    }],
}))
PY
}

wait_until_active() {
  "${dry_run}" && return 0
  local waited=0 state
  while ((waited < WAIT_SECONDS)); do
    state="$(destination)"
    [[ "${state}" == "CloudWatchLogs"$'\t'"ACTIVE" ]] && { echo "  destination is ACTIVE"; return 0; }
    echo "  waiting (${state//$'\t'/ }) ..."
    sleep 10
    waited=$((waited + 10))
  done
  die "still not ACTIVE after ${WAIT_SECONDS}s: run '$0 status -r ${region}' later"
}

show_status() {
  local state rate policy
  state="$(destination)"
  rate="$(aws_cli xray get-indexing-rules \
    --query 'IndexingRules[?Name==`Default`].Rule.Probabilistic.DesiredSamplingPercentage | [0]' --output text 2>/dev/null || echo "?")"
  if aws_cli logs describe-resource-policies --query "resourcePolicies[?policyName=='${POLICY_NAME}'].policyName | [0]" --output text 2>/dev/null | grep -qx "${POLICY_NAME}"; then
    policy="present"
  else
    policy="missing"
  fi
  echo "profile ${profile:-${AWS_PROFILE:-default}}, account ${account}, region ${region}"
  echo "  trace segment destination / status : ${state//$'\t'/ / }"
  echo "  indexing rate (Default rule)       : ${rate}%"
  echo "  log policy ${POLICY_NAME}: ${policy}"
  if [[ "${state}" == "CloudWatchLogs"$'\t'"ACTIVE" ]]; then echo "  => Transaction Search is ENABLED"; else echo "  => Transaction Search is not enabled"; fi
}

enable() {
  confirm
  echo "Enabling Transaction Search in account ${account}, region ${region}"
  change logs put-resource-policy --policy-name "${POLICY_NAME}" --policy-document "$(policy_document)"
  if [[ "$(destination | cut -f1)" == "CloudWatchLogs" ]]; then
    echo "  destination is already CloudWatchLogs"
  else
    change xray update-trace-segment-destination --destination CloudWatchLogs
  fi
  wait_until_active
  if [[ -n "${indexing}" ]]; then
    change xray update-indexing-rule --name Default \
      --rule "{\"Probabilistic\": {\"DesiredSamplingPercentage\": ${indexing}}}"
  fi
  "${dry_run}" && { echo "Dry run: nothing was changed."; return 0; }
  echo "Done. Spans appear in CloudWatch -> GenAI Observability a few minutes after the next request."
}

disable() {
  confirm
  echo "Disabling Transaction Search in account ${account}, region ${region}"
  if [[ "$(destination | cut -f1)" == "XRay" ]]; then
    echo "  destination is already XRay"
  else
    change xray update-trace-segment-destination --destination XRay
  fi
  if "${dry_run}"; then
    change logs delete-resource-policy --policy-name "${POLICY_NAME}"
  else
    aws_cli logs delete-resource-policy --policy-name "${POLICY_NAME}" >/dev/null 2>&1 || echo "  log policy ${POLICY_NAME} was not there"
  fi
  "${dry_run}" && { echo "Dry run: nothing was changed."; return 0; }
  echo "Done. Existing spans stay in the aws/spans log group until its retention removes them."
}

case "${command}" in
  status) show_status ;;
  enable) enable ;;
  disable) disable ;;
esac
