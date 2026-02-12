"""
Pydantic models for VFS monitoring domain.

Pydantic-модели для описания слотов и внутренних структур.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, Field


class Slot(BaseModel):
    """Single VFS appointment slot."""

    date: datetime
    start_time: time
    end_time: Optional[time] = None
    location: str
    service: str
    notes: Optional[str] = None


class MonitorState(BaseModel):
    """State of monitoring loop, used internally."""

    is_running: bool = False
    last_check_at: Optional[datetime] = None
    last_error: Optional[str] = None
    checks_count: int = 0
    slots_found_total: int = 0


__all__ = ["Slot", "MonitorState"]

