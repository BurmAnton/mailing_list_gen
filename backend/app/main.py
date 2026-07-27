from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.api.integrations import router as integrations_router
from app.api.integrations import track_router
from app.config import get_settings
from app.db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    # Ensure SQLite data directory exists
    if settings.database_url.startswith("sqlite"):
        # sqlite+aiosqlite:///./data/mailing.db
        db_path = settings.database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await init_db()
    yield


app = FastAPI(title="Mailing List Generator", version="0.1.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(integrations_router)
app.include_router(track_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
