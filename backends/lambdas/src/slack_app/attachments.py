"""The files attached to a Slack message, as metadata only.

The Lambdas never download files. They pass the agent a reference to each one (its Slack
file ID plus the name, type and size people see), and the agent downloads what it
needs itself (see attachments.py in the agent). That keeps file bytes out of the 3-second
events handler, the 256 KB SQS message and the triage call.
"""

from dataclasses import dataclass

# Enough for "compare these" without letting one message fill the job. Slack allows 10.
MAX_FILES_PER_MESSAGE = 10

# Slack keeps a placeholder for deleted files ("tombstone") and for files older than a
# free workspace's history limit ("hidden_by_limit"). Neither can be downloaded.
_UNAVAILABLE_MODES = {"tombstone", "hidden_by_limit"}


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    mimetype: str = ""
    size: int = 0

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "mimetype": self.mimetype, "size": self.size}

    @property
    def label(self) -> str:
        """What a person sees in Slack, e.g. "q3-report.pdf (1.2 MB)"."""
        return f"{self.name} ({human_size(self.size)})" if self.size else self.name


def from_files(files: object) -> tuple[Attachment, ...]:
    """Slack's `files` array (from an event, conversations.replies or a queued job) -> Attachments."""
    if not isinstance(files, list):
        return ()
    attachments = []
    for file in files:
        if not isinstance(file, dict) or not file.get("id") or file.get("mode") in _UNAVAILABLE_MODES:
            continue
        attachments.append(
            Attachment(
                id=str(file["id"]),
                name=str(file.get("name") or file.get("title") or "file"),
                mimetype=str(file.get("mimetype") or ""),
                size=_int(file.get("size")),
            )
        )
    return tuple(attachments[:MAX_FILES_PER_MESSAGE])


def describe(attachments: tuple[Attachment, ...]) -> str:
    """The line people's files get in a transcript, e.g. "[attached: q3.csv (12 KB)]", or "" for none."""
    if not attachments:
        return ""
    return "[attached: " + ", ".join(attachment.label for attachment in attachments) + "]"


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _int(value: object) -> int:
    try:
        return max(int(value), 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
