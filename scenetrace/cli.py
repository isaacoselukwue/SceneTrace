"""Command line: python -m scenetrace <command>."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scenetrace", description="SceneTrace, created by Isaac Oselukwue.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the API, worker and built client in one process")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=int(os.environ.get("SCENETRACE_PORT", "8765")))
    serve.add_argument("--import-demos", type=Path, nargs="?", const=Path("demos"), default=None,
                       help="Replay approved recorded demonstrations into the data directory before serving")
    verify = sub.add_parser("verify", help="Verify an extracted evidence bundle against a separately trusted head")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--expected-head")
    pub = sub.add_parser("publish-demo", help="Package a completed analysis as an approved recorded demonstration")
    pub.add_argument("analysis_id")
    pub.add_argument("--title", required=True)
    pub.add_argument("--description", required=True)
    pub.add_argument("--approved-by", required=True, help="The person who reviewed the footage for permission and identifying content")
    pub.add_argument("--approval-note", required=True, help="What was reviewed and why publication is acceptable")
    pub.add_argument("--out", type=Path, default=Path("demos"))
    imp = sub.add_parser("import-demos", help="Import approved recorded demonstrations into the data directory")
    imp.add_argument("source", type=Path, nargs="?", default=Path("demos"))
    args = parser.parse_args(argv)

    from .config import Settings
    settings = Settings.load()
    if args.command == "serve":
        import uvicorn

        from .api.app import create_app
        host = args.host or settings.bind_host
        if args.import_demos is not None:
            from .demos import import_demos
            from .jobs.context import Services
            import_demos(Services.create(settings), args.import_demos)
        uvicorn.run(create_app(Settings.load(env={"SCENETRACE_HOST": host})), host=host, port=args.port, workers=1,
                    log_level="warning", proxy_headers=False)
        return 0
    if args.command == "verify":
        from reference_core.integrity import verify_bundle
        result = verify_bundle(args.bundle, args.expected_head)
        print(json.dumps(result, indent=2))
        return 0 if result["integrity"] == "valid" else 1
    from .demos import import_demos, publish_demo
    from .jobs.context import Services
    svc = Services.create(settings)
    if args.command == "publish-demo":
        path = publish_demo(svc, args.analysis_id, title=args.title, description=args.description,
                            approved_by=args.approved_by, approval_note=args.approval_note, out_dir=args.out)
        print(path)
        return 0
    if args.command == "import-demos":
        print(json.dumps(import_demos(svc, args.source), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
