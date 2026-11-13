import json
from pathlib import Path

from .models import ApiTypes

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "frontend/src/schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            ApiTypes.model_json_schema(mode="serialization"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
