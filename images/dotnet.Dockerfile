# syntax=docker/dockerfile:1.7
# =============================================================================
# .NET Temporal worker image (per-worker csproj + shared MSBuild props at domain root).
#
#   DOMAIN            domain key (e.g. hello) — selects apps/temporal/workers/dotnet/<domain>/
#   APP_PATH          worker project dir, e.g. .../dotnet/<domain>/workflow
#   DOTNET_VERSION    SDK/runtime image tag (default 8.0) — must match TARGET_FRAMEWORK
#   TARGET_FRAMEWORK  MSBuild TFM (default net8.0), e.g. net8.0 / net10.0
# =============================================================================
ARG DOTNET_VERSION=8.0
ARG TARGET_FRAMEWORK=net8.0
ARG DOMAIN
ARG APP_PATH

FROM mcr.microsoft.com/dotnet/sdk:${DOTNET_VERSION} AS build
ARG DOMAIN
ARG APP_PATH
ARG TARGET_FRAMEWORK
WORKDIR /repo

# Shared MSBuild props + worker project (cache restore until csproj/props change).
COPY apps/temporal/workers/dotnet/${DOMAIN}/Directory.Build.props \
     apps/temporal/workers/dotnet/${DOMAIN}/Directory.Packages.props \
     apps/temporal/workers/dotnet/${DOMAIN}/.editorconfig \
     ./apps/temporal/workers/dotnet/${DOMAIN}/

COPY ${APP_PATH}/*.csproj ./${APP_PATH}/
RUN dotnet restore ./${APP_PATH} -p:TargetFramework=${TARGET_FRAMEWORK}

COPY apps/temporal/workers/dotnet/${DOMAIN}/ ./apps/temporal/workers/dotnet/${DOMAIN}/
RUN dotnet publish ./${APP_PATH} -c Release -p:TargetFramework=${TARGET_FRAMEWORK} \
    --no-restore -o /out

FROM mcr.microsoft.com/dotnet/runtime:${DOTNET_VERSION}
ARG DOTNET_VERSION
RUN groupadd -r appuser && useradd -r -g appuser appuser
WORKDIR /app
COPY --from=build /out ./
USER appuser
ENTRYPOINT ["dotnet", "Worker.dll"]
