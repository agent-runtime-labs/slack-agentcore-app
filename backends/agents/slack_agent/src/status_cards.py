"""Slack Block Kit cards for the public service-status tool (see status_mcp.py).

The tool reads a status MCP server (StatusPulse) and the model writes one or two short
sentences; this module turns the structured result into the message around them:

    <the model's short answer>
    ▌🚦 Service status · 2 of 10 need attention                  (bar: red/orange/amber/green)
    ▌🟡 Cloudflare — Minor issues · Status page
    ▌Minor Service Outage
    ▌⚠️ Workers Build failing to start · investigating
    ▌🟡 Twilio — Minor issues · Status page
    ▌🟢 11 operational · GitHub · Discord · OpenAI · Claude · npm …   (healthy services: one line)
    ▌Live from each provider's public status page · updated 10:42

Only services with a problem get a full card, worst first; healthy ones are one wrapped line
of names, and when everything is healthy the whole thing is two lines. The cards sit in a Slack
attachment, which is what gives the message its colour bar.

Everything that comes from a status page is third-party text. It is escaped for Slack (so
`<!channel>` or a spoofed link can't fire), whitespace-collapsed and truncated, a status
page link is only made for an https URL without `<`, `>` or `|`, and an unrecognised indicator never reaches the
output as-is.

Nothing here touches the network or Slack, so it is cheap to test.
"""

import re
import time
from dataclasses import dataclass, field

MAX_SERVICES = 30  # how many a result may hold; extra entries are ignored
MAX_PROBLEMS = 10  # full cards shown (worst first)
MAX_OPERATIONAL = 20  # names shown in the healthy line
MAX_INCIDENTS = 3
MAX_TEXT_CHARS = 300
MAX_SECTION_CHARS = 3000  # Slack's limit for the text of one section block
MAX_URL_CHARS = 2000

# indicator -> (emoji, label, severity). Statuspage's own indicator names.
_INDICATORS = {
    "none": ("\U0001f7e2", "Operational", 0),
    "minor": ("\U0001f7e1", "Minor issues", 1),
    "major": ("\U0001f7e0", "Major outage", 2),
    "critical": ("\U0001f534", "Critical outage", 3),
}
_UNKNOWN = ("⚪", "Status unknown", 1)  # shown with the problems, not buried among the greens

# Colour bar by the worst severity in the result.
_COLORS = {0: "#2EB67D", 1: "#ECB22E", 2: "#E8912D", 3: "#E01E5A"}


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
    more_incidents: int = 0  # reported by the server but not kept
    url: str = ""  # the provider's status page; empty unless it is a plain https URL

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


def defang(text: str) -> str:
    """Stops `<!channel>`-style broadcasts in text a model may have copied from a status page.

    Only the `<!` that starts them is changed, so the model's own *bold*, links and mentions work.
    """
    return text.replace("<!", "&lt;!")


def slack_text(text: str) -> str:
    """A model's answer made safe and readable for Slack: no broadcasts, and **bold** as Slack's *bold*."""
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", defang(text))


def clean(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    """Single-line, length-capped, unescaped text from an untrusted value."""
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _https_url(value: object) -> str:
    """The URL if it is a plain https link that can sit inside a Slack `<url|label>` safely, else ''."""
    url = value.strip() if isinstance(value, str) else ""
    ok = url.startswith("https://") and len(url) <= MAX_URL_CHARS and not any(c.isspace() or c in "<>|" for c in url)
    return url if ok else ""


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
        raw = item.get("incidents")
        named = [i for i in (raw[:50] if isinstance(raw, list) else []) if isinstance(i, dict) and i.get("name")]
        incidents = tuple(
            Incident(name=clean(i["name"], 120), status=clean(i.get("status"), 40)) for i in named[:MAX_INCIDENTS]
        )
        services.append(
            Service(
                id=clean(item.get("id") or item["name"], 40).lower(),
                name=clean(item["name"], 60),
                indicator=indicator if indicator in _INDICATORS else "unknown",
                description=clean(item.get("description")),
                incidents=incidents,
                more_incidents=len(named) - len(incidents),
                url=_https_url(item.get("pageUrl")),
            )
        )
    return services


def summary_text(services: list[Service], for_slack: bool = False) -> str:
    """Plain lines for the model, one per service. `for_slack` escapes them for display."""
    esc = escape if for_slack else (lambda text: text)
    lines = []
    for service in services:
        line = f"- {esc(service.name)}: {service.label.lower()}"
        if service.description:
            line += f" ({esc(service.description)})"
        for incident in service.incidents:
            line += f"; incident: {esc(incident.name)} [{esc(incident.status)}]"
        lines.append(line)
    return "\n".join(lines)


def build_message(services: list[Service], message: str = "", now: float | None = None) -> dict:
    """The parts of a Slack message for these services: `blocks` (the answer) and `attachments` (the cards).

    The answer is the model's own short reply, or a plain list when it wrote none, so the
    text is never shown twice and never empty.
    """
    answer = slack_text(message.strip()) or summary_text(services, for_slack=True)
    worst = max((s.severity for s in services), default=0)
    return {
        "blocks": [_section(answer[:MAX_SECTION_CHARS])],
        "attachments": [{"color": _COLORS[worst], "blocks": build_card(services, now)}],
    }


def build_card(services: list[Service], now: float | None = None) -> list[dict]:
    """The blocks inside the coloured attachment."""
    ordered = sorted(services, key=lambda s: -s.severity)  # stable: ties keep the server's order
    problems = [s for s in ordered if s.severity]
    # One service is always shown in full; otherwise only problems are, and the rest is one line.
    detailed = ordered if len(ordered) == 1 else problems
    healthy = [] if len(ordered) == 1 else [s for s in ordered if not s.severity]
    when = int(now if now is not None else time.time())

    blocks: list[dict] = [_section(_title(len(ordered), len(problems), healthy))]

    for service in detailed[:MAX_PROBLEMS]:
        blocks.append(_card(service))
        if service.incidents:
            elements = [_incident(i) for i in service.incidents]
            if service.more_incidents:
                elements.append({"type": "mrkdwn", "text": f"_+{service.more_incidents} more_"})
            blocks.append({"type": "context", "elements": elements})
    if len(detailed) > MAX_PROBLEMS:
        blocks.append(_context(f"…and {len(detailed) - MAX_PROBLEMS} more with issues"))

    if problems and healthy:
        blocks.append(_section(_operational(healthy)))

    blocks.append(_context(f"Live from each provider's public status page · updated <!date^{when}^{{time}}|just now>"))
    return blocks


def _title(total: int, attention: int, healthy: list[Service]) -> str:
    title = "\U0001f6a6 *Service status*"
    if total == 1:
        return title
    if attention:
        return f"{title} · {attention} of {total} need attention"
    names = " · ".join(escape(s.name) for s in healthy)  # all healthy: the whole answer in one block
    return f"{title} · all {total} operational\n{names}"[:MAX_SECTION_CHARS]


def _operational(healthy: list[Service]) -> str:
    """One wrapped line for the healthy services: it reads the same in a narrow thread pane as in a wide one."""
    shown = healthy[:MAX_OPERATIONAL]
    names = " · ".join(escape(s.name) for s in shown)
    more = f" · …and {len(healthy) - len(shown)} more" if len(healthy) > len(shown) else ""
    return f"\U0001f7e2 *{len(healthy)} operational* · {names}{more}"[:MAX_SECTION_CHARS]


def _card(service: Service) -> dict:
    text = f"{service.emoji} *{escape(service.name)}* — {service.label}"
    if service.url:
        # A plain link, not a button: Slack sends every button click to the app's Interactivity URL,
        # even a URL button, and shows a warning icon on it when none is configured.
        text += f" · <{escape(service.url)}|Status page>"
    if service.description:
        text += f"\n{escape(service.description)}"
    return _section(text)


def _incident(incident: Incident) -> dict:
    text = f"⚠️ {escape(incident.name)}"
    if incident.status:
        text += f" · _{escape(incident.status)}_"
    return {"type": "mrkdwn", "text": text}


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

    def message(self, answer: str = "") -> dict | None:
        """`{"blocks": …, "attachments": …}` for the reply, or None if no status check ran."""
        return build_message(self.services, answer) if self.services else None
