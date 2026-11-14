"""Shared web application state, templates and in-memory build manager."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ordpresse.pipeline import BuildResult
from ordpresse.services.mirror_prober import MirrorProberService

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
mirror_prober = MirrorProberService()


@dataclass
class BuildJob:
    build_id: str
    status: str = "queued"  # queued, building, done, error, cancelled
    progress: int = 0
    message: str = "Venter i kø..."
    result: Optional[BuildResult] = None
    error: Optional[str] = None
    cancelled: bool = False
    events: asyncio.Queue = field(default_factory=asyncio.Queue)


ACTIVE_BUILDS: Dict[str, BuildJob] = {}


class CacheSubmitPayload(BaseModel):
    word: str = Field(..., min_length=1, max_length=80)
    html: str = Field(..., min_length=1, max_length=500_000)
    build_id: Optional[str] = None


class ProvisionKeyPayload(BaseModel):
    device_name: str = Field(..., min_length=1, max_length=100)
    endpoint_url: Optional[str] = "https://ordpresse.seame.click/api/v1/koreader/lookup"
    custom_key: Optional[str] = None


class ChangePasswordPayload(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=4, max_length=128)
    confirm_password: str = Field(..., min_length=4, max_length=128)
