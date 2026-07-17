# Known OneNote Desktop COM Crash

## Status

The Windows OneNote Desktop COM backend is not a reliable complete source for
the affected notebook. During the real recovery, OneNote repeatedly terminated
or disconnected its COM server while `joplin-importer` called `GetPageContent` for certain
pages. The hierarchy call could still expose a page ID, title, and location,
but no page XML was returned for the crashing content call.

This is treated as a native OneNote Desktop/COM defect or damaged internal
OneNote state, not as an ordinary `joplin-importer` XML parsing error. The exact Microsoft
root cause has not been proven: it may be a malformed page object, damaged
section/cache state, or a bug in OneNote's native content serializer.

The following attempted repairs did not make COM extraction reliable:

* closing and reopening OneNote;
* removing local copies, allowing OneNote to download them again, and waiting
  for cloud synchronization to finish;
* Microsoft Office Quick Repair;
* Microsoft Office Online Repair.

Because `ONENOTE.EXE` fails before returning XML, `joplin-importer` has no response bytes
to patch on the fly. Modifying OneNote's live cache or section files behind the
desktop application's back is unsafe, unsupported, and outside this tool's
read-only source contract.

## Why quarantine did not solve this incident

Quarantine worked mechanically for each explicitly listed page ID: the scanner
skipped that ID before calling `GetPageContent`. It did not repair or recover
the page, and it could not predict which unlisted page would crash next.

The feature is useful only when the affected set is small, known, and stable.
In this incident, skipping one known page exposed additional crashing pages.
After a native crash the COM connection was no longer trustworthy, so the
scanner correctly stopped instead of recording every remaining page as
missing. An operator safety cap of 20 quarantine entries was set to prevent an
unbounded skip-and-retry loop; quarantine was not accepted as a route to a
complete scan.

Every quarantine entry also removes that page's content from the snapshot by
definition. Therefore even a run that reaches the end has `partial` coverage
and cannot be used for deterministic full export. Quarantine is failure
isolation and diagnostic evidence, not a corruption workaround.

## Operational decision

For this recovery:

* COM remains optional for current-state analysis of pages OneNote can read;
* COM/quarantine output must not be described as a complete backup;
* the complete newest local OneNote backup is the supported export source;
* the backup files are read without starting OneNote and are never modified;
* Microsoft Graph is not a fallback recovery source yet because its real cloud
  mode remains incomplete and unvalidated ([TODO.md](../TODO.md)).

Keep real page IDs, titles, crash lists, dumps, and snapshots only in ignored
local artifacts. Do not copy them into issues, documentation, fixtures, or Git.

## Recognizing the failure

Classify the incident as this known native failure when the evidence shows:

1. `GetHierarchy` returned the page metadata;
2. the native process terminated or COM became unavailable during
   `GetPageContent`;
3. no OneNote XML payload reached the normalizer;
4. restarting the desktop application merely allowed scanning to continue
   until another affected page;
5. the manifest reports partial coverage and unattempted pages rather than
   treating them as absent.

If `GetPageContent` returns XML successfully and normalization fails afterward,
that is a different extractor/parser bug and should not be hidden by
quarantine.
