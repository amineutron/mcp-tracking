from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
import uuid


class ItemStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class SessionStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PAUSED = "paused"


class TrackingItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    status: ItemStatus = ItemStatus.PENDING
    processed: Optional[float] = None
    total: Optional[float] = None
    unit: str = ""
    note: str = ""


class LogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    message: str


class TrackingSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    template: str  # "download", "machine", "free"
    status: SessionStatus = SessionStatus.RUNNING
    processed: float = 0
    total: float = 100
    unit: str = ""
    items: List[TrackingItem] = []
    logs: List[LogEntry] = []
    extra: Dict[str, Any] = {}  # champs specifiques au template
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
