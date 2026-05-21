from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_raw_bundle(path: Path, bundle: Any) -> None:
    if hasattr(bundle, "model_dump"):
        data = bundle.model_dump(mode="json")
    else:
        data = bundle

    save_json(path, data)


def save_normalized_file(path: Path, normalized: Any) -> None:
    if hasattr(normalized, "model_dump"):
        data = normalized.model_dump(mode="json")
    else:
        data = normalized

    save_json(path, data)


def split_file_by_lines(
    input_path: Path,
    output_dir: Path,
    parts: int = 10,
    output_prefix: str = "audit_part",
    log: Callable[[str], None] | None = None,
) -> list[Path]:
    """
    Split any text file into N parts by line count.

    This does NOT parse JSON structure.
    It simply:
    - reads all lines
    - divides total lines by parts
    - writes each chunk into a .txt file

    Useful for debugging very large JSON files.
    """
    if parts <= 0:
        raise ValueError("parts must be greater than 0.")

    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    with input_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    total_lines = len(lines)

    if total_lines == 0:
        if log is not None:
            log("No lines to split.")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)

    chunk_size = max(1, (total_lines + parts - 1) // parts)

    written_files = 0
    output_paths: list[Path] = []

    for i in range(parts):
        start = i * chunk_size
        end = min(start + chunk_size, total_lines)

        if start >= total_lines:
            break

        chunk_lines = lines[start:end]
        output_path = output_dir / f"{output_prefix}_{i + 1}.txt"

        with output_path.open("w", encoding="utf-8") as f:
            f.writelines(chunk_lines)

        written_files += 1
        output_paths.append(output_path)

    if log is not None:
        log(
            f"Split {total_lines} lines from {input_path.name} into "
            f"{written_files} text files in {output_dir}"
        )

    return output_paths
