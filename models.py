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


class LogLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class TrackingItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    status: ItemStatus = ItemStatus.PENDING
    processed: Optional[float] = None
    total: Optional[float] = None
    unit: str = ""
    note: str = ""
    # Poses automatiquement par mutations.py au passage running / done|error
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class LogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    message: str
    level: LogLevel = LogLevel.INFO


class ProgressPoint(BaseModel):
    """Echantillon (instant, valeur) servant au calcul de vitesse / ETA."""
    t: datetime
    v: float


class TrackingSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    template: str  # voir templates.py
    status: SessionStatus = SessionStatus.RUNNING
    processed: float = 0
    total: float = 100
    unit: str = ""
    items: List[TrackingItem] = []
    logs: List[LogEntry] = []
    extra: Dict[str, Any] = {}  # champs specifiques au template
    history: List[ProgressPoint] = []  # fenetre glissante de progression
    pid: Optional[int] = None  # processus a signaler pour stop / kill
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
