"""Slack Block Kit cards for the public service-status tool (see status_mcp.py).

The tool reads a status MCP server (StatusPulse) and the model writes one or two short
sentences; this module turns the structured result into the cards that follow them:

    <the model's short answer>
    🚦 Service status
    1 of 3 services need attention
    🟠 GitHub — Major outage
    Git Operations degraded
    ⚠️ Delayed webhook delivery · investigating
    🟢 Cloudflare — Operational
    ...
    Live from each provider's public status page · updated 10:42

Everything in a card that comes from a status page is third-party text. It is escaped for
Slack (so `<!channel>` or a spoofed link can't fire), whitespace-collapsed and truncated,
and an unrecognised indicator never reaches the output as-is.

Nothing here touches the network or Slack, so it is cheap to test.
"""

import time
from dataclasses import dataclass, field

MAX_SERVICES = 10
MAX_INCIDENTS = 3
MAX_TEXT_CHARS = 300
MAX_SECTION_CHARS = 3000  # Slack's limit for the text of one section block

# indicator -> (emoji, label, severity). Statuspage's own indicator names.
_INDICATORS = {
    "none": ("\U0001f7e2", "Operational", 0),
    "minor": ("\U0001f7e1", "Minor issues", 1),
    "major": ("\U0001f7e0", "Major outage", 2),
    "critical": ("\U0001f534", "Critical outage", 3),
}
_UNKNOWN = ("⚪", "Status unknown", 1)  # shown with the problems, not buried among the greens


@dataclass(frozen=True)
class Incident:
    name: str
    status: str


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    indicator: str  # one of _INDICATORS, or "unknown"
    description: str
    incidents: tuple[Incident, ...] = ()

    @property
    def emoji(self) -> str:
        return _INDICATORS.get(self.indicator, _UNKNOWN)[0]

    @property
    def label(self) -> str:
        return _INDICATORS.get(self.indicator, _UNKNOWN)[1]

    @property
    def severity(self) -> int:
        return _INDICATORS.get(self.indicator, _UNKNOWN)[2]


def escape(text: str) -> str:
    """Slack's three required escapes: they stop `<!channel>`, `<@U123>` and `<url|label>` from firing."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    """Single-line, length-capped, unescaped text from an untrusted value."""
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_services(structured: object) -> list[Service]:
    """The `services` list of a StatusPulse result, or [] when it isn't what we expect.

    Malformed entries are dropped one by one rather than failing the whole answer.
    """
    items = structured.get("services") if isinstance(structured, dict) else None
    if not isinstance(items, list):
        return []

    services = []
    for item in items[:MAX_SERVICES]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        indicator = clean(item.get("indicator"), 20).lower()
        incidents = tuple(
            Incident(name=clean(i.get("name"), 120), status=clean(i.get("status"), 40))
            for i in (item.get("incidents") or [])[:MAX_INCIDENTS]
            if isinstance(i, dict) and i.get("name")
        )
        services.append(
            Service(
                id=clean(item.get("id") or item["name"], 40).lower(),
                name=clean(item["name"], 60),
                indicator=indicator if indicator in _INDICATORS else "unknown",
                description=clean(item.get("description")),
                incidents=incidents,
            )
        )
    return services


def summary_text(services: list[Service]) -> str:
    """Plain lines for the model (and Slack's notification text): one per service."""
    lines = []
    for service in services:
        line = f"- {service.name}: {service.label.lower()}"
        if service.description:
            line += f" ({service.description})"
        for incident in service.incidents:
            line += f"; incident: {incident.name} [{incident.status}]"
        lines.append(line)
    return "\n".join(lines)


def build_blocks(services: list[Service], message: str = "", now: float | None = None) -> list[dict]:
    """Block Kit for these services, worst first, under the model's own short answer."""
    ordered = sorted(services, key=lambda s: -s.severity)[:MAX_SERVICES]
    attention = sum(1 for s in ordered if s.severity)
    when = int(now if now is not None else time.time())

    blocks: list[dict] = []
    if message.strip():
        blocks.append(_section(message.strip()[:MAX_SECTION_CHARS]))
    title = {"type": "plain_text", "text": "\U0001f6a6 Service status", "emoji": True}
    blocks.append({"type": "header", "text": title})
    blocks.append(_context(_headline(len(ordered), attention)))

    for service in ordered:
        text = f"{service.emoji} *{escape(service.name)}* — {service.label}"
        if service.description:
            text += f"\n{escape(service.description)}"
        blocks.append(_section(text))
        if service.incidents:
            blocks.append({"type": "context", "elements": [_incident(i) for i in service.incidents]})

    blocks.append(
        _context(f"Live from each provider's public status page · updated <!date^{when}^{{time}}|just now>")
    )
    return blocks


def _incident(incident: Incident) -> dict:
    text = f"⚠️ {escape(incident.name)}"
    if incident.status:
        text += f" · _{escape(incident.status)}_"
    return {"type": "mrkdwn", "text": text}


def _headline(total: int, attention: int) -> str:
    noun = "service" if total == 1 else "services"
    if not attention:
        return f"All {total} {noun} operational" if total > 1 else "Operational"
    return f"{attention} of {total} {noun} need attention"


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


@dataclass
class StatusCards:
    """Collects what the status tool found during one request, for main.py to attach to the reply.

    Same idea as AuthState: the tool writes to it while the model is running, and the
    entrypoint reads it afterwards. If the tool runs twice (GitHub, then Cloudflare), the
    cards show both; a later result for the same service replaces the earlier one.
    """

    services: list[Service] = field(default_factory=list)

    def show(self, services: list[Service]) -> None:
        by_id = {s.id: s for s in self.services}
        by_id.update({s.id: s for s in services})
        self.services = list(by_id.values())

    def blocks(self, message: str = "") -> list[dict] | None:
        return build_blocks(self.services, message) if self.services else None
