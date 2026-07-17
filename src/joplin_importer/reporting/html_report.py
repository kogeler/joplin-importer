"""Offline HTML audit report.

Self-contained file: no external JavaScript or CSS, inline vanilla JS only
for filtering. All user content is HTML-escaped; previews are capped. Full
note bodies are excluded unless explicitly requested.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from ..matching.results import AuditResult

_PREVIEW_CAP = 400

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OneNote → Joplin audit report</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 1.5rem; color: #1a1a1a; }}
h1 {{ font-size: 1.4rem; }}
h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }}
th {{ background: #f0f0f0; position: sticky; top: 0; }}
.summary-grid {{ display: flex; gap: 2rem; flex-wrap: wrap; }}
.summary-card {{ border: 1px solid #ddd; border-radius: 6px; padding: 0.8rem 1.2rem; }}
.summary-card h3 {{ margin: 0 0 0.4rem; font-size: 0.9rem; }}
.filters {{ margin: 1rem 0; display: flex; gap: 1rem; flex-wrap: wrap; }}
.filters label {{ font-size: 0.85rem; }}
.badge {{ padding: 1px 6px; border-radius: 8px; font-size: 0.75rem; white-space: nowrap; }}
.b-exact,.b-confirmed {{ background: #d4edda; }}
.b-high-confidence,.b-probable {{ background: #fff3cd; }}
.b-ambiguous,.b-uncertain {{ background: #ffe5d0; }}
.b-unmatched {{ background: #f8d7da; }}
.b-informational {{ background: #e2e3e5; }}
.preview {{ font-family: monospace; white-space: pre-wrap; max-width: 40rem;
            max-height: 8rem; overflow: auto; color: #444; }}
.meta {{ color: #666; font-size: 0.8rem; }}
</style>
</head>
<body>
<h1>OneNote → Joplin audit report</h1>
<p class="meta">Tool {tool_version} · thresholds {threshold_version} ·
source snapshot {source_snapshot} · target snapshot {target_snapshot}</p>

<div class="summary-grid">
<div class="summary-card"><h3>Inventory</h3>
{inventory_rows}</div>
<div class="summary-card"><h3>Matches by confidence</h3>
{match_rows}</div>
<div class="summary-card"><h3>Findings by evidence</h3>
{evidence_rows}</div>
<div class="summary-card"><h3>Findings by cause</h3>
{cause_rows}</div>
</div>

<h2>Findings</h2>
<div class="filters">
  <label>Kind <select id="f-kind" onchange="applyFilters()">{kind_options}</select></label>
  <label>Evidence <select id="f-evidence"
    onchange="applyFilters()">{evidence_options}</select></label>
  <label>Cause <select id="f-cause" onchange="applyFilters()">{cause_options}</select></label>
  <label>Notebook/section <input id="f-path" oninput="applyFilters()"
    placeholder="filter path"></label>
</div>
<table id="findings">
<thead><tr><th>Kind</th><th>Evidence</th><th>Cause</th><th>Title</th>
<th>Path</th><th>Explanation</th><th>IDs</th></tr></thead>
<tbody>
{finding_rows}
</tbody>
</table>

<h2>Matches</h2>
<div class="filters">
  <label>Confidence <select id="m-confidence"
    onchange="applyMatchFilters()">{confidence_options}</select></label>
  <label>Path <input id="m-path" oninput="applyMatchFilters()" placeholder="filter path"></label>
</div>
<table id="matches">
<thead><tr><th>Confidence</th><th>Stage</th><th>Score</th><th>Source (title / path)</th>
<th>Target (title / path)</th><th>Explanation</th></tr></thead>
<tbody>
{match_table_rows}
</tbody>
</table>

<script>
function applyFilters() {{
  var kind = document.getElementById('f-kind').value;
  var evidence = document.getElementById('f-evidence').value;
  var cause = document.getElementById('f-cause').value;
  var path = document.getElementById('f-path').value.toLowerCase();
  document.querySelectorAll('#findings tbody tr').forEach(function(row) {{
    var show = (!kind || row.dataset.kind === kind)
      && (!evidence || row.dataset.evidence === evidence)
      && (!cause || row.dataset.cause === cause)
      && (!path || row.dataset.path.indexOf(path) !== -1);
    row.style.display = show ? '' : 'none';
  }});
}}
function applyMatchFilters() {{
  var confidence = document.getElementById('m-confidence').value;
  var path = document.getElementById('m-path').value.toLowerCase();
  document.querySelectorAll('#matches tbody tr').forEach(function(row) {{
    var show = (!confidence || row.dataset.confidence === confidence)
      && (!path || row.dataset.path.indexOf(path) !== -1);
    row.style.display = show ? '' : 'none';
  }});
}}
</script>
</body>
</html>
"""


def write_html_report(result: AuditResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "summary.html"
    summary = result.summary

    def esc(value) -> str:
        return html.escape(str(value), quote=True)

    def count_rows(counts: dict[str, int]) -> str:
        if not counts:
            return "<p class='meta'>none</p>"
        return (
            "<table>"
            + "".join(
                f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in sorted(counts.items())
            )
            + "</table>"
        )

    def options(values: set[str]) -> str:
        opts = "<option value=''>all</option>"
        for value in sorted(values):
            opts += f"<option>{esc(value)}</option>"
        return opts

    finding_rows = []
    for finding in result.findings:
        path_str = " / ".join(finding.path)
        ids = " ".join(
            x for x in [finding.source_page_id or "", finding.joplin_note_id or ""] if x
        )
        explanation = finding.explanation[:_PREVIEW_CAP]
        details = json.dumps(finding.details, ensure_ascii=False) if finding.details else ""
        finding_rows.append(
            f"<tr data-kind='{esc(finding.kind)}' data-evidence='{esc(finding.evidence)}'"
            f" data-cause='{esc(finding.cause)}' data-path='{esc(path_str.lower())}'>"
            f"<td>{esc(finding.kind)}</td>"
            f"<td><span class='badge b-{esc(finding.evidence)}'>{esc(finding.evidence)}</span></td>"
            f"<td>{esc(finding.cause)}</td>"
            f"<td>{esc(finding.title)}</td>"
            f"<td>{esc(path_str)}</td>"
            f"<td>{esc(explanation)}"
            + (f"<div class='preview'>{esc(details[:_PREVIEW_CAP])}</div>" if details else "")
            + "</td>"
            f"<td class='meta'>{esc(ids)}</td></tr>"
        )

    match_rows = []
    for match in result.matches:
        src = f"{match.source_title} — {' / '.join(match.source_path)}"
        dst = f"{match.target_title} — {' / '.join(match.target_path)}"
        combined_path = (
            " / ".join(match.source_path) + " " + " / ".join(match.target_path)
        ).lower()
        score = f"{match.score:.3f}" if match.score is not None else ""
        match_rows.append(
            f"<tr data-confidence='{esc(match.confidence)}' data-path='{esc(combined_path)}'>"
            f"<td><span class='badge b-{esc(match.confidence)}'>{esc(match.confidence)}</span></td>"
            f"<td>{esc(match.stage)}</td><td>{score}</td>"
            f"<td>{esc(src)}</td><td>{esc(dst)}</td>"
            f"<td class='preview'>{esc(' | '.join(match.explanation)[:_PREVIEW_CAP])}</td></tr>"
        )

    html_text = _TEMPLATE.format(
        tool_version=esc(summary.tool_version),
        threshold_version=esc(summary.threshold_version),
        source_snapshot=esc(summary.source_snapshot_id),
        target_snapshot=esc(summary.target_snapshot_id),
        inventory_rows=count_rows(
            {"source pages": summary.source_pages, "target notes": summary.target_notes}
        ),
        match_rows=count_rows(summary.matches_by_confidence),
        evidence_rows=count_rows(summary.findings_by_evidence),
        cause_rows=count_rows(summary.findings_by_cause),
        kind_options=options({str(f.kind) for f in result.findings}),
        evidence_options=options({str(f.evidence) for f in result.findings}),
        cause_options=options({str(f.cause) for f in result.findings}),
        confidence_options=options({str(m.confidence) for m in result.matches}),
        finding_rows="\n".join(finding_rows),
        match_table_rows="\n".join(match_rows),
    )
    path.write_text(html_text, encoding="utf-8")
    return path


def write_json_report(result: AuditResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "summary.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path
