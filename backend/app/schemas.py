
from pydantic import BaseModel


class LoginRequest(BaseModel):
    account: str
    password: str


class UserOut(BaseModel):
    id: int
    account: str
    name: str
    role: str

    model_config = {"from_attributes": True}
