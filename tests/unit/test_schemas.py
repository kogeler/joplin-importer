"""The committed schemas/ files must match the current models."""

from pathlib import Path

from joplin_importer.schemas import generate_schemas

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def test_schema_files_are_current():
    generated = generate_schemas()
    for name, text in generated.items():
        path = SCHEMAS_DIR / f"{name}.schema.json"
        assert path.exists(), (
            f"missing {path}; regenerate with: "
            ".venv/bin/python -m joplin_importer.schemas schemas"
        )
        assert path.read_text(encoding="utf-8") == text, (
            f"{path} is stale; regenerate with: "
            ".venv/bin/python -m joplin_importer.schemas schemas"
        )


def test_no_orphan_schema_files():
    generated = set(generate_schemas())
    on_disk = {p.name.removesuffix(".schema.json") for p in SCHEMAS_DIR.glob("*.schema.json")}
    assert on_disk == generated
