from pydantic import BaseModel, Field


class HelloInput(BaseModel):
    name: str = Field(min_length=1)


class HelloResult(BaseModel):
    message: str
