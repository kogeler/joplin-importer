"""Reports and repair-plan tests."""

import csv
import json

import pytest

from joplin_importer.matching.audit import run_audit
from joplin_importer.repair.planner import (
    build_approval,
    build_repair_plan,
    load_approval,
    load_plan,
    verify_approval,
    write_plan,
)
from joplin_importer.reporting.csv_reports import write_csv_reports
from joplin_importer.reporting.html_report import write_html_report, write_json_report
from tests.integration.test_audit_flow import snapshots  # fixture reuse  # noqa: F401


@pytest.fixture
def audit(snapshots):  # noqa: F811
    source, target = snapshots
    return source, target, run_audit(source, target, tool_version="0.1.0-test")


def test_csv_reports_written(audit, tmp_path):
    _source, _target, result = audit
    files = write_csv_reports(result, tmp_path / "reports")
    names = {f.name for f in files}
    assert names == {
        "matches.csv",
        "missing-pages.csv",
        "empty-or-truncated.csv",
        "missing-resources.csv",
        "ambiguous.csv",
        "format-differences.csv",
        "source-drift.csv",
    }
    with (tmp_path / "reports" / "missing-pages.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert any(r["source_page_id"] == "{page-old-1}" for r in rows)


def test_html_report_offline_and_escaped(audit, tmp_path):
    _source, _target, result = audit
    result.findings[0].title = "<script>alert(1)</script>"
    path = write_html_report(result, tmp_path / "reports")
    text = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text  # escaped
    assert "&lt;script&gt;" in text
    assert "http://" not in text.replace("http://joplin", "")  # no external refs
    assert "https://" not in text
    assert "src=" not in text.split("<style>")[0]


def test_json_report(audit, tmp_path):
    _source, _target, result = audit
    path = write_json_report(result, tmp_path / "reports")
    payload = json.loads(path.read_text())
    assert payload["summary"]["source_pages"] == 4


def test_repair_plan_create_only(audit, tmp_path):
    source, _target, result = audit
    plan, bodies = build_repair_plan(
        result,
        source,
        tool_version="0.1.0-test",
        target_instance_fingerprint="fp-test",
        created_at_utc="2026-07-17T00:00:00Z",
    )
    assert plan.mode == "create-only"
    assert plan.actions, "missing pages must produce recovery actions"
    action_pages = {a.source_page_id for a in plan.actions}
    assert "{page-old-1}" in action_pages
    for action in plan.actions:
        assert str(action.action) == "create-recovery-copy"
        assert action.destination_notebook == "_OneNote Recovery"
        assert action.expected_body_sha256
        assert action.idempotency_key.startswith("joplin-importer:")
        assert bodies[action.action_id]


def test_repair_plan_is_deterministic(audit):
    source, _target, result = audit
    kwargs = dict(
        tool_version="0.1.0-test",
        target_instance_fingerprint="fp-test",
        created_at_utc="2026-07-17T00:00:00Z",
    )
    plan_a, bodies_a = build_repair_plan(result, source, **kwargs)
    plan_b, bodies_b = build_repair_plan(result, source, **kwargs)
    assert plan_a.model_dump() == plan_b.model_dump()
    assert bodies_a == bodies_b


def test_recovery_body_contains_metadata_and_marker(audit):
    source, _target, result = audit
    plan, bodies = build_repair_plan(
        result,
        source,
        tool_version="0.1.0-test",
        target_instance_fingerprint="fp-test",
        created_at_utc="2026-07-17T00:00:00Z",
    )
    action = next(a for a in plan.actions if a.source_page_id == "{page-old-1}")
    body = bodies[action.action_id]
    assert "joplin-importer:onenote_page_id={page-old-1}" in body
    assert "Original location:" in body
    assert "Archived" in body  # actual content preserved
    assert "Unsupported OneNote object" in body or "ink" in body  # ink warning visible


def test_approval_roundtrip_and_digest_check(audit, tmp_path):
    source, _target, result = audit
    plan, bodies = build_repair_plan(
        result,
        source,
        tool_version="0.1.0-test",
        target_instance_fingerprint="fp-test",
    )
    plan_path = tmp_path / "repair-plan.json"
    write_plan(plan, bodies, plan_path)

    approval = build_approval(plan_path, operator="tester")
    approval_path = tmp_path / "approvals.json"
    approval_path.write_text(approval.model_dump_json(indent=2))

    loaded, _digest = load_approval(approval_path)
    verify_approval(plan_path, loaded)  # must not raise

    # tamper with the plan -> digest check must fail
    plan_path.write_text(plan_path.read_text().replace("create-only", "create-only "))
    with pytest.raises(ValueError, match="different repair plan"):
        verify_approval(plan_path, loaded)


def test_approval_rejects_unknown_action_ids(audit, tmp_path):
    source, _target, result = audit
    plan, bodies = build_repair_plan(
        result,
        source,
        tool_version="0.1.0-test",
        target_instance_fingerprint="fp-test",
    )
    plan_path = tmp_path / "repair-plan.json"
    write_plan(plan, bodies, plan_path)
    with pytest.raises(ValueError, match="unknown action ids"):
        build_approval(plan_path, action_ids=["nonexistent"])


def test_plan_file_roundtrip(audit, tmp_path):
    source, _target, result = audit
    plan, bodies = build_repair_plan(
        result,
        source,
        tool_version="0.1.0-test",
        target_instance_fingerprint="fp-test",
    )
    plan_path = tmp_path / "repair-plan.json"
    write_plan(plan, bodies, plan_path)
    loaded, digest = load_plan(plan_path)
    assert loaded.plan_id == plan.plan_id
    assert len(digest) == 64
