from fastapi import APIRouter

from app.api.routes import statements

router = APIRouter()
router.include_router(statements.router, prefix="/statements", tags=["statements"])
