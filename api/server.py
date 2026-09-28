"""FastAPI 服务：把引擎暴露为 REST API + 静态演示页。

启动：
    python -m api.server          # http://127.0.0.1:8000

若项目根目录存在 .env（参考 .env.example），启动时自动加载，
即可用真实大模型驱动 NPC；无 .env 时自动回退离线 Mock。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load_env(path: str) -> None:
    """极简 .env 加载器：KEY=VALUE，# 注释，不覆盖已有环境变量。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env(os.path.join(ROOT, ".env"))

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from engine.engine import NPCEngine
from engine.llm import create_provider

DATA_DIR = os.environ.get("NPC_DATA_DIR", os.path.join(ROOT, "data", "memory_store"))

app = FastAPI(title="autonomous-npc-agent", version="0.1.0")
_provider = create_provider()
print(f"[npc-engine] LLM provider: {_provider.name}"
      + (f" ({_provider.base_url}, model={_provider.model})"
         if getattr(_provider, "base_url", "") else ""))
engine = NPCEngine(llm=_provider, store_dir=DATA_DIR)


class TalkRequest(BaseModel):
    npc_id: str
    text: str


class TickRequest(BaseModel):
    minutes: int = 10


class MoveRequest(BaseModel):
    location_id: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(ROOT, "demo", "index.html"))


@app.get("/api/status")
def status():
    result = engine.status()
    result["llm"] = {"provider": _provider.name,
                     "model": getattr(_provider, "model", None)}
    return result


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
