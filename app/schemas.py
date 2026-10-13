from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_serializer

class FileCreate(BaseModel):
    original_name: str
    stored_name: str

class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_name: str
    stored_name: str
    share_token: str
    uploaded_at: datetime
    expires_at: datetime

    @field_serializer("uploaded_at", "expires_at")
    def format_uploaded_at(self, value: datetime) -> str:
        return value.strftime("%d-%m-%Y %H:%M:%S")