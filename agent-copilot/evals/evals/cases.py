"""Case loading + JSON-schema validation."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

from evals import CASES_DIR

SCHEMA = json.loads((CASES_DIR / "case.schema.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


class CaseError(ValueError):
    pass


def validate_case(case: dict, where: str = "<case>") -> dict:
    errs = sorted(_VALIDATOR.iter_errors(case), key=lambda e: list(e.path))
    if errs:
        msg = "; ".join(f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in errs[:5])
        raise CaseError(f"{where}: {msg}")
    return case


def load_file(path: Path) -> list[dict]:
    docs = yaml.safe_load(path.read_text())
    docs = docs if isinstance(docs, list) else [docs]
    return [validate_case(d, f"{path.name}[{i}]") for i, d in enumerate(docs)]


def load_cases(suite: str = "golden", directory: Path = CASES_DIR) -> list[dict]:
    out = []
    for p in sorted(directory.glob("*.yaml")):
        out += load_file(p)
    ids = [c["id"] for c in out]
    if len(ids) != len(set(ids)):
        raise CaseError("duplicate case ids")
    return out if suite == "all" else [c for c in out if c["suite"] == suite]
