"""CodeBlockStore mixin — immutable, sha256-pinned code blocks."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from loopmaster.mcp.store_models import (
    _CAPABILITY_RE,
    _CODE_NAME_RE,
    _SEMVER_RE,
    BLOCK_LANGUAGES,
    CodeBlockData,
    StoreHost,
    _split_block_ref,
    row_to_code_block,
)


class CodeBlockStoreMixin(StoreHost):
    """CRUD for the code_blocks table."""

    def save_code_block(
        self,
        name: str,
        version: str,
        language: str,
        source: str,
        capabilities: list[str] | None = None,
        description: str = "",
        entrypoint: str = "main",
    ) -> CodeBlockData:
        """Register an immutable code block. Re-registering the same
        (name, version) raises ValueError — publish a new version instead."""
        if not isinstance(name, str) or not _CODE_NAME_RE.match(name):
            raise ValueError(f"invalid code block name {name!r} (expected kebab-case)")
        if not isinstance(version, str) or not _SEMVER_RE.match(version):
            raise ValueError(f"invalid version {version!r} (expected semantic X.Y.Z)")
        if language not in BLOCK_LANGUAGES:
            raise ValueError(
                f"unsupported language {language!r} (expected one of {sorted(BLOCK_LANGUAGES)})"
            )
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        caps = [str(c) for c in (capabilities or [])]
        for cap in caps:
            if not _CAPABILITY_RE.match(cap):
                raise ValueError(
                    f"invalid capability {cap!r} "
                    "(expected 'net', 'fs:read:<prefix>' or 'fs:write:<prefix>')"
                )
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO code_blocks (
                        name, version, language, entrypoint, source, sha256,
                        capabilities_json, description, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        version,
                        language,
                        entrypoint or "main",
                        source,
                        digest,
                        json.dumps(caps),
                        description,
                        time.time(),
                    ),
                )
                self.conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"code block '{name}@{version}' already exists — register a new version instead"
                ) from exc
            finally:
                cur.close()
        return CodeBlockData(
            name=name,
            version=version,
            language=language,
            source=source,
            sha256=digest,
            entrypoint=entrypoint or "main",
            capabilities=caps,
            description=description,
        )

    def get_code_block(self, ref: str, version: str | None = None) -> CodeBlockData | None:
        """Fetch a code block by 'name@X.Y.Z' ref, or by name + version."""
        name, ref_version = _split_block_ref(ref)
        resolved_version = version or ref_version
        with self._lock:
            cur = self.conn.cursor()
            if resolved_version is None:
                cur.execute(
                    "SELECT * FROM code_blocks WHERE name = ? ORDER BY id DESC LIMIT 1",
                    (name,),
                )
            else:
                cur.execute(
                    "SELECT * FROM code_blocks WHERE name = ? AND version = ?",
                    (name, resolved_version),
                )
            row = cur.fetchone()
            cur.close()
        return row_to_code_block(row) if row else None

    def list_code_blocks(
        self, pattern: str | None = None, limit: int = 100, include_source: bool = False
    ) -> list[CodeBlockData]:
        """List code blocks (newest first), optionally filtered by name substring."""
        with self._lock:
            cur = self.conn.cursor()
            if pattern:
                cur.execute(
                    "SELECT * FROM code_blocks WHERE name LIKE ? ORDER BY created_at DESC LIMIT ?",
                    (f"%{pattern}%", int(limit)),
                )
            else:
                cur.execute(
                    "SELECT * FROM code_blocks ORDER BY created_at DESC LIMIT ?",
                    (int(limit),),
                )
            rows = cur.fetchall()
            cur.close()
        blocks = []
        for row in rows:
            block = row_to_code_block(row)
            if not include_source:
                block.source = ""
            blocks.append(block)
        return blocks

    def delete_code_block(self, name: str, version: str) -> bool:
        """Delete a specific code block version. Returns True when removed."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM code_blocks WHERE name = ? AND version = ?", (name, version))
            deleted = cur.rowcount > 0
            self.conn.commit()
            cur.close()
        return deleted

    def verify_code_block(self, ref: str, version: str | None = None) -> bool:
        """Recompute the sha256 of the stored source and compare with the pin."""
        block = self.get_code_block(ref, version)
        if block is None:
            return False
        return hashlib.sha256(block.source.encode("utf-8")).hexdigest() == block.sha256
