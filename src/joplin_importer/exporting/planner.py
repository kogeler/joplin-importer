"""Build immutable plans for exporting a complete source snapshot to Joplin."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..models import PageRecord, SnapshotReader, SourceBackend
from ..models.hashing import sha256_canonical_json, sha256_file, sha256_text
from ..models.timeutil import now_utc_iso
from ..normalization import Normalizer
from ..normalization.model import semantic_hash
from .content import build_export_html
from .models import ExportApproval, ExportFolder, ExportNote, ExportPlan


def build_export_plan(
    source: SnapshotReader,
    *,
    tool_version: str,
    conflict_policy: str = "fail",
    target_instance_fingerprint: str = "",
    created_at_utc: str | None = None,
) -> tuple[ExportPlan, dict[str, str]]:
    """Compile every source page; no audit or fuzzy matching is involved."""
    if conflict_policy not in {"fail", "replace-managed"}:
        raise ValueError("conflict policy must be 'fail' or 'replace-managed'")
    if source.manifest.source_backend == SourceBackend.ONENOTE_GRAPH:
        raise ValueError(
            "Microsoft Graph snapshots are experimental analysis sources and cannot be exported"
        )
    if source.manifest.coverage_status != "complete":
        raise ValueError("full export requires a source snapshot with complete coverage")
    checksum_problems = source.verify_checksums()
    if checksum_problems:
        raise ValueError("source snapshot checksum verification failed")

    pages = sorted(source.iter_pages(), key=_page_sort_key)
    if not pages:
        raise ValueError("source snapshot contains no pages")

    root_titles: dict[str, str] = {}
    for page in pages:
        key = page.notebook_id or page.notebook_title
        previous = root_titles.setdefault(page.notebook_title, key)
        if previous != key:
            raise ValueError(
                f"source contains multiple top-level notebooks named {page.notebook_title!r}"
            )

    folders, section_nodes = _build_folders(pages)
    action_specs = [
        _ActionSpec(
            action_id=_action_id(source.manifest.snapshot_id, page.source_page_id),
            source_page_id=page.source_page_id,
            parent_node_id=section_nodes[_section_key(page)],
            title=page.page_title or "Untitled Page",
            semantic=page.semantic_model_sha256 or "",
            resources=sorted(set(page.resource_hashes)),
        )
        for page in pages
    ]
    plan_id = sha256_canonical_json(
        {
            "source_snapshot_id": source.manifest.snapshot_id,
            "source_inventory_hash": source.manifest.inventory_hash,
            "conflict_policy": conflict_policy,
            "content_mode": "mixed-html-markdown",
            "folders": [folder.model_dump(mode="json") for folder in folders],
            "notes": [spec.canonical_dict() for spec in action_specs],
        }
    )[:16]

    normalizer = Normalizer()
    bodies: dict[str, str] = {}
    notes: list[ExportNote] = []
    for page, spec in zip(pages, action_specs, strict=True):
        model = _load_model(source, page, normalizer)
        body = build_export_html(
            page,
            model,
            plan_id=plan_id,
            action_id=spec.action_id,
        )
        bodies[spec.action_id] = body
        notes.append(
            ExportNote(
                action_id=spec.action_id,
                source_page_id=page.source_page_id,
                parent_node_id=spec.parent_node_id,
                title=spec.title,
                expected_body_sha256=sha256_text(body),
                expected_semantic_sha256=semantic_hash(model),
                expected_resource_hashes=spec.resources,
                created_at=page.created_at,
                updated_at=page.updated_at,
                page_order=page.page_order,
            )
        )

    manifest_path = source.snapshot_dir / "manifest.json"
    plan = ExportPlan(
        plan_id=plan_id,
        tool_version=tool_version,
        created_at_utc=created_at_utc or now_utc_iso(),
        conflict_policy=conflict_policy,  # type: ignore[arg-type]
        source_snapshot_id=source.manifest.snapshot_id,
        source_manifest_hash=sha256_file(manifest_path),
        target_instance_fingerprint=target_instance_fingerprint,
        content_mode="mixed-html-markdown",
        folders=folders,
        notes=notes,
    )
    return plan, bodies


def write_export_plan(plan: ExportPlan, bodies: dict[str, str], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    bodies_dir = output.parent / (output.stem + ".bodies")
    bodies_dir.mkdir(exist_ok=True)
    for action_id, body in sorted(bodies.items()):
        (bodies_dir / f"{action_id}.html").write_text(body, encoding="utf-8")
    return output


def load_export_plan(path: Path) -> tuple[ExportPlan, str]:
    return (
        ExportPlan.model_validate_json(path.read_text(encoding="utf-8")),
        sha256_file(path),
    )


def load_export_body(plan_path: Path, action_id: str) -> str:
    return (plan_path.parent / (plan_path.stem + ".bodies") / f"{action_id}.html").read_text(
        encoding="utf-8"
    )


def build_export_approval(plan_path: Path, *, operator: str = "") -> ExportApproval:
    load_export_plan(plan_path)
    return ExportApproval(export_plan_sha256=sha256_file(plan_path), operator=operator)


def load_export_approval(path: Path) -> tuple[ExportApproval, str]:
    return (
        ExportApproval.model_validate_json(path.read_text(encoding="utf-8")),
        sha256_file(path),
    )


def _build_folders(
    pages: list[PageRecord],
) -> tuple[list[ExportFolder], dict[tuple[str, tuple[str, ...], str, str], str]]:
    folders: list[ExportFolder] = []
    node_by_key: dict[tuple[str, ...], str] = {}
    sections: dict[tuple[str, tuple[str, ...], str, str], str] = {}
    pages_by_notebook: dict[str, list[PageRecord]] = defaultdict(list)
    for page in pages:
        pages_by_notebook[page.notebook_id or page.notebook_title].append(page)

    for notebook_key, notebook_pages in sorted(
        pages_by_notebook.items(), key=lambda item: (item[1][0].notebook_title.casefold(), item[0])
    ):
        title = notebook_pages[0].notebook_title or "Untitled Notebook"
        root_key = ("notebook", notebook_key)
        root_id = _folder_id(root_key)
        node_by_key[root_key] = root_id
        folders.append(
            ExportFolder(
                node_id=root_id,
                title=title,
                kind="notebook",
                source_key=notebook_key,
            )
        )

        for page in sorted(notebook_pages, key=_page_sort_key):
            parent_id = root_id
            path_parts: list[str] = []
            for group in page.section_group_path:
                path_parts.append(group)
                group_key = ("group", notebook_key, *path_parts)
                if group_key not in node_by_key:
                    group_id = _folder_id(group_key)
                    node_by_key[group_key] = group_id
                    folders.append(
                        ExportFolder(
                            node_id=group_id,
                            parent_node_id=parent_id,
                            title=group or "Untitled Section Group",
                            kind="section-group",
                            source_key="/".join(path_parts),
                        )
                    )
                parent_id = node_by_key[group_key]

            section_key = _section_key(page)
            if section_key not in sections:
                section_id = _folder_id(
                    (
                        "section",
                        notebook_key,
                        *page.section_group_path,
                        page.section_id,
                        page.section_title,
                    )
                )
                sections[section_key] = section_id
                folders.append(
                    ExportFolder(
                        node_id=section_id,
                        parent_node_id=parent_id,
                        title=page.section_title or "Untitled Section",
                        kind="section",
                        source_key=page.section_id or page.section_title,
                    )
                )
    return folders, sections


def _section_key(page: PageRecord) -> tuple[str, tuple[str, ...], str, str]:
    return (
        page.notebook_id or page.notebook_title,
        tuple(page.section_group_path),
        page.section_id,
        page.section_title,
    )


def _folder_id(key: tuple[str, ...]) -> str:
    return sha256_canonical_json(key)[:16]


def _action_id(snapshot_id: str, page_id: str) -> str:
    return sha256_text(f"{snapshot_id}|{page_id}|full-export")[:16]


def _page_sort_key(page: PageRecord) -> tuple:
    return (
        page.notebook_title.casefold(),
        tuple(part.casefold() for part in page.section_group_path),
        page.section_title.casefold(),
        page.page_order,
        page.source_page_id,
    )


def _load_model(source: SnapshotReader, page: PageRecord, normalizer: Normalizer):
    if page.semantic_model_path and page.normalizer_version == normalizer.version:
        try:
            return json.loads(source.read_relative(page.semantic_model_path))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    if not page.raw_content_path:
        return {"kind": "doc", "version": "1", "children": []}
    raw = source.read_relative(page.raw_content_path).decode("utf-8")
    resource_map = {r.source_reference: r.sha256 for r in page.resources if r.sha256}
    return normalizer.normalize(page.raw_content_format, raw, resource_map).semantic_model


@dataclass(frozen=True)
class _ActionSpec:
    action_id: str
    source_page_id: str
    parent_node_id: str
    title: str
    semantic: str
    resources: list[str]

    def canonical_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "source_page_id": self.source_page_id,
            "parent_node_id": self.parent_node_id,
            "title": self.title,
            "semantic": self.semantic,
            "resources": self.resources,
        }
