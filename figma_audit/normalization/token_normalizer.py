from __future__ import annotations

from typing import Any

from figma_audit.models.normalized_models import NormalizedToken


def normalize_tokens(raw_variables: dict[str, Any] | None) -> list[NormalizedToken]:
    """
    Convert raw Figma local variables payload into normalized tokens.

    Expected raw shape:
    {
      "meta": {
        "variables": {...},
        "variableCollections": {...}
      }
    }

    Returns:
    - a sorted list of NormalizedToken
    """
    if not raw_variables:
        return []

    meta = raw_variables.get("meta", {})
    if not isinstance(meta, dict):
        return []

    variables = meta.get("variables", {})
    collections = meta.get("variableCollections", {})

    if not isinstance(variables, dict):
        variables = {}

    if not isinstance(collections, dict):
        collections = {}

    collection_name_by_id: dict[str, str | None] = {
        collection_id: collection_data.get("name")
        for collection_id, collection_data in collections.items()
        if isinstance(collection_data, dict)
    }

    tokens: list[NormalizedToken] = []

    for variable_id, variable_data in variables.items():
        if not isinstance(variable_data, dict):
            continue

        token = NormalizedToken(
            id=variable_id,
            name=variable_data.get("name", ""),
            token_type=variable_data.get("resolvedType", "UNKNOWN"),
            collection_id=variable_data.get("variableCollectionId"),
            collection_name=collection_name_by_id.get(
                variable_data.get("variableCollectionId")
            ),
            values_by_mode=variable_data.get("valuesByMode", {}) or {},
            scopes=variable_data.get("scopes", []) or [],
        )
        tokens.append(token)

    tokens.sort(key=lambda token: ((token.collection_name or ""), token.name))
    return tokens