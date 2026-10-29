import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reference_core.models import AnswerDraft, AuditResult, Claim, EventRecord, Evidence, Plan  # noqa: E402

for model in [AnswerDraft, AuditResult, Claim, EventRecord, Evidence, Plan]:
    path = ROOT / "contracts" / f"{model.__name__}.schema.json"
    path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(path.relative_to(ROOT))
