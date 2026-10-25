# Meeting follow-up

Toolkit can turn one completed recording, or several parts of the same meeting,
into source-linked notes, recorded decisions, action items, and complete email
drafts. Open **Meeting follow-up…** from the tray, or **Meetings** in Work,
select the completed recordings and choose **Prepare notes & follow-ups**.
Selected segments are combined in recording order.

The work continues in the background. Its status appears with the saved meeting
selection and in Work. Closing the view leaves the task and its results intact.
If the task needs input or cannot finish, Work shows the question or the reason
and provides the existing resume controls.

Each result includes:

- Notes and decisions with quotations linking back to the selected recordings.
- Actions with an owner and due wording when the transcript states them, and
  explicit **Not stated** fields otherwise.
- Complete follow-up drafts, with recipients only where the source identifies
  them. Unknown email addresses stay empty.
- A package containing the generated files and exact source snapshots.

Multiple segments keep their separate identities and capture notices. A
microphone interruption remains a gap; Toolkit does not reconstruct missing
audio. For an interview, the result records the discussion and administrative
next steps without scoring candidates or making a hiring decision.

Use the action and draft editors to review the result. A save is confirmed only
after its exact edit has been stored. Concurrent changes produce a conflict and
retain the entered text. Generated files and later edits have separate saved
versions, so a retry cannot replace reviewed content. Where several generated
packages exist, select the intended version explicitly.

During preparation, Toolkit saves unsent drafts to an already running classic
Outlook session when its draft store can be verified. Otherwise, it saves full
`.eml` files and reports that destination. It does not start Outlook or send
messages. Retrying a saved draft checks the same destination and content;
changes made in Outlook or a missing draft are preserved for inspection.
Local edits made after preparation remain local and do not silently update an
Outlook draft. Open the selected local draft explicitly for further mail review.

Saved work lives under `%LOCALAPPDATA%\toolkit\work\meeting-followups`:

| Location | Contents |
| --- | --- |
| `requests.sqlite3` | The ordered selection, exact transcript bytes, and original work request |
| `packages` | Notes, action CSV, draft files, source snapshots, and edit history |
| `mail` | Verified mail destination records and any fallback email files |

The implementation uses Toolkit's existing durable work controller. Preparation
is stored before submission; every publication and mail save passes through
the action journal. Model output is validated against the selected transcript
bytes before publication. A model reply alone cannot mark the workflow complete.
The meeting worker's tools are limited to those sources, artifacts, draft
saving, and durable questions/recovery.

Behavioral checks use real SQLite databases, files, local HTTP model fixtures,
and explicit mail transport dependencies. The validation record distinguishes
these checks, targeted mutation results, the real meeting run, and native
Outlook evidence.
