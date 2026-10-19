# Mitra Shell MCP POC

This opt-in integration gives Mitra an ephemeral **virtual** shell for deterministic
text analysis. It does not run OS subprocesses or provide Kubernetes/AWS access.

## Deploy

Set `SHELL_MCP_ENABLED=true` in the AgentCore Runtime container environment.
The shipped Docker image includes `strands-shell`, and the Dockerfile sets this
flag for the proof of concept. Remove/override the flag to disable the integration.

Each invocation starts a separate Shell MCP subprocess; its virtual filesystem
and temporary host directory are deleted after the invocation. Only latest-message
UTF-8 text documents (up to 4.5 MB) are copied into `/workspace`.
Slack document download limitations are unchanged.

## Test

Upload [sample-app.log](../samples/shell-mcp/sample-app.log) to the Slack thread
and mention the bot:

> @Mitra Use Shell MCP to count ERROR entries in the uploaded log, group them
> by message, and explain the most likely contributing cause. Give exact totals.

Expected results:
- 12 ERROR entries
- Database connection timeout: 7
- Redis connection failed: 3
- HTTP 503 upstream unavailable: 2

Try a follow-up in a **new** Slack mention with the same file reattached:

> @Mitra Use Shell MCP to count WARN lines and compare INFO/WARN/ERROR counts.

Expected: INFO 4, WARN 3, ERROR 12.

If no tool is called, check the AgentCore Runtime environment variable and
that `strands-shell` can launch in the container. The tool names in logs should
include `shell`, `read_file`, `write_file`, `list_dir`.

## Limitations

- This is a POC, not permission to execute `kubectl`, `aws`, or host Bash.
- Text attachment staging is limited by Mitra's existing document size cap.
- Earlier-thread attachments are not automatically staged; reattach them.
- The virtual shell follows the Shell command inventory, not GNU coreutils.
- The sandbox's default network policy still permits some public requests. Do
  not use it for confidential data until outbound network access is restricted.
- No cross-invocation persistence. Do not enable for untrusted customers without
  reviewing sandbox process hardening, network restrictions, and audit controls.
