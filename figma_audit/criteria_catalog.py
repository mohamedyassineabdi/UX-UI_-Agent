from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from figma_audit.config import DEFAULT_CRITERIA_PATH
from figma_audit.models.criteria import CriteriaCatalog


def load_criteria_catalog(path: Path | str | None = None) -> CriteriaCatalog:
    """Load and validate the UX/UI criteria catalog JSON."""
    criteria_path = Path(path) if path is not None else DEFAULT_CRITERIA_PATH

    if not criteria_path.exists():
        raise FileNotFoundError(f"Criteria catalog not found: {criteria_path}")

    with criteria_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    try:
        catalog = CriteriaCatalog.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Criteria catalog is not valid: {criteria_path}") from exc

    reference_errors = catalog.validate_links()
    if reference_errors:
        details = "; ".join(reference_errors)
        raise ValueError(f"Criteria catalog has invalid references: {details}")

    return catalog
