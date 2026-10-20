"""FastAPI app entrypoint."""

from fastapi import FastAPI

from app.api.v1.router import api_router

app = FastAPI(title="DELEA API", version="0.0.0")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")
