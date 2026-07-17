"""Normalization tests.

The central invariant: semantically equivalent HTML and Markdown must produce
the same canonical semantic model hash, while genuine content differences
must produce different hashes.
"""

from joplin_importer.exporting.content import build_export_html
from joplin_importer.models.enums import AuditRole, ContentFormat, SourceBackend
from joplin_importer.models.snapshot import PageRecord
from joplin_importer.normalization import Normalizer
from joplin_importer.normalization.model import semantic_hash, visible_text
from joplin_importer.normalization.textnorm import normalize_text
from tests.fake_onenote import page_xml

norm = Normalizer()


def md(text, resources=None):
    return norm.normalize(ContentFormat.MARKDOWN, text, resources)


def html(text, resources=None):
    return norm.normalize(ContentFormat.HTML, text, resources)


def onexml(text, resources=None):
    return norm.normalize(ContentFormat.ONENOTE_XML, text, resources)


# -- text normalization ---------------------------------------------------------


def test_normalize_text_nfc_and_whitespace():
    assert normalize_text("Café   x") == normalize_text("Café x")
    assert normalize_text("a b") == "a b"
    assert normalize_text("  a \r\n\r\n\r\n b ") == "a\nb"
    assert normalize_text("слово​слово") == "словослово"


# -- cross-format equivalence -----------------------------------------------------


def test_equivalent_html_and_markdown_hash_equal():
    a = md("# Title\n\nHello **world** and [link](https://example.com)\n")
    b = html(
        "<h1>Title</h1><p>Hello <b>world</b> and "
        '<a href="https://example.com">link</a></p>'
    )
    assert a.semantic_sha256 == b.semantic_sha256
    assert a.normalized_text == b.normalized_text


def test_different_whitespace_identical_content():
    a = md("Hello   world\n")
    b = md("Hello world")
    assert a.semantic_sha256 == b.semantic_sha256


def test_truncated_content_hashes_differ():
    full = md("Paragraph one.\n\nParagraph two.")
    truncated = md("Paragraph one.")
    assert full.semantic_sha256 != truncated.semantic_sha256


def test_table_equivalence_and_cell_order():
    a = md("| a | b |\n| --- | --- |\n| 1 | 2 |")
    b = html("<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert a.semantic_sha256 == b.semantic_sha256
    swapped = html(
        "<table><tr><th>b</th><th>a</th></tr><tr><td>2</td><td>1</td></tr></table>"
    )
    assert a.semantic_sha256 != swapped.semantic_sha256


def test_nested_html_table_rows_are_not_duplicated_in_parent():
    result = html(
        "<table><tr><td><table><tr><td>inner one</td></tr>"
        "<tr><td>inner two</td></tr></table></td></tr></table>"
    )
    assert result.normalized_text == "inner one inner two"


def test_checklist_state_preserved():
    done = md("- [x] task one")
    todo = md("- [ ] task one")
    assert done.semantic_sha256 != todo.semantic_sha256
    items = done.semantic_model["children"][0]["items"]
    assert items[0]["checked"] is True
    assert items[0]["children"][0]["text"] == "task one"


def test_checklist_html_checkbox_equivalence():
    a = md("- [x] task one")
    b = html(
        '<ul><li class="task-list-item">'
        '<input type="checkbox" checked disabled> task one</li></ul>'
    )
    assert a.semantic_sha256 == b.semantic_sha256


def test_nested_list_structure():
    a = md("- top\n  - nested\n")
    model = a.semantic_model
    top_items = model["children"][0]["items"]
    assert len(top_items) == 1
    kinds = [c["kind"] for c in top_items[0]["children"]]
    assert "list" in kinds


def test_code_block_preserved_separately_from_text():
    a = md("```python\nprint('hi')\n```")
    block = a.semantic_model["children"][0]
    assert block["kind"] == "code"
    assert block["language"] == "python"
    assert "print('hi')" in block["text"]
    b = md("print('hi')")
    assert a.semantic_sha256 != b.semantic_sha256  # code vs paragraph is meaningful


def test_links_preserved():
    a = md("[here](https://example.com/x)")
    para = a.semantic_model["children"][0]
    assert para["links"] == ["https://example.com/x"]
    assert para["link_spans"] == [
        {"start": 0, "end": 4, "href": "https://example.com/x"}
    ]


def test_export_renders_link_label_once_without_duplicate_url():
    result = html(
        '<p>Read <a href="https://example.com/target">the source</a> now.</p>'
    )
    body = build_export_html(
        PageRecord(
            source_backend=SourceBackend.ONENOTE_BACKUP,
            audit_role=AuditRole.CORROBORATING,
            source_page_id="page-1",
        ),
        result.semantic_model,
        plan_id="plan-1",
        action_id="action-1",
    )
    assert body == (
        '<p>Read <a href="https://example.com/target">the source</a> now.</p>'
    )
    assert body.count("https://example.com/target") == 1


def test_line_breaks_meaningful():
    a = md("line one  \nline two")  # hard break
    b = md("line one line two")
    assert a.semantic_sha256 != b.semantic_sha256


def test_images_by_content_hash_not_id():
    res_a = {":/" + "a" * 32: "hash-1"}
    res_b = {":/" + "b" * 32: "hash-1"}
    a = md(f"![img](:/{'a' * 32})", res_a)
    b = md(f"![img](:/{'b' * 32})", res_b)
    # same content hash but different Joplin IDs -> reference must not matter
    node_a = a.semantic_model["children"][0]
    node_b = b.semantic_model["children"][0]
    assert node_a["hash"] == node_b["hash"] == "hash-1"


def test_attachment_link_becomes_attachment_node():
    resources = {":/" + "c" * 32: "hash-2"}
    a = md(f"[report.pdf](:/{'c' * 32})", resources)
    node = a.semantic_model["children"][0]
    assert node["kind"] == "attachment"
    assert node["hash"] == "hash-2"
    assert node["name"] == "report.pdf"


def test_markdown_with_inline_html_mixed():
    a = norm.normalize(ContentFormat.MIXED, "Hello <b>bold</b> world")
    b = md("Hello **bold** world")
    assert a.semantic_sha256 == b.semantic_sha256


def test_html_block_inside_markdown():
    a = md("before\n\n<table><tr><td>x</td></tr></table>\n\nafter")
    kinds = [c["kind"] for c in a.semantic_model["children"]]
    assert kinds == ["paragraph", "table", "paragraph"]


def test_html_script_and_style_ignored():
    a = html("<p>text</p><script>alert(1)</script><style>p{}</style>")
    assert visible_text(a.semantic_model) == "text"


# -- OneNote XML ------------------------------------------------------------------


def test_onenote_xml_body_text_and_image():
    resources = {"image:1": "img-hash"}
    result = onexml(
        page_xml("{p}", "Title", body_text="Body text", with_image=True), resources
    )
    kinds = [c["kind"] for c in result.semantic_model["children"]]
    assert kinds == ["paragraph", "image"]
    assert result.semantic_model["children"][0]["text"] == "Body text"
    assert result.semantic_model["children"][1]["hash"] == "img-hash"
    # the page title must not be part of the body model
    assert "Title" not in result.normalized_text


def test_onenote_xml_matches_equivalent_markdown():
    a = onexml(page_xml("{p}", "T", body_text="Same content"))
    b = md("Same content")
    assert a.semantic_sha256 == b.semantic_sha256


def test_onenote_inline_html_in_t_runs():
    xml = page_xml("{p}", "T", body_text="plain")
    xml = xml.replace(
        "<![CDATA[plain]]>",
        "<![CDATA[<span style='font-weight:bold'>bold</span> and "
        "<a href=\"https://example.com\">link</a>]]>",
    )
    result = onexml(xml)
    para = result.semantic_model["children"][0]
    assert para["text"] == "bold and link"
    assert para["links"] == ["https://example.com"]


def test_onenote_ink_becomes_placeholder():
    result = onexml(page_xml("{p}", "T", body_text="x", with_ink=True))
    kinds = [c["kind"] for c in result.semantic_model["children"]]
    assert "placeholder" in kinds


def test_onenote_attachment_node():
    resources = {"file:1": "file-hash"}
    result = onexml(
        page_xml("{p}", "T", with_file_path="C:\\cache\\report.pdf"), resources
    )
    nodes = [c for c in result.semantic_model["children"] if c["kind"] == "attachment"]
    assert len(nodes) == 1
    assert nodes[0]["hash"] == "file-hash"
    assert nodes[0]["name"] == "report.pdf"


def test_empty_page_vs_image_only_page():
    empty = onexml(page_xml("{p}", "T"))
    image_only = onexml(page_xml("{p}", "T", with_image=True), {"image:1": "h"})
    assert empty.normalized_text == ""
    assert image_only.normalized_text == ""
    # semantic models still differ: the image is content
    assert empty.semantic_sha256 != image_only.semantic_sha256


def test_unicode_titles_and_text():
    a = md("Привет **мир** ☕")
    assert "Привет мир ☕" == a.normalized_text


def test_semantic_hash_stable_across_runs():
    a = md("# T\n\ncontent")
    b = md("# T\n\ncontent")
    assert a.semantic_sha256 == b.semantic_sha256
    assert semantic_hash(a.semantic_model) == a.semantic_sha256
