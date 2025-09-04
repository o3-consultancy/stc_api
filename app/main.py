# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.dependencies.db import close_mongo_connection
from app.routers import users, quiz
from app.middleware.auth import ApiKeyAuthMiddleware, collect_public_paths

settings = get_settings()

app = FastAPI(
    title="STC API",
    version="1.0.0",
    # keep schema available if you want; not required by the static page
    openapi_url="/openapi.json",
    docs_url=None,                # disable Swagger UI
    redoc_url=None,               # disable FastAPI ReDoc
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers (secured by API key unless explicitly public)
app.include_router(users.router, prefix=settings.API_BASE_PATH)
app.include_router(quiz.router, prefix=settings.API_BASE_PATH)

# Serve static ReDoc at /docs
app.mount("/docs", StaticFiles(directory="app/static/docs", html=True), name="docs")

# API-key middleware BEFORE startup
# middleware already whitelists /docs, /redoc, /openapi.json, /healthz
public_paths = collect_public_paths(app)
app.add_middleware(ApiKeyAuthMiddleware, public_paths=public_paths)

# Public health endpoint


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

# Shutdown cleanly


@app.on_event("shutdown")
async def _shutdown():
    await close_mongo_connection()
