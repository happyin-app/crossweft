"""FastAPI: router prefixes, include_router prefixes (nested), mounts, and
registrations the scanner must report. @app.get("/in-a-docstring") is not one."""
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from .users import router as users_router

app = FastAPI()
items = APIRouter(prefix="/items")
admin = APIRouter()
dynamic = APIRouter(prefix=PREFIX)


@app.get("/health")
def health():
    return "ok"


@items.get("/{item_id}")
@items.put("/{item_id:path}")
def item(item_id):
    return item_id


@items.api_route("/bulk", methods=["POST", "PATCH"])
def bulk():
    return None


@admin.delete("/users/{uid}")
def wipe(uid):
    return uid


@admin.websocket("/ws")
async def ws(socket):
    return None


@app.get(f"/v1/{VERSION}/x")
def computed():
    return None


@dynamic.get("/lost")
def lost():
    return None


def register(router: APIRouter):
    @router.get("/from-helper")
    def helper():
        return None


items.include_router(admin, prefix="/admin")
app.include_router(items, prefix="/v1")
app.include_router(users_router, prefix="/users")
app.include_router(users_router)
app.mount("/static", StaticFiles(directory="static"))
app.add_api_route("/metrics", metrics)
# @app.get("/commented")
