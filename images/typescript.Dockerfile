# syntax=docker/dockerfile:1.7
# =============================================================================
# TypeScript Temporal worker image (pnpm workspace - one lockfile at repo root).
#
#   APP_PATH   worker package dir, e.g.
#              apps/temporal/workers/typescript/<domain>/activity
# =============================================================================
FROM node:22-bookworm-slim AS build
ARG APP_PATH
WORKDIR /repo

RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml* ./
COPY libs ./libs
COPY ${APP_PATH}/package.json ${APP_PATH}/package.json

RUN pnpm install --frozen-lockfile || pnpm install

COPY ${APP_PATH} ${APP_PATH}

RUN DOMAIN="$(echo "${APP_PATH}" | sed 's|apps/temporal/workers/typescript/||' | cut -d/ -f1)" && \
    if [ -f "libs/${DOMAIN}/typescript/tsconfig.json" ]; then \
      cd "libs/${DOMAIN}/typescript" && pnpm exec tsc -p tsconfig.json; \
    fi

WORKDIR /repo/${APP_PATH}
RUN pnpm exec tsc -p tsconfig.json

FROM node:22-bookworm-slim
ARG APP_PATH
WORKDIR /repo
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate
COPY --from=build /repo/node_modules ./node_modules
COPY --from=build /repo/libs ./libs
COPY --from=build /repo/package.json ./package.json
COPY --from=build /repo/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=build /repo/${APP_PATH} ./${APP_PATH}
ENV NODE_ENV=production
WORKDIR /repo/${APP_PATH}
CMD ["node", "dist/worker.js"]
