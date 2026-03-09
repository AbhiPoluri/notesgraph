#!/usr/bin/env python3
"""
notesgraph — local-first notes app with graph visualizer.
Accessible from phone over WiFi. No subscription needed.

Storage: ~/.notesgraph/notes.db
Web UI:  http://0.0.0.0:8766
"""

import json
import math
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

# ── Config ─────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".notesgraph" / "notes.db"
MEM_DB_PATH = Path.home() / ".abhimem" / "memory.db"
STATIC_DIR = Path(__file__).parent / "static"
EMBED_MODEL = "nomic-embed-text"

# ── DB setup ────────────────────────────────────────────────────────────────

def get_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            embedding TEXT
        )
    """)
    db.commit()
    return db

# ── Embedding & similarity ───────────────────────────────────────────────────

def embed(text: str):
    try:
        payload = json.dumps({"model": EMBED_MODEL, "input": text[:2000]}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data["embeddings"][0]
    except Exception:
        return None

def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    ma = math.sqrt(sum(x * x for x in a))
    mb = math.sqrt(sum(x * x for x in b))
    return dot / (ma * mb) if ma and mb else 0.0

# ── Wiki link extraction ─────────────────────────────────────────────────────

def extract_links(content: str) -> list[str]:
    """Extract [[linked note titles]] from content."""
    return re.findall(r'\[\[([^\]]+)\]\]', content)

# ── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=str(STATIC_DIR))

@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")

@app.route("/api/notes", methods=["GET"])
def list_notes():
    db = get_db()
    rows = db.execute(
        "SELECT id, title, tags, created_at, updated_at, substr(content, 1, 120) as preview FROM notes ORDER BY updated_at DESC"
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/notes", methods=["POST"])
def create_note():
    data = request.json
    title = data.get("title", "Untitled").strip()
    content = data.get("content", "")
    tags = data.get("tags", "")
    now = int(time.time())
    vec = embed(title + " " + content[:500])
    db = get_db()
    cur = db.execute(
        "INSERT INTO notes (title, content, tags, created_at, updated_at, embedding) VALUES (?, ?, ?, ?, ?, ?)",
        (title, content, tags, now, now, json.dumps(vec) if vec else None)
    )
    db.commit()
    note_id = cur.lastrowid
    db.close()
    return jsonify({"id": note_id, "title": title, "content": content, "tags": tags, "created_at": now, "updated_at": now})

@app.route("/api/notes/<int:note_id>", methods=["GET"])
def get_note(note_id):
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    db.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))

@app.route("/api/notes/<int:note_id>", methods=["PUT"])
def update_note(note_id):
    data = request.json
    now = int(time.time())
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Not found"}), 404
    title = data.get("title", row["title"])
    content = data.get("content", row["content"])
    tags = data.get("tags", row["tags"])
    vec = embed(title + " " + content[:500])
    db.execute(
        "UPDATE notes SET title=?, content=?, tags=?, updated_at=?, embedding=? WHERE id=?",
        (title, content, tags, now, json.dumps(vec) if vec else row["embedding"], note_id)
    )
    db.commit()
    db.close()
    return jsonify({"id": note_id, "title": title, "content": content, "tags": tags, "updated_at": now})

@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    db = get_db()
    db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    # Text search
    db = get_db()
    rows = db.execute(
        "SELECT id, title, tags, updated_at, substr(content,1,120) as preview FROM notes WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC LIMIT 20",
        (f"%{q}%", f"%{q}%")
    ).fetchall()
    results = [dict(r) for r in rows]
    # Semantic search if Ollama available
    q_vec = embed(q)
    if q_vec:
        all_rows = db.execute("SELECT id, title, tags, updated_at, substr(content,1,120) as preview, embedding FROM notes WHERE embedding IS NOT NULL").fetchall()
        scored = []
        seen_ids = {r["id"] for r in results}
        for row in all_rows:
            if row["id"] in seen_ids:
                continue
            emb = json.loads(row["embedding"])
            sim = cosine_sim(q_vec, emb)
            if sim > 0.6:
                d = dict(row)
                d["similarity"] = sim
                scored.append(d)
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        results.extend(scored[:5])
    db.close()
    return jsonify(results)

@app.route("/api/graph")
def graph():
    db = get_db()
    notes = db.execute("SELECT id, title, tags, embedding FROM notes").fetchall()
    db.close()

    nodes = [{"id": r["id"], "title": r["title"], "tags": r["tags"] or ""} for r in notes]

    # Build edges: wikilinks + semantic similarity
    edges = []
    title_to_id = {r["title"].lower(): r["id"] for r in notes}

    # Re-fetch content for wikilinks
    db = get_db()
    all_notes = db.execute("SELECT id, title, content, embedding FROM notes").fetchall()
    db.close()

    for note in all_notes:
        # Wikilinks
        links = extract_links(note["content"])
        for link in links:
            target_id = title_to_id.get(link.lower())
            if target_id and target_id != note["id"]:
                edges.append({"source": note["id"], "target": target_id, "type": "link", "weight": 1.0})

        # Semantic similarity (only if embedding exists)
        if note["embedding"]:
            emb_a = json.loads(note["embedding"])
            for other in all_notes:
                if other["id"] <= note["id"] or not other["embedding"]:
                    continue
                emb_b = json.loads(other["embedding"])
                sim = cosine_sim(emb_a, emb_b)
                if sim > 0.75:
                    edges.append({"source": note["id"], "target": other["id"], "type": "similar", "weight": round(sim, 3)})

    return jsonify({"nodes": nodes, "edges": edges})

@app.route("/api/memories", methods=["GET"])
def list_memories():
    if not MEM_DB_PATH.exists():
        return jsonify([])
    try:
        db = sqlite3.connect(str(MEM_DB_PATH))
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, text, category, created_at FROM memories ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        db.close()
        return jsonify([dict(r) for r in rows])
    except Exception:
        return jsonify([])

if __name__ == "__main__":
    print(f"notesgraph running at http://0.0.0.0:8766")
    print(f"On your phone: http://[your-mac-ip]:8766")
    app.run(host="0.0.0.0", port=8766, debug=False)
