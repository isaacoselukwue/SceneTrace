from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

ZERO = "0" * 64
CONTROL_FILES = {"manifest.json", "commits.json", "HEAD"}


def canonical_bytes(value: Any) -> bytes:
    def check(item: Any) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict:
            for key, child in item.items():
                if not isinstance(key, str) or not key.isascii():
                    raise ValueError("Canonical keys must be ASCII strings")
                check(child)
            return
        raise ValueError("Canonical data permits no floats or custom objects")
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if not relative or "\\" in relative or posix.is_absolute() or any(p in (".", "..") for p in posix.parts):
        raise ValueError("Unsafe bundle path")
    if relative != posix.as_posix():
        raise ValueError("Non-canonical bundle path")
    path = root.joinpath(*posix.parts)
    for component in [path, *path.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError("Symlinks are not permitted")
    path.resolve().relative_to(root.resolve())
    return path


def read_json(path: Path) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    raw = path.read_bytes()
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
    if raw != canonical_bytes(parsed):
        raise ValueError("Control JSON is not canonical")
    return parsed


def seal_bundle(root: Path, event_payloads: list[dict[str, Any]]) -> str:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Bundle directory does not exist")
    if any((root / name).exists() for name in CONTROL_FILES):
        raise ValueError("Refusing to overwrite a sealed bundle")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinks are not permitted")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files[relative] = digest(safe_path(root, relative).read_bytes())
    manifest = {"schema_version": 1, "files": files}
    manifest_digest = digest(canonical_bytes(manifest))
    commits: list[dict[str, Any]] = []
    parent = ZERO
    payloads = list(event_payloads) + [{"kind": "seal", "manifest_sha256": manifest_digest}]
    for sequence, payload in enumerate(payloads):
        refs = payload.get("evidence_paths", [])
        if not isinstance(refs, list) or any(not isinstance(p, str) for p in refs):
            raise ValueError("Invalid evidence paths")
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate evidence path")
        body = {"sequence": sequence, "parent": parent, "payload": payload,
                "evidence_hashes": {p: files[p] for p in refs}}
        commit_digest = digest(canonical_bytes(body))
        commits.append({**body, "hash": commit_digest})
        parent = commit_digest
    (root / "manifest.json").write_bytes(canonical_bytes(manifest))
    (root / "commits.json").write_bytes(canonical_bytes(commits))
    (root / "HEAD").write_text(parent + "\n", encoding="ascii")
    return parent


def verify_bundle(root: Path, expected_head: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    root = root.resolve()
    try:
        manifest = read_json(safe_path(root, "manifest.json"))
        commits = read_json(safe_path(root, "commits.json"))
        head_raw = safe_path(root, "HEAD").read_bytes()
        head = head_raw.decode("ascii").strip()
        if head_raw != (head + "\n").encode("ascii"):
            raise ValueError("HEAD is not canonical")
        if not re.fullmatch(r"[0-9a-f]{64}", head):
            raise ValueError("Invalid HEAD")
        if set(manifest) != {"schema_version", "files"} or manifest["schema_version"] != 1:
            raise ValueError("Invalid manifest")
        if not isinstance(manifest["files"], dict) or not isinstance(commits, list) or not commits:
            raise ValueError("Invalid bundle structure")
        expected_paths = set(manifest["files"])
        actual_paths: set[str] = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlink in bundle")
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if relative not in CONTROL_FILES:
                    actual_paths.add(relative)
        if actual_paths != expected_paths:
            errors.append("FILE_INVENTORY_MISMATCH")
        for relative, expected in manifest["files"].items():
            path = safe_path(root, relative)
            if not path.is_file() or digest(path.read_bytes()) != expected:
                errors.append(f"FILE_HASH_MISMATCH:{relative}")
        parent = ZERO
        for index, commit in enumerate(commits):
            if set(commit) != {"sequence", "parent", "payload", "evidence_hashes", "hash"}:
                raise ValueError("Invalid commit fields")
            body = {key: value for key, value in commit.items() if key != "hash"}
            if commit["sequence"] != index or commit["parent"] != parent:
                errors.append(f"CHAIN_LINK_MISMATCH:{index}")
            if digest(canonical_bytes(body)) != commit["hash"]:
                errors.append(f"COMMIT_HASH_MISMATCH:{index}")
            refs = commit["payload"].get("evidence_paths", [])
            if set(refs) != set(commit["evidence_hashes"]):
                errors.append(f"EVIDENCE_INVENTORY_MISMATCH:{index}")
            for relative, expected in commit["evidence_hashes"].items():
                if manifest["files"].get(relative) != expected:
                    errors.append(f"EVIDENCE_REFERENCE_MISMATCH:{index}")
            parent = commit["hash"]
        if commits[-1]["payload"] != {"kind": "seal", "manifest_sha256": digest(canonical_bytes(manifest))}:
            errors.append("SEAL_MISMATCH")
        if parent != head:
            errors.append("HEAD_MISMATCH")
        if expected_head is not None and expected_head != head:
            errors.append("TRUST_ANCHOR_MISMATCH")
        return {"integrity": "failed" if errors else "valid", "anchored": expected_head is not None,
                "head": head, "errors": errors}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"integrity": "failed", "anchored": expected_head is not None,
                "head": None, "errors": errors + [f"MALFORMED_BUNDLE:{type(exc).__name__}"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a SceneTrace reference bundle")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--expected-head")
    args = parser.parse_args()
    result = verify_bundle(args.bundle, args.expected_head)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["integrity"] == "valid" else 1)


if __name__ == "__main__":
    main()
