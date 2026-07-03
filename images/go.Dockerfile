# syntax=docker/dockerfile:1.7
# =============================================================================
# Configurable image for any Go app in this monorepo (first: the
# temporal-worker-autoscaler controller). Each Go app is a self-contained module
# under apps/<domain>/<app>/go with its own go.mod — so the build context need
# only carry that module dir (passed as APP_PATH).
#
#   APP_PATH   Go module dir, e.g. apps/platform/temporal-worker-autoscaler/go
#   APP_PKG    package to build, relative to the module (default ./cmd)
# =============================================================================
ARG GO_VERSION=1.26

FROM golang:${GO_VERSION} AS build
ARG APP_PATH
ARG APP_PKG=./cmd
WORKDIR /src

# Dependency layer first (cached until go.mod/go.sum change).
COPY ${APP_PATH}/go.mod ${APP_PATH}/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download

# Source, then a static build.
COPY ${APP_PATH}/ ./
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOFLAGS=-trimpath go build -o /out/app ${APP_PKG}

# Minimal, non-root runtime.
FROM gcr.io/distroless/static:nonroot
COPY --from=build /out/app /app
USER 65532:65532
ENTRYPOINT ["/app"]
