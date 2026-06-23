from __future__ import annotations

import os

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    name: str = Field(default_factory=lambda: os.environ.get("OPP_NAME", "OpenPrint Server"))
    port: int = Field(default_factory=lambda: int(os.environ.get("OPP_PORT", "631")))
    host: str = Field(default_factory=lambda: os.environ.get("OPP_HOST", "0.0.0.0"))
    auth_token: str | None = Field(
        default_factory=lambda: os.environ.get("OPP_AUTH_TOKEN")
    )
    max_file_size: int = Field(
        default_factory=lambda: int(os.environ.get("OPP_MAX_FILE_SIZE", "104857600"))
    )
    color: bool = True
    duplex: bool = True
    supported_media: list[str] = Field(default_factory=lambda: ["a4", "letter"])
    enable_discovery: bool = True
    log_requests: bool = True
    max_queue_size: int = 100
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
