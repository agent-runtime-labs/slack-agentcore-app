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

# Safe defaults until main.py runs: no Slack text in traces or telemetry logs. An empty
# allow-list makes Strands replace every prompt, answer and tool input/result with
# "[REDACTED]", and the second line stops ADOT's Bedrock instrumentation logging prompts.
# main.py then sets both again from TELEMETRY_CAPTURE_CONTENT (telemetry_capture_content in
# Terraform), which is how dev can read content and every other environment cannot.
ENV OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=
ENV OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false

COPY --chown=bedrock_agentcore:bedrock_agentcore src ./src
USER bedrock_agentcore

EXPOSE 8080
# ADOT auto-instrumentation: spans for the Strands agents, Bedrock calls, boto3 and HTTP
# clients. Not used in localdev, which has no collector to send them to.
CMD ["opentelemetry-instrument", "python", "-m", "main"]
