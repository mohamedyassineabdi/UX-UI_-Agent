from __future__ import annotations

from collections.abc import Callable

from figma_audit.config import FIGMA_FETCH_VARIABLES
from figma_audit.ingestion.figma_client import FigmaApiError, FigmaClient
from figma_audit.ingestion.url_parser import parse_figma_url
from figma_audit.models.raw_bundle import RawFigmaBundle


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)


def _file_like_node_response(raw_nodes: dict[str, object], node_id: str, document: object) -> dict[str, object]:
    raw_file = {
        key: value
        for key, value in raw_nodes.items()
        if key != "nodes"
    }
    raw_file["name"] = raw_file.get("name") or f"Node {node_id}"
    raw_file["document"] = document
    return raw_file


def fetch_figma_bundle(
    figma_url: str,
    *,
    fetch_variables: bool | None = None,
    log: Callable[[str], None] | None = None,
) -> RawFigmaBundle:
    """
    Layer A orchestration function.

    Steps:
    1. Parse the Figma URL
    2. Fetch raw file JSON or a specific node
    3. If node-based fetch is used, reshape the response so the normalizer can read it
    4. Try to fetch local variables
    5. Return one unified raw bundle object

    Notes:
    - Variables are optional and should not break the pipeline
    - If a node_id is present, we fetch only that node for performance
    - The returned raw_file is always normalized into a file-like structure:
      {
          "document": {...},
          ...
      }
    """
    _emit(log, "Parsing Figma URL...")
    parsed = parse_figma_url(figma_url)

    file_key = parsed["file_key"]
    node_id = parsed["node_id"]
    fetch_variables = FIGMA_FETCH_VARIABLES if fetch_variables is None else fetch_variables

    _emit(log, f"File key: {file_key}")
    _emit(log, f"Node ID: {node_id}")

    client = FigmaClient(log=log)
    warnings: list[str] = []

    try:
        if node_id:
            _emit(log, "Fetching specific node...")
            raw_nodes = client.get_file_nodes(file_key, [node_id])

            node_data = raw_nodes.get("nodes", {}).get(node_id, {})
            document = node_data.get("document")

            if not document:
                raise ValueError(f"No document found for node_id {node_id}")

            raw_file = _file_like_node_response(raw_nodes, node_id, document)
        else:
            _emit(log, "No node_id provided. Fetching full file; this may be slow.")
            raw_file = client.get_file(file_key)

        _emit(log, "Figma file fetched successfully")

    except Exception as exc:
        _emit(log, "Error while fetching Figma file")
        raise exc

    raw_variables = None
    if not fetch_variables:
        _emit(log, "Skipping local variables fetch to keep Figma API usage low.")
        return RawFigmaBundle(
            source_url=figma_url,
            file_key=file_key,
            node_id=node_id,
            raw_file=raw_file,
            raw_variables=raw_variables,
            warnings=warnings,
        )

    try:
        _emit(log, "Fetching local variables...")
        raw_variables = client.get_local_variables(file_key)

        if raw_variables is None:
            warnings.append(
                "Variables endpoint unavailable for this file, token scope, or workspace plan."
            )
            _emit(log, "Variables not available")
        else:
            _emit(log, "Variables fetched")

    except FigmaApiError as exc:
        warnings.append(f"Could not fetch local variables: {exc}")
        _emit(log, f"Variables fetch failed: {exc}")

    return RawFigmaBundle(
        source_url=figma_url,
        file_key=file_key,
        node_id=node_id,
        raw_file=raw_file,
        raw_variables=raw_variables,
        warnings=warnings,
    )
