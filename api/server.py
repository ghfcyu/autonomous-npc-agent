"""FastAPI 服务：把引擎暴露为 REST API + 静态演示页。

启动：
    python -m api.server          # http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from engine.engine import NPCEngine
from engine.llm import create_provider

DATA_DIR = os.environ.get("NPC_DATA_DIR", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memory_store"))

app = FastAPI(title="autonomous-npc-agent", version="0.1.0")
engine = NPCEngine(llm=create_provider(), store_dir=DATA_DIR)


class TalkRequest(BaseModel):
    npc_id: str
    text: str


class TickRequest(BaseModel):
    minutes: int = 10


class MoveRequest(BaseModel):
    location_id: str


@app.get("/")
def index() -> FileResponse:
    demo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "demo", "index.html")
    return FileResponse(demo_path)


@app.get("/api/status")
def status():
    return engine.status()


@app.post("/api/talk")
def talk(req: TalkRequest):
    return engine.player_says(req.text, req.npc_id)


@app.post("/api/tick")
def tick(req: TickRequest):
    return engine.tick(req.minutes)


@app.post("/api/move")
def move(req: MoveRequest):
    return engine.move_player(req.location_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
