#!/usr/bin/env python3
"""
notesgraph — local-first notes app with graph visualizer.
Accessible from phone over WiFi. No subscription needed.

Storage: ~/.notesgraph/notes.db
Web UI:  http://0.0.0.0:8766
"""

import argparse
import io
import json
import math
import os
import re
import socket
import sqlite3
import time
import urllib.request
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

# ── Config ─────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".notesgraph" / "notes.db"
MEM_DB_PATH = Path.home() / ".abhimem" / "memory.db"
STATIC_DIR = Path(__file__).parent / "static"
ATTACHMENTS_DIR = Path.home() / ".notesgraph" / "attachments"
EMBED_MODEL = "nomic-embed-text"

ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

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

wifi_enabled = False  # default: local only
LOCALHOST_ADDRS = {"127.0.0.1", "::1"}

@app.before_request
def check_wifi_lock():
    if not wifi_enabled and request.remote_addr not in LOCALHOST_ADDRS:
        if request.path.startswith("/api/"):
            return jsonify({"error": "WiFi access is disabled"}), 403
        return "WiFi access is disabled. Enable it in notesgraph.", 403

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

@app.route("/api/memory-edges")
def memory_edges():
    if not MEM_DB_PATH.exists():
        return jsonify([])
    try:
        db = sqlite3.connect(str(MEM_DB_PATH))
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"
        ).fetchall()
        db.close()
    except Exception:
        return jsonify([])

    data = [(r["id"], json.loads(r["embedding"])) for r in rows]
    edges = []
    for i, (id_a, emb_a) in enumerate(data):
        for id_b, emb_b in data[i+1:]:
            sim = cosine_sim(emb_a, emb_b)
            if sim > 0.72:
                edges.append({
                    "source": f"m{id_a}",
                    "target": f"m{id_b}",
                    "type": "memory-similar",
                    "weight": round(sim, 3)
                })
    return jsonify(edges)


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

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(f.filename)
    raw = f.read()

    # Decode text content
    for enc in ("utf-8", "latin-1"):
        try:
            content = raw.decode(enc)
            break
        except Exception:
            content = None
    if content is None:
        return jsonify({"error": "File is not readable text"}), 400

    # Strip leading/trailing whitespace, use filename (without extension) as title
    stem = Path(filename).stem or filename
    title = stem.replace("-", " ").replace("_", " ").strip()
    now = int(time.time())
    vec = embed(title + " " + content[:500])
    db = get_db()
    cur = db.execute(
        "INSERT INTO notes (title, content, tags, created_at, updated_at, embedding) VALUES (?, ?, ?, ?, ?, ?)",
        (title, content, "uploaded", now, now, json.dumps(vec) if vec else None)
    )
    db.commit()
    note_id = cur.lastrowid
    db.close()
    return jsonify({"id": note_id, "title": title, "size": len(raw)})


TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".py",
                   ".js", ".ts", ".html", ".css", ".yaml", ".yml", ".sh",
                   ".toml", ".xml", ".log", ".tex", ".sql", ".r", ".rb", ".go",
                   ".java", ".c", ".cpp", ".h", ".swift", ".kt"}

@app.route("/api/upload-zip", methods=["POST"])
def upload_zip():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    raw = f.read()
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return jsonify({"error": "Not a valid zip file"}), 400

    created = []
    skipped = []
    db = get_db()
    now = int(time.time())

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or name.startswith("__MACOSX") or name.startswith("."):
                continue
            ext = Path(name).suffix.lower()
            if ext not in TEXT_EXTENSIONS:
                skipped.append(name)
                continue
            try:
                content_bytes = zf.read(name)
                content = None
                for enc in ("utf-8", "latin-1"):
                    try:
                        content = content_bytes.decode(enc)
                        break
                    except Exception:
                        pass
                if content is None:
                    skipped.append(name)
                    continue
                stem = Path(name).stem or Path(name).name
                title = stem.replace("-", " ").replace("_", " ").strip()
                if not title:
                    continue
                vec = embed(title + " " + content[:500])
                cur = db.execute(
                    "INSERT INTO notes (title, content, tags, created_at, updated_at, embedding) VALUES (?, ?, ?, ?, ?, ?)",
                    (title, content, "zip-upload", now, now, json.dumps(vec) if vec else None)
                )
                db.commit()
                created.append({"id": cur.lastrowid, "title": title})
            except Exception:
                skipped.append(name)

    db.close()
    return jsonify({"created": created, "skipped": skipped, "count": len(created)})


@app.route("/api/attachments", methods=["POST"])
def upload_attachment():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    filename = secure_filename(f.filename)
    # Make filename unique with timestamp prefix
    unique_name = f"{int(time.time() * 1000)}_{filename}"
    dest = ATTACHMENTS_DIR / unique_name
    f.save(str(dest))
    url = f"/attachments/{unique_name}"
    ext = Path(filename).suffix.lower()
    is_image = ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
    return jsonify({"url": url, "filename": filename, "is_image": is_image})

@app.route("/api/attachments", methods=["GET"])
def list_attachments():
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
    files = []
    for p in sorted(ATTACHMENTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            ext = p.suffix.lower()
            # Strip timestamp prefix to get original name
            name = p.name
            original = "_".join(name.split("_")[1:]) if "_" in name else name
            files.append({
                "filename": name,
                "original": original,
                "url": f"/attachments/{name}",
                "is_image": ext in IMAGE_EXTS,
                "size": p.stat().st_size,
                "created_at": int(p.stat().st_mtime),
            })
    return jsonify(files)

@app.route("/attachments/<path:filename>")
def serve_attachment(filename):
    resp = send_from_directory(str(ATTACHMENTS_DIR), filename)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/server-info")
def server_info():
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        lan_ip = None
    port = app.config.get("PORT", 8766)
    return jsonify({"wifi_enabled": wifi_enabled, "lan_ip": lan_ip, "port": port})

@app.route("/api/wifi", methods=["POST"])
def toggle_wifi():
    global wifi_enabled
    data = request.json or {}
    if "enabled" in data:
        wifi_enabled = bool(data["enabled"])
    else:
        wifi_enabled = not wifi_enabled
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        lan_ip = None
    return jsonify({"wifi_enabled": wifi_enabled, "lan_ip": lan_ip})


def ensure_ssl_cert(cert_path: Path, key_path: Path):
    """Generate a self-signed cert using Python's cryptography library."""
    if cert_path.exists() and key_path.exists():
        return
    print("Generating self-signed SSL certificate...")
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509 import IPAddress
        import ipaddress, datetime
    except ImportError:
        raise SystemExit(
            "Missing dependency: run  uv run --with flask,werkzeug,cryptography python3 server.py --https\n"
            "or: pip install cryptography"
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "notesgraph")])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                IPAddress(ipaddress.IPv6Address("::1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Certificate saved to {cert_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wifi", action="store_true", help="Start with WiFi access enabled (default: local only)")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--https", action="store_true", help="Enable HTTPS with a self-signed certificate")
    args = parser.parse_args()

    if args.wifi:
        wifi_enabled = True

    app.config["PORT"] = args.port

    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        lan_ip = "your-mac-ip"

    ssl_context = None
    scheme = "http"
    if args.https:
        cert_path = DB_PATH.parent / "cert.pem"
        key_path = DB_PATH.parent / "key.pem"
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        ensure_ssl_cert(cert_path, key_path)
        ssl_context = (str(cert_path), str(key_path))
        scheme = "https"

    print(f"notesgraph running at {scheme}://localhost:{args.port}")
    if wifi_enabled:
        print(f"WiFi: {scheme}://{lan_ip}:{args.port}")
    else:
        print("WiFi: disabled (toggle in app to enable)")
    if args.https:
        print("HTTPS enabled (self-signed cert — accept the browser warning once)")
    app.run(host="0.0.0.0", port=args.port, debug=False, ssl_context=ssl_context)
