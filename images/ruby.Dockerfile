# syntax=docker/dockerfile:1.7
# =============================================================================
# Ruby Temporal worker image (per-worker Gemfile + path gem to libs/<domain>/ruby).
#
#   APP_PATH      worker dir, e.g. apps/temporal/workers/ruby/<domain>/workflow
#   RUBY_VERSION  base image tag (default 3.3)
# =============================================================================
ARG RUBY_VERSION=3.3
ARG APP_PATH

FROM ruby:${RUBY_VERSION}-slim AS build
ARG APP_PATH
WORKDIR /repo

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends build-essential git && \
    rm -rf /var/lib/apt/lists/*

# Shared domain gem (path dep from worker Gemfile).
COPY libs/ ./libs/

COPY ${APP_PATH}/Gemfile ${APP_PATH}/Gemfile.lock ./${APP_PATH}/
COPY ${APP_PATH}/ ./${APP_PATH}/

WORKDIR /repo/${APP_PATH}
RUN bundle config set --local deployment 'true' && \
    bundle config set --local path vendor/bundle && \
    bundle install --jobs 4

FROM ruby:${RUBY_VERSION}-slim
ARG APP_PATH
RUN groupadd -r appuser && useradd -r -g appuser appuser
WORKDIR /app
COPY --from=build /repo/${APP_PATH} /app
# Worker Gemfiles use a path dep to libs/<domain>/ruby (../../../../../../libs/...).
# Bundler resolves that at runtime even in deployment mode — mirror the build layout.
COPY --from=build /repo/libs /libs
# Build-stage bundle config lives in /usr/local/bundle/config (not under /app).
ENV BUNDLE_DEPLOYMENT=1 \
    BUNDLE_PATH=/app/vendor/bundle
USER appuser
CMD ["ruby", "worker.rb"]
