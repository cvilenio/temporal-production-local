from enum import StrEnum


class TaskQueue(StrEnum):
    WORKFLOW = "orders-workflow-task-queue"
    ACTIVITY = "orders-activity-task-queue"
