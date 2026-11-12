# Mermaid -> PNG Lambda for draw_diagram (arm64, container image).
#   target "lambda" -> pushed to ECR and used by AWS Lambda
ARG NODE_VERSION=22

# ============================================
# Chromium pack: pinned and checked, so a changed upstream file fails the build.
# ============================================
FROM public.ecr.aws/docker/library/debian:bookworm-slim AS chromium

ARG CHROMIUM_VERSION=v153.0.0
ARG CHROMIUM_PACK_SHA256=b6874fc0d7af15e16a15ef1b73177731f698c296c73d26aa4a90840c0777a338

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /pack.tar \
      "https://github.com/Sparticuz/chromium/releases/download/${CHROMIUM_VERSION}/chromium-${CHROMIUM_VERSION}-pack.arm64.tar" \
    && echo "${CHROMIUM_PACK_SHA256}  /pack.tar" | sha256sum -c - \
    && mkdir /chromium \
    && tar -xf /pack.tar -C /chromium

# ============================================
# Lambda stage
# ============================================
FROM public.ecr.aws/lambda/nodejs:${NODE_VERSION} AS lambda

# puppeteer is a peer dependency of mermaid-cli; the browser comes from the pack, not from it.
ENV PUPPETEER_SKIP_DOWNLOAD=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

COPY package.json package-lock.json ${LAMBDA_TASK_ROOT}/
RUN npm ci --omit=dev --no-audit --no-fund

COPY --from=chromium /chromium /opt/chromium
COPY index.mjs ${LAMBDA_TASK_ROOT}/

CMD ["index.handler"]
