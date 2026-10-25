from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


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
    content_type: str
    file_size: int

    @field_serializer("uploaded_at", "expires_at")
    def format_uploaded_at(self, value: datetime) -> str:
        return value.strftime("%d-%m-%Y %H:%M:%S")


class FileUploadResponse(FileResponse):
    share_url: str


class UploadAuthorizeRequest(BaseModel):
    original_name: str = Field(default="unnamed", max_length=1024)
    content_type: str = Field(default="", max_length=255)
    file_size: int = Field(ge=0)


class UploadAuthorizationResponse(BaseModel):
    mode: Literal["direct", "proxy"]
    max_file_size: int
    upload_url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    upload_token: str | None = None
    upload_expires_at: datetime | None = None


class UploadCompleteRequest(BaseModel):
    upload_token: str = Field(min_length=1, max_length=8192)
