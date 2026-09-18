from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agents, auth, notifications, users
from app.core.config import get_settings
from app.db.mongo import close_mongo, init_mongo
from app.services import crawler_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_mongo(app)
    # A restart kills in-flight crawls; unstick agents left in "Running".
    await crawler_service.reset_interrupted_crawls(app.state.mongo_db)
    yield
    await close_mongo(app)


settings = get_settings()

app = FastAPI(title="Data Crawler API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,  # Bearer headers, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")


@app.get("/api/health")
async def health(request: Request) -> dict:
    try:
        await request.app.state.mongo_db.command("ping")
        db_status = "up"
    except Exception:
        db_status = "down"
    return {"status": "ok", "database": db_status}
