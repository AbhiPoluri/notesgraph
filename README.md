# notesgraph

A local-first notes app with a graph visualizer — and a memory layer for your Claude AI sessions.

Write in Markdown, link notes with `[[wikilinks]]`, and watch your knowledge form a graph. If you use [abhimem](https://github.com/AbhiPoluri/abhimem), your Claude memories show up as a separate layer on the same graph — so you can see how your notes and your AI's memory relate to each other.

No accounts. No cloud. No subscriptions.

![python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat)
![flask](https://img.shields.io/badge/flask-3.0-green?style=flat)

---

## Features

- **Markdown editor** with inline image rendering
- **Graph view** — notes as nodes, wikilinks and semantic similarity as edges
- **Wikilinks** — `[[Note Title]]` creates connections between notes
- **abhimem integration** — memories from your Claude sessions appear as orange nodes on the same graph
- **Semantic search** — finds related notes by meaning, not just keywords (requires [Ollama](https://ollama.ai))
- **File & zip upload** — drag-drop files or zip folders to bulk-import as notes
- **WiFi access** — toggle on to reach the app from your phone on the same network
- **Zero cloud** — all data stays local in `~/.notesgraph/notes.db` (SQLite)

---

## Setup

**1. Install dependencies**

```bash
pip install flask werkzeug
```

**2. (Optional) Install Ollama for semantic search and graph similarity**

Download from [ollama.ai](https://ollama.ai), then:

```bash
ollama pull nomic-embed-text
```

Without Ollama, the app still works — search falls back to text matching and the graph shows wikilinks only.

**3. Run**

```bash
python3 server.py
```

Open [http://localhost:8766](http://localhost:8766) in your browser.

**From your phone:** enable WiFi mode in the app footer, then open `http://<your-mac-ip>:8766`.

---

## Usage

| Action | How |
|--------|-----|
| New note | Click **+ New** in the sidebar |
| Link notes | Type `[[Note Title]]` in the editor |
| Tags | Comma-separated in the tags field |
| Search | Type in the search bar — text + semantic |
| Graph view | Click the **Graph** tab |
| Upload files | Drag-drop a file or zip onto the uploads panel |
| WiFi toggle | Enable in the footer to access from your phone |
| Save | `Cmd+S` / `Ctrl+S` (or autosaves on edit) |

### Graph view

- **Purple/colored nodes** — your notes (color = first tag)
- **Orange nodes** — abhimem memories (if abhimem is running)
- **Solid lines** — wikilinks between notes
- **Dashed lines** — semantically similar notes or memories (requires Ollama)

---

## abhimem integration

[abhimem](https://github.com/AbhiPoluri/abhimem) is a persistent memory layer for Claude Code — it extracts facts from your sessions, embeds them, and stores them locally. notesgraph reads from the same database and renders those memories as nodes on the graph.

To use this integration:
1. Install and run abhimem: see [github.com/AbhiPoluri/abhimem](https://github.com/AbhiPoluri/abhimem)
2. Run notesgraph — memories auto-appear in the Graph tab as orange nodes

No configuration needed if both use default paths.

---

## Stack

- **Backend:** Python 3 + Flask
- **Frontend:** Vanilla JS + D3.js (graph) + marked.js (Markdown)
- **Storage:** SQLite (`~/.notesgraph/notes.db`)
- **Embeddings:** Ollama + nomic-embed-text (optional)
