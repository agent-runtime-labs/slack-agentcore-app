# One image, three Lambda handlers (selected via image_config.command in Terraform).
#   target "lambda"   -> pushed to ECR and used by AWS Lambda (arm64)
#   target "localdev" -> used by Tilt; runs the handlers behind a local FastAPI server
ARG PYTHON_VERSION=3.13

# ============================================
# Lambda stage
# ============================================
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION} AS lambda

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt -t ${LAMBDA_TASK_ROOT}

COPY src/slack_app ${LAMBDA_TASK_ROOT}/slack_app

CMD ["slack_app.handlers.slack_events.handler"]

# ============================================
# Localdev stage (Tilt)
# ============================================
FROM public.ecr.aws/docker/library/python:${PYTHON_VERSION}-slim AS localdev

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

COPY requirements.txt requirements-local.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-local.txt

RUN useradd -m -u 1000 app
COPY --chown=app:app src ./src
USER app

EXPOSE 8081
CMD ["uvicorn", "slack_app.local_server:app", "--host", "0.0.0.0", "--port", "8081", "--reload", "--reload-dir", "/app/src"]
