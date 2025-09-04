from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.dependencies.db import close_mongo_connection
from app.routers import users, quiz
from app.routers import surveys, analytics, admin
from app.middleware.auth import ApiKeyAuthMiddleware, collect_public_paths

settings = get_settings()

app = FastAPI(
    title="STC API",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url=None,
    redoc_url=None,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(users.router, prefix=settings.API_BASE_PATH)
app.include_router(quiz.router, prefix=settings.API_BASE_PATH)
app.include_router(surveys.router, prefix=settings.API_BASE_PATH)  # <-- NEW
app.include_router(analytics.router, prefix=settings.API_BASE_PATH)  # NEW
app.include_router(admin.router, prefix=settings.API_BASE_PATH)

# Static docs (public)
app.mount("/docs", StaticFiles(directory="app/static/docs", html=True), name="docs")

# API-key middleware BEFORE startup (whitelists /docs, /healthz, etc.)
public_paths = collect_public_paths(app)
app.add_middleware(ApiKeyAuthMiddleware, public_paths=public_paths)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.on_event("shutdown")
async def _shutdown():
    await close_mongo_connection()
