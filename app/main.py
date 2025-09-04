from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import get_settings
from app.dependencies.db import close_mongo_connection  # no startup connect
from app.routers import users, quiz
from app.middleware.auth import ApiKeyAuthMiddleware, collect_public_paths

settings = get_settings()

app = FastAPI(
    title="STC API",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers under base path
app.include_router(users.router, prefix=settings.API_BASE_PATH)
app.include_router(quiz.router, prefix=settings.API_BASE_PATH)

# API-key middleware BEFORE startup
# only /docs, /redoc, /openapi.json, /healthz are public
public_paths = collect_public_paths(app)
app.add_middleware(ApiKeyAuthMiddleware, public_paths=public_paths)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.on_event("shutdown")
async def _shutdown():
    await close_mongo_connection()
