#!/usr/bin/env python3
"""
Collaborative domain availability checker.

Setup:
    pip install fastapi uvicorn

Run:
    python scripts/domain_checker/server.py

Share with a friend:
    # Install ngrok (https://ngrok.com), then:
    ngrok http 8000
    # Send them the https://xxxx.ngrok.io URL
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from upstash_redis import Redis

HERE = Path(__file__).parent
TLDS = [".com", ".io", ".ai", ".co", ".app"]

redis = Redis(url="https://measured-ringtail-97695.upstash.io", token=os.environ["UPSTASH_REDIS_REST_TOKEN"])

app = FastAPI()
shortlist: list[dict[str, Any]] = json.loads(redis.get("shortlist") or "[]")
connections: list[WebSocket] = []


def save_shortlist() -> None:
    redis.set("shortlist", json.dumps(shortlist))


# ── Domain check ───────────────────────────────────────────────────────────────

async def is_available(domain: str) -> bool:
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, socket.getaddrinfo, domain, None)
        return False
    except OSError:
        return True


async def check_name(name: str) -> dict[str, Any]:
    results = await asyncio.gather(
        *[is_available(f"{name.lower()}{tld}") for tld in TLDS]
    )
    return {"name": name, "domains": dict(zip(TLDS, results))}


# ── Broadcast ──────────────────────────────────────────────────────────────────

async def broadcast(message: dict) -> None:
    for ws in connections[:]:
        try:
            await ws.send_json(message)
        except Exception:
            connections.remove(ws)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(HERE / "index.html")


class CheckRequest(BaseModel):
    name: str


class ShortlistAdd(BaseModel):
    name: str
    domains: dict[str, bool]


@app.post("/check")
async def check(req: CheckRequest):
    name = req.name.strip()
    if not name:
        return {"error": "No name provided"}
    return await check_name(name)


@app.get("/shortlist")
async def get_shortlist():
    return shortlist


@app.post("/shortlist")
async def add(req: ShortlistAdd):
    name = req.name.strip()
    if not name or any(i["name"] == name for i in shortlist):
        return {"ok": False}
    entry = {"name": name, "domains": req.domains}
    shortlist.append(entry)
    save_shortlist()
    await broadcast({"type": "add", "entry": entry})
    return {"ok": True}


@app.delete("/shortlist/{name}")
async def remove(name: str):
    global shortlist
    shortlist = [i for i in shortlist if i["name"] != name]
    save_shortlist()
    await broadcast({"type": "remove", "name": name})
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    connections.append(ws)
    await ws.send_json({"type": "init", "shortlist": shortlist})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in connections:
            connections.remove(ws)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    print(f"\n  Domain Checker running at http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
