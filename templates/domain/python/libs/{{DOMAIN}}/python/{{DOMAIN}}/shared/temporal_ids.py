from enum import StrEnum


class TaskQueue(StrEnum):
    WORKFLOW = "{{DOMAIN}}-workflow-task-queue"
    ACTIVITY = "{{DOMAIN}}-activity-task-queue"


class ActivityName(StrEnum):
    SAY_HELLO = "say_hello"
