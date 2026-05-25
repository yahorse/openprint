from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    PRINTING = "printing"
    COMPLETED = "completed"
    CANCELED = "canceled"
    ERROR = "error"


class PrinterState(str, enum.Enum):
    IDLE = "idle"
    PRINTING = "printing"
    ERROR = "error"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


class DuplexMode(str, enum.Enum):
    NONE = "none"
    LONG_EDGE = "long-edge"
    SHORT_EDGE = "short-edge"


class Capabilities(BaseModel):
    color: bool = True
    duplex: bool = True
    media_sizes: list[str] = Field(default_factory=lambda: ["a4", "letter"])
    max_pdf_version: str = "2.0"
    max_file_size: int = 104_857_600  # 100MB
    copies_max: int = 99


class PrinterInfo(BaseModel):
    name: str
    manufacturer: str = "Generic"
    model: str = "OPP Reference Server"
    protocol_version: str = "1.0"
    capabilities: Capabilities = Field(default_factory=Capabilities)
    status: PrinterState = PrinterState.IDLE


class JobRequest(BaseModel):
    copies: int = 1
    color: bool = True
    duplex: DuplexMode = DuplexMode.NONE
    media: str = "a4"
    pages: str = "all"
    priority: int = 50


class Job(BaseModel):
    id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pages_total: int = 0
    pages_printed: int = 0
    copies: int = 1
    color: bool = True
    duplex: DuplexMode = DuplexMode.NONE
    media: str = "a4"
    priority: int = 50
    error: str | None = None
    file_size: int = 0


class JobList(BaseModel):
    jobs: list[Job]
    total: int


class TrayStatus(BaseModel):
    media: str
    level: str  # "full", "low", "empty"


class SupplyLevels(BaseModel):
    black: int = 100
    cyan: int = 100
    magenta: int = 100
    yellow: int = 100
    paper: dict[str, TrayStatus] = Field(default_factory=dict)


class PrinterStatus(BaseModel):
    state: PrinterState = PrinterState.IDLE
    supplies: SupplyLevels = Field(default_factory=SupplyLevels)
    errors: list[str] = Field(default_factory=list)
    jobs_queued: int = 0
    jobs_printing: int = 0


class SSEEvent(BaseModel):
    event: str
    data: dict[str, Any]
