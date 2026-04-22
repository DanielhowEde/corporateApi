"""
Payload + JSON Schema store for the programmatic API.

On-disk layout under {PAYLOADS_DIR}:

    {PROJECT}/
        smoke.json          - full Message payload template
        regression.json
        Schemas/
            schema1.json    - JSON Schema (Draft 2020-12 compatible)
            schema2.json

The store exposes discovery (list projects/templates/schemas), loading
(parse JSON from disk), and `validate_payload` — which runs every schema
under {PROJECT}/Schemas/ against the payload. All schemas must pass.
"""

import json
import re
from pathlib import Path

from .utils import setup_logging

logger = setup_logging("payload_store")


class PayloadStoreError(Exception):
    """Raised for missing projects, templates, or unparseable files."""


_PROJECT_RE = re.compile(r"^[A-Z0-9]{3}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SCHEMAS_DIR = "Schemas"


class PayloadStore:
    """Disk-backed store of payload templates and JSON Schemas."""

    def __init__(self, payloads_dir: str | Path | None = None):
        from .config import config

        self.payloads_dir = Path(payloads_dir) if payloads_dir else config.payloads_dir

    # ---- discovery -----------------------------------------------------

    def list_projects(self) -> list[str]:
        """Return project codes (uppercase 3-char dirs) present on disk."""
        if not self.payloads_dir.exists():
            return []
        out = []
        for entry in self.payloads_dir.iterdir():
            if entry.is_dir() and _PROJECT_RE.match(entry.name):
                out.append(entry.name)
        out.sort()
        return out

    def list_payloads(self, project: str) -> list[str]:
        """Return template names (without .json) directly under {PROJECT}/."""
        proj_dir = self._project_dir(project)
        if not proj_dir.exists():
            return []
        out = []
        for entry in proj_dir.iterdir():
            if entry.is_file() and entry.suffix == ".json":
                out.append(entry.stem)
        out.sort()
        return out

    def list_schemas(self, project: str) -> list[str]:
        """Return schema names (without .json) under {PROJECT}/Schemas/."""
        schemas_dir = self._project_dir(project) / _SCHEMAS_DIR
        if not schemas_dir.exists():
            return []
        out = []
        for entry in schemas_dir.iterdir():
            if entry.is_file() and entry.suffix == ".json":
                out.append(entry.stem)
        out.sort()
        return out

    # ---- loading -------------------------------------------------------

    def load_payload(self, project: str, name: str) -> dict:
        """Load and parse a template payload. Raises PayloadStoreError."""
        path = self._template_path(project, name)
        if not path.exists():
            raise PayloadStoreError(f"Template not found: {project}/{name}")
        return self._read_json(path)

    def load_schema(self, project: str, name: str) -> dict:
        """Load and parse a JSON Schema. Raises PayloadStoreError."""
        path = self._schema_path(project, name)
        if not path.exists():
            raise PayloadStoreError(f"Schema not found: {project}/Schemas/{name}")
        return self._read_json(path)

    # ---- validation ----------------------------------------------------

    def validate_payload(self, project: str, payload: dict) -> tuple[bool, list[str]]:
        """
        Validate `payload` against every schema under {PROJECT}/Schemas/.

        All schemas must pass. Returns (ok, errors) where errors is a list
        of "<schema>: <reason>" strings when ok is False.

        A project with no Schemas/ directory (or no .json files in it) is
        rejected — callers must supply at least one schema to opt in.
        """
        try:
            import jsonschema
        except ImportError as exc:
            raise PayloadStoreError(
                "jsonschema package is required. Install: pip install jsonschema"
            ) from exc

        schema_names = self.list_schemas(project)
        if not schema_names:
            return False, [f"No schemas configured for project {project}"]

        errors: list[str] = []
        for name in schema_names:
            try:
                schema = self.load_schema(project, name)
            except PayloadStoreError as exc:
                errors.append(f"{name}: {exc}")
                continue
            try:
                jsonschema.validate(instance=payload, schema=schema)
            except jsonschema.ValidationError as exc:
                errors.append(f"{name}: {exc.message}")
            except jsonschema.SchemaError as exc:
                errors.append(f"{name}: invalid schema ({exc.message})")

        return (len(errors) == 0, errors)

    # ---- internals -----------------------------------------------------

    def _project_dir(self, project: str) -> Path:
        if not _PROJECT_RE.match(project or ""):
            raise PayloadStoreError(f"Invalid project code: {project!r}")
        return self.payloads_dir / project

    def _template_path(self, project: str, name: str) -> Path:
        if not _NAME_RE.match(name or ""):
            raise PayloadStoreError(f"Invalid template name: {name!r}")
        return self._project_dir(project) / f"{name}.json"

    def _schema_path(self, project: str, name: str) -> Path:
        if not _NAME_RE.match(name or ""):
            raise PayloadStoreError(f"Invalid schema name: {name!r}")
        return self._project_dir(project) / _SCHEMAS_DIR / f"{name}.json"

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise PayloadStoreError(f"Failed to read {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise PayloadStoreError(f"{path.name} must contain a JSON object")
        return data
