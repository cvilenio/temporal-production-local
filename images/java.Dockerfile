# syntax=docker/dockerfile:1.7
# =============================================================================
# Configurable Java image for Temporal worker apps in this monorepo.
#
# Build args:
#   DOMAIN           Domain key (e.g. hello) — selects libs/<domain>/java
#   APP_MODULE       Gradle subproject (e.g. :hello-workflow-worker)
#   WORKER_REL_PATH  Path to worker module under apps/temporal/workers/java/
#   APP_JAR          Boot jar base name under build/libs/ (defaults from module)
# =============================================================================
FROM eclipse-temurin:17-jdk AS builder

WORKDIR /workspace

COPY gradlew gradlew.bat settings.gradle build.gradle gradle.properties ./
COPY gradle ./gradle
COPY libs/appkit/java ./libs/appkit/java

ARG DOMAIN=hello
COPY libs/${DOMAIN}/java ./libs/${DOMAIN}/java
COPY apps/temporal/workers/java ./apps/temporal/workers/java

ARG APP_MODULE=:hello-workflow-worker

RUN chmod +x gradlew && ./gradlew ${APP_MODULE}:bootJar -x test --no-daemon

FROM eclipse-temurin:17-jre

ARG WORKER_REL_PATH=apps/temporal/workers/java/hello/workflow
ARG APP_JAR=hello-workflow-worker

WORKDIR /app

COPY --from=builder /workspace/${WORKER_REL_PATH}/build/libs/${APP_JAR}-*.jar /app/app.jar

ENV JAVA_OPTS="-XX:MaxRAMPercentage=75.0"

ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
