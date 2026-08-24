#!/usr/bin/env python3
"""File-based document storage for LoopMaster templates.

Replaces AgentDB with simple JSON file storage in .loopmaster/decisions/.

Usage:
    python templates/storage_tool.py store \
        --doc_id "adr-my-project-123" --domain "adr" --content "..."
    python templates/storage_tool.py search --query "auth" --domain "adr"
    python templates/storage_tool.py get --doc_id "adr-my-project-123"
"""

import json
import sys
from datetime import datetime
from pathlib import Path

STORE_DIR = Path(".loopmaster/decisions")


def ensure_store():
    STORE_DIR.mkdir(parents=True, exist_ok=True)


def store_doc(doc_id: str, domain: str, content: str, metadata: str = "{}") -> str:
    ensure_store()
    doc = {
        "id": doc_id,
        "domain": domain,
        "content": content,
        "metadata": json.loads(metadata) if isinstance(metadata, str) else metadata,
        "created_at": datetime.now().isoformat(),
    }
    path = STORE_DIR / f"{doc_id}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    return json.dumps({"ok": True, "id": doc_id, "path": str(path)})


def search_docs(query: str, domain: str = "", limit: int = 10) -> str:
    ensure_store()
    keywords = query.lower().split()
    results = []
    for f in STORE_DIR.glob("*.json"):
        try:
            doc = json.loads(f.read_text())
            if domain and doc.get("domain") != domain:
                continue
            text = (doc.get("content", "") + " " + doc.get("id", "")).lower()
            if all(kw in text for kw in keywords):
                results.append(doc)
                if len(results) >= limit:
                    break
        except (json.JSONDecodeError, OSError):
            continue
    return json.dumps(results, ensure_ascii=False, indent=2)


def get_doc(doc_id: str) -> str:
    path = STORE_DIR / f"{doc_id}.json"
    if not path.exists():
        return json.dumps({"error": f"Document not found: {doc_id}"})
    return path.read_text()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(0)

    action = sys.argv[1]
    args = {}
    i = 2
    while i < len(sys.argv):
        if sys.argv[i].startswith("--") and i + 1 < len(sys.argv):
            args[sys.argv[i][2:]] = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if action == "store":
        result = store_doc(
            args["doc_id"], args["domain"], args["content"], args.get("metadata", "{}")
        )
    elif action == "search":
        result = search_docs(
            args.get("query", ""), args.get("domain", ""), int(args.get("limit", "10"))
        )
    elif action == "get":
        result = get_doc(args["doc_id"])
    else:
        result = json.dumps({"error": f"Unknown action: {action}"})

    sys.stdout.buffer.write(result.encode("utf-8") + b"\n")


if __name__ == "__main__":
    main()
