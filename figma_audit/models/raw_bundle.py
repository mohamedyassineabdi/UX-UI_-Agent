from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RawFigmaBundle(BaseModel):
    """
    This model represents the output of Layer A (Ingestion).

    It contains:
    - the original source URL
    - identifiers extracted from the URL
    - raw JSON responses from Figma API
    - non-blocking warnings (e.g., variables not available)

    This object is passed directly into Layer B (Normalization).
    """

    # Input metadata
    source_url: str
    file_key: str
    node_id: str | None = None

    # Raw Figma API responses
    raw_file: dict[str, Any]
    raw_variables: dict[str, Any] | None = None

    # Non-critical issues (do not stop execution)
    warnings: list[str] = Field(default_factory=list)

    def has_variables(self) -> bool:
        """
        Helper to quickly check if variables were successfully fetched.
        """
        return self.raw_variables is not None

    def summary(self) -> dict[str, Any]:
        """
        Lightweight summary for debugging/logging.
        """
        return {
            "file_key": self.file_key,
            "has_variables": self.has_variables(),
            "warnings_count": len(self.warnings),
        }