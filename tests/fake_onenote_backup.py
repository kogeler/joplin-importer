"""Small Aspose-compatible DOM fakes for local backup scanner tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace


class _Container:
    def __init__(self, *children):
        self.children = list(children)

    def __iter__(self):
        return iter(self.children)


class TextRun:
    def __init__(self, text: str, hyperlink: str | None = None):
        self.Text = text
        self.Style = SimpleNamespace(HyperlinkAddress=hyperlink)


class RichText:
    def __init__(self, text: str = "", *, hyperlink: str | None = None):
        self.Text = text
        self.TextRuns = [TextRun(text, hyperlink)] if text else []


class Title(_Container):
    def __init__(self, text: str):
        self.TitleText = RichText(text)
        super().__init__(self.TitleText)


class Outline(_Container):
    pass


class OutlineElement(_Container):
    def __init__(self, *children, number_list=None):
        self.NumberList = number_list
        super().__init__(*children)


class Image(_Container):
    def __init__(self, data=b"image", filename="picture.png", alt="diagram"):
        self.Bytes = data
        self.FileName = filename
        self.Format = "png"
        self.AlternativeTextTitle = alt
        self.AlternativeTextDescription = None
        super().__init__()


class AttachedFile:
    def __init__(self, data=b"attachment", filename="document.bin"):
        self.Bytes = data
        self.FileName = filename


class TableCell(_Container):
    pass


class TableRow(_Container):
    pass


class Table(_Container):
    pass


class Page(_Container):
    def __init__(self, title: str, *children, level: int = 1):
        self.Title = Title(title)
        self.Level = level
        self.CreationTime = datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)
        self.LastModifiedTime = datetime(2021, 2, 3, 4, 5, 6, tzinfo=UTC)
        super().__init__(self.Title, *children)


class Document(_Container):
    pass
