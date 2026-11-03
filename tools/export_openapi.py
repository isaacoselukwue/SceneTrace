from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scenetrace.api.app import create_app  # noqa: E402
from scenetrace.config import Settings  # noqa: E402

app = create_app(Settings.load(ROOT, env={"SCENETRACE_DATA_DIR": tempfile.mkdtemp(), "MODEL_API_KEY": ""}), start_worker=False)
target = ROOT / "client" / "src" / "api" / "openapi.json"
target.write_text(json.dumps(app.openapi(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
print(target.relative_to(ROOT))
