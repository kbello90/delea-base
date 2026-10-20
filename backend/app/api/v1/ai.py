"""AI assistant routes (/ai/query). Read-only enforcement lives here (TRD §3.2). Stub."""

from fastapi import APIRouter

router = APIRouter(prefix="/ai", tags=["ai"])
