# re‑export routers for fast import
from .common import router as common_router
from .lessons import router as lessons_router
from .homework import router as homework_router
from .students import router as students_router

__all__ = ["common_router", "lessons_router", "homework_router", "students_router"]