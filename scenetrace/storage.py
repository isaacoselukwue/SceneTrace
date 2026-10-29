"""Private media layout and registered assets.

Paths are generated from server IDs only. Assets are served by ID after an owner
check, a containment check and a symlink check; the storage root is never
exposed as a static directory.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from .db import Database
from .errors import not_found
from .ids import new_id, utc_now

SAFE_ID = re.compile(r"^[a-z]+_[0-9a-f]{32}$|^owner_[a-z0-9]{1,40}$|^public$")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def _check_id(value: str) -> str:
    if not SAFE_ID.match(value):
        raise ValueError(f"unsafe identifier {value!r}")
    return value


class Storage:
    def __init__(self, data_dir: Path, db: Database) -> None:
        self.root = data_dir.resolve()
        self.db = db
        for sub in ("private", "public-demos", "tmp", "db"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # Layout ---------------------------------------------------------------
    def clip_dir(self, owner_id: str, clip_id: str) -> Path:
        return self.root / "private" / _check_id(owner_id) / _check_id(clip_id)

    def source_dir(self, owner_id: str, clip_id: str) -> Path:
        return self.clip_dir(owner_id, clip_id) / "source"

    def proxy_dir(self, owner_id: str, clip_id: str) -> Path:
        return self.clip_dir(owner_id, clip_id) / "proxy"

    def analysis_dir(self, owner_id: str, clip_id: str, analysis_id: str) -> Path:
        return self.clip_dir(owner_id, clip_id) / "analysis" / _check_id(analysis_id)

    def export_dir(self, owner_id: str, clip_id: str, export_id: str) -> Path:
        return self.clip_dir(owner_id, clip_id) / "exports" / _check_id(export_id)

    def demo_dir(self, demo_id: str) -> Path:
        return self.root / "public-demos" / _check_id(demo_id)

    def tmp_dir(self) -> Path:
        path = self.root / "tmp" / new_id("req")
        path.mkdir(parents=True)
        return path

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def contained(self, rel_path: str) -> Path:
        """Resolve a stored relative path, refusing traversal and symlinks."""
        if rel_path.startswith("/") or "\\" in rel_path or any(p in ("", ".", "..") for p in rel_path.split("/")):
            raise ValueError("unsafe stored path")
        path = self.root.joinpath(*rel_path.split("/"))
        current = path
        while current != self.root:
            if current.is_symlink():
                raise ValueError("symlink in storage path")
            current = current.parent
        path.resolve().relative_to(self.root)
        return path

    # Assets ---------------------------------------------------------------
    def register_asset(self, *, owner_id: str, clip_id: str, path: Path, kind: str, media_type: str,
                       derived: bool, analysis_id: str | None = None, public: bool | None = None) -> str:
        if path.is_symlink() or not path.is_file():
            raise ValueError("asset must be a regular file")
        rel = self.relative(path)
        self.contained(rel)
        public = (owner_id == "public") if public is None else public
        digest = sha256_file(path)
        asset_id = new_id("ast")
        with self.db.write() as conn:
            existing = conn.execute("SELECT id, sha256 FROM assets WHERE owner_id = ? AND rel_path = ?", (owner_id, rel)).fetchone()
            if existing is not None:
                if existing["sha256"] != digest:
                    raise ValueError("registered asset bytes changed; assets are immutable")
                return str(existing["id"])
            conn.execute(
                "INSERT INTO assets(id, owner_id, clip_id, analysis_id, kind, rel_path, sha256, bytes, media_type, derived, public, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (asset_id, owner_id, clip_id, analysis_id, kind, rel, digest, path.stat().st_size, media_type,
                 int(derived), int(public), utc_now()))
        return asset_id

    def asset_row(self, asset_id: str, owner_id: str | None, *, allow_public: bool = True):
        row = self.db.one("SELECT * FROM assets WHERE id = ? AND revoked_at IS NULL", (asset_id,))
        if row is None:
            raise not_found("asset")
        if row["owner_id"] != owner_id and not (allow_public and row["public"]):
            raise not_found("asset")
        return row

    def asset_path(self, asset_id: str, owner_id: str | None, *, verify_digest: bool = False) -> tuple[Path, str]:
        row = self.asset_row(asset_id, owner_id)
        path = self.contained(row["rel_path"])
        if not path.is_file():
            raise not_found("asset")
        if verify_digest and sha256_file(path) != row["sha256"]:
            raise ValueError("ASSET_DIGEST_MISMATCH")
        return path, str(row["media_type"])

    def remove_tree(self, path: Path) -> None:
        path.resolve().relative_to(self.root)
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
