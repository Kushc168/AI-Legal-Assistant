"""AI Chat: conversations with grounded, cited answers (Sprint 6)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..services import ai_service, contracts

router = APIRouter(prefix="/api", tags=["chat"])


class NewConversation(BaseModel):
    contract_ids: list[str] = Field(min_length=1)
    title: str | None = None


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


def _require(conversation_id: str) -> dict:
    conv = db.query_one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.get("/conversations")
def list_conversations():
    rows = db.query("SELECT * FROM conversations ORDER BY updated_at DESC")
    for r in rows:
        r["message_count"] = db.scalar("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (r["id"],))
    return rows


@router.post("/conversations", status_code=201)
def create_conversation(body: NewConversation):
    for cid in body.contract_ids:
        row = contracts.get(cid)
        if not row:
            raise HTTPException(400, f"Contract {cid} not found")
        if row["status"] != "indexed":
            raise HTTPException(409, f"{row['name']} is not indexed yet.")
    names = [contracts.get(c)["name"] for c in body.contract_ids]
    conv_id = db.new_id("conv")
    now = db.now_iso()
    db.insert("conversations", {
        "id": conv_id, "title": body.title or f"Questions about {', '.join(names)[:80]}",
        "contract_ids_json": body.contract_ids, "created_at": now, "updated_at": now,
    })
    return get_conversation(conv_id)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    conv = _require(conversation_id)
    conv["messages"] = db.query(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid", (conversation_id,))
    conv["contracts"] = [c for c in (contracts.get(cid) for cid in conv["contract_ids"]) if c]
    return conv


@router.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: Rename):
    _require(conversation_id)
    db.update("conversations", conversation_id, {"title": body.title, "updated_at": db.now_iso()})
    return get_conversation(conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str):
    _require(conversation_id)
    with db.connect() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


@router.post("/conversations/{conversation_id}/messages")
def ask(conversation_id: str, body: NewMessage):
    conv = _require(conversation_id)
    ids = [cid for cid in conv["contract_ids"] if contracts.get(cid)]
    if not ids:
        raise HTTPException(409, "The contracts in this conversation were deleted.")
    history = db.query(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid", (conversation_id,))
    now = db.now_iso()
    user_msg = {"id": db.new_id("msg"), "conversation_id": conversation_id, "role": "user",
                "content": body.content, "created_at": now}
    db.insert("messages", user_msg)
    db.insert("searches", {"id": db.new_id("q"), "query": body.content, "kind": "chat",
                           "conversation_id": conversation_id, "created_at": now})
    result = ai_service.generate_answer(ids, body.content, history)
    reply = {
        "id": db.new_id("msg"), "conversation_id": conversation_id, "role": "assistant",
        "content": result["answer"], "citations_json": result["citations"], "found": int(result["found"]),
        "confidence": result["confidence"], "created_at": db.now_iso(),
    }
    db.insert("messages", reply)
    db.update("conversations", conversation_id, {"updated_at": reply["created_at"]})
    return {"user": user_msg, "assistant": {**{k: v for k, v in reply.items() if k != "citations_json"},
                                            "citations": result["citations"], "mode": result["mode"],
                                            "flags": result.get("flags", [])}}
