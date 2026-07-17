"""OneNote desktop COM API access.

Windows-only. The COM calls are isolated behind :class:`OneNoteApi` so the
hierarchy/page parsing and the scanner are testable on any OS with fakes;
only :class:`ComOneNoteApi` touches ``win32com``.

Read-only guarantee: this module only ever calls ``GetHierarchy`` and
``GetPageContent``. No update/create/delete COM method is wrapped at all.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol

ONENOTE_2013_NAMESPACE = "http://schemas.microsoft.com/office/onenote/2013/onenote"

# HierarchyScope
HS_PAGES = 4

# PageInfo: what GetPageContent embeds in the XML
PI_BASIC = 0
PI_BINARY_DATA = 1
PI_ALL = 7

# Office keeps compatibility ProgIDs for older OneNote automation clients.
# Some Click-to-Run installations have an incomplete newest type-library
# registration while the compatible 12.0 interface remains fully usable.
ONENOTE_PROGIDS = (
    "OneNote.Application",
    "OneNote.Application.15",
    "OneNote.Application.14",
    "OneNote.Application.12",
)


class OneNoteApiError(RuntimeError):
    pass


class OneNoteProcessUnavailableError(OneNoteApiError):
    """The native OneNote process died or its COM RPC server disconnected."""


class OneNoteApi(Protocol):
    """Minimal read-only surface of the OneNote COM application object."""

    def get_hierarchy(self) -> str:
        """Full hierarchy XML down to pages."""
        ...

    def get_page_content(self, page_id: str, *, include_binary: bool = True) -> str:
        """Page XML, optionally with embedded base64 binary data."""
        ...


class ComOneNoteApi:
    """Real COM implementation (requires Windows + OneNote desktop + pywin32)."""

    _app: object

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OneNoteApiError(
                "the OneNote COM adapter requires Windows with the OneNote "
                "desktop application installed; use it on the Windows test machine"
            )
        try:
            import pythoncom  # noqa: F401
            import win32com.client
        except ImportError as exc:  # pragma: no cover - Windows-only
            raise OneNoteApiError(
                "pywin32 is required for the COM adapter: pip install pywin32"
            ) from exc
        self._app = _connect_onenote(win32com.client)

    def get_hierarchy(self) -> str:  # pragma: no cover - Windows-only
        return self._call_with_out_param("GetHierarchy", "", HS_PAGES)

    def get_page_content(
        self, page_id: str, *, include_binary: bool = True
    ) -> str:  # pragma: no cover - Windows-only
        page_infos = [PI_ALL, PI_BINARY_DATA, PI_BASIC] if include_binary else [PI_BASIC]
        last_error: Exception | None = None
        for page_info in page_infos:
            try:
                return self._call_with_out_param("GetPageContent", page_id, "", page_info)
            except Exception as exc:  # COM errors vary by OneNote build
                last_error = exc
                if _is_rpc_process_failure(exc):
                    raise OneNoteProcessUnavailableError(
                        f"GetPageContent lost the OneNote COM process for {page_id}: {exc}"
                    ) from exc
        raise OneNoteApiError(f"GetPageContent failed for {page_id}: {last_error}")

    def _call_with_out_param(self, method: str, *args):  # pragma: no cover - Windows-only
        """Call a COM method whose XML result is an out parameter.

        pywin32 returns out params as the call result (early binding) or needs
        them passed as placeholders (late binding); handle both.
        """
        result = getattr(self._app, method)(*args)
        if isinstance(result, tuple):
            for item in result:
                if isinstance(item, str) and item.lstrip().startswith("<"):
                    return item
            raise OneNoteApiError(f"{method} returned no XML: {result!r}")
        if isinstance(result, str):
            return result
        raise OneNoteApiError(f"{method} returned unexpected type {type(result).__name__}")


def _connect_onenote(win32_client: Any) -> object:  # pragma: no cover - Windows-only
    """Return a usable OneNote automation object across Office registrations."""
    failures: list[str] = []
    for progid in ONENOTE_PROGIDS:
        for factory_name, factory in (
            ("early binding", win32_client.gencache.EnsureDispatch),
            ("late binding", win32_client.Dispatch),
        ):
            try:
                app = factory(progid)
                if not callable(getattr(app, "GetHierarchy", None)) or not callable(
                    getattr(app, "GetPageContent", None)
                ):
                    raise AttributeError("required read-only methods are unavailable")
                return app
            except Exception as exc:  # COM errors vary by Office build
                failures.append(f"{progid} ({factory_name}): {type(exc).__name__}")
    detail = "; ".join(failures)
    raise OneNoteApiError(
        "could not initialize the OneNote desktop COM read-only interface; "
        f"tried compatibility ProgIDs ({detail})"
    )


_RPC_PROCESS_FAILURE_HRESULTS = {
    -2147023170,  # 0x800706BE: RPC call failed (native process commonly crashed)
    -2147023174,  # 0x800706BA: RPC server unavailable
}


def _is_rpc_process_failure(exc: Exception) -> bool:
    """Recognize pywintypes COM errors that make further calls meaningless."""
    hresult = getattr(exc, "hresult", None)
    if hresult is None and exc.args and isinstance(exc.args[0], int):
        hresult = exc.args[0]
    return hresult in _RPC_PROCESS_FAILURE_HRESULTS
