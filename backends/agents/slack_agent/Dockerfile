# Strands agent served by BedrockAgentCoreApp on :8080 (/invocations, /ping).
#   target "dist"     -> pushed to ECR for AgentCore Runtime (must be linux/arm64)
#   target "localdev" -> used by Tilt; restarts on source changes
ARG PYTHON_VERSION=3.13

FROM public.ecr.aws/docker/library/python:${PYTHON_VERSION}-slim AS base

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m -u 1000 bedrock_agentcore

# ============================================
# Localdev stage (Tilt)
# ============================================
FROM base AS localdev

RUN pip install --no-cache-dir watchfiles
COPY --chown=bedrock_agentcore:bedrock_agentcore src ./src
USER bedrock_agentcore

EXPOSE 8080
CMD ["watchfiles", "--filter", "python", "python -m main", "/app/src"]

# ============================================
# Dist stage (AgentCore Runtime)
# ============================================
FROM base AS dist

# Traces carry timings, token counts and tool names, never Slack text. An empty allow-list
# makes Strands replace every prompt, answer and tool input/result with "[REDACTED]". To
# see content while debugging, set OTEL_SEMCONV_STABILITY_OPT_IN on the runtime (e.g.
# gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages) and revert it.
ENV OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=

COPY --chown=bedrock_agentcore:bedrock_agentcore src ./src
USER bedrock_agentcore

EXPOSE 8080
# ADOT auto-instrumentation: spans for the Strands agents, Bedrock calls, boto3 and HTTP
# clients. Not used in localdev, which has no collector to send them to.
CMD ["opentelemetry-instrument", "python", "-m", "main"]
