from temporalio import activity

from {{DOMAIN}}.shared.temporal_ids import ActivityName


class HelloActivities:
    @activity.defn(name=ActivityName.SAY_HELLO)
    async def say_hello(self, name: str) -> str:
        return f"Hello, {name}!"
