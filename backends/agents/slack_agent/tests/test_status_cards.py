"""The Block Kit cards for the service-status tool: layout, ordering and untrusted text."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from status_cards import (  # noqa: E402
    MAX_SERVICES,
    StatusCards,
    build_blocks,
    escape,
    parse_services,
    summary_text,
)

NOW = 1_760_000_000


def raw(name="GitHub", indicator="none", description="All Systems Operational", incidents=None, **extra):
    return {
        "id": name.lower(),
        "name": name,
        "indicator": indicator,
        "description": description,
        "incidents": incidents or [],
        **extra,
    }


def texts(blocks):
    """Every mrkdwn / plain_text string in the blocks, in order."""
    out = []
    for block in blocks:
        if "text" in block:
            out.append(block["text"]["text"])
        for element in block.get("elements", []):
            out.append(element["text"])
    return out


# --- parsing ---------------------------------------------------------------------------


def test_parses_a_statuspulse_result():
    services = parse_services(
        {
            "services": [
                raw(incidents=[{"name": "Delayed webhooks", "impact": "minor", "status": "investigating"}]),
                raw("Discord", "major", "Partial Outage"),
            ]
        }
    )

    assert [s.name for s in services] == ["GitHub", "Discord"]
    assert services[0].incidents[0].name == "Delayed webhooks"
    assert services[1].indicator == "major"


def test_malformed_results_give_no_services():
    for bad in (None, "x", [], {}, {"services": "nope"}, {"services": [None, 3, {"id": "x"}]}):
        assert parse_services(bad) == []


def test_one_bad_entry_does_not_drop_the_others():
    services = parse_services({"services": [{"nonsense": True}, raw("Cloudflare")]})

    assert [s.name for s in services] == ["Cloudflare"]


def test_unknown_indicator_is_shown_as_unknown_not_passed_through():
    [service] = parse_services({"services": [raw(indicator="<!channel> pwned")]})

    assert service.indicator == "unknown"
    assert service.emoji == "⚪"


def test_parsing_caps_services_and_incidents():
    many = [raw(f"Svc{i}") for i in range(MAX_SERVICES + 5)]
    incidents = [{"name": f"inc{i}", "status": "investigating"} for i in range(9)]

    assert len(parse_services({"services": many})) == MAX_SERVICES
    [service] = parse_services({"services": [raw(incidents=incidents)]})
    assert len(service.incidents) == 3


# --- layout ----------------------------------------------------------------------------


def test_layout_is_header_headline_then_one_card_per_service():
    blocks = build_blocks(parse_services({"services": [raw(), raw("Cloudflare")]}), now=NOW)

    assert [b["type"] for b in blocks] == ["header", "context", "section", "section", "context"]
    assert texts(blocks)[0] == "\U0001f6a6 Service status"
    assert "All 2 services operational" in texts(blocks)[1]
    assert texts(blocks)[2] == "\U0001f7e2 *GitHub* — Operational\nAll Systems Operational"
    assert f"<!date^{NOW}^{{time}}|just now>" in texts(blocks)[-1]


def test_problems_come_first_and_are_counted():
    services = parse_services(
        {
            "services": [
                raw("Discord"),
                raw("GitHub", "critical", "Major Outage"),
                raw("Cloudflare", "minor", "Degraded"),
            ]
        }
    )

    blocks = build_blocks(services, now=NOW)

    cards = [t for t in texts(blocks) if "—" in t]
    assert [c.split("*")[1] for c in cards] == ["GitHub", "Cloudflare", "Discord"]
    assert "2 of 3 services need attention" in texts(blocks)[1]
    assert cards[0].startswith("\U0001f534") and cards[1].startswith("\U0001f7e1")


def test_a_single_service_headline_reads_naturally():
    assert "Operational" == texts(build_blocks(parse_services({"services": [raw()]}), now=NOW))[1]
    broken = parse_services({"services": [raw(indicator="major")]})
    assert "1 of 1 service need attention" in texts(build_blocks(broken, now=NOW))[1]


def test_incidents_go_in_a_context_block_under_their_service():
    service = raw("GitHub", "minor", "Degraded", [{"name": "Delayed webhooks", "status": "investigating"}])

    blocks = build_blocks(parse_services({"services": [service]}), now=NOW)

    assert [b["type"] for b in blocks] == ["header", "context", "section", "context", "context"]
    assert blocks[3]["elements"][0]["text"] == "⚠️ Delayed webhooks · _investigating_"


def test_the_models_answer_is_the_first_block():
    blocks = build_blocks(parse_services({"services": [raw()]}), message="  GitHub looks fine.  ", now=NOW)

    assert blocks[0] == {"type": "section", "text": {"type": "mrkdwn", "text": "GitHub looks fine."}}
    assert blocks[1]["type"] == "header"


def test_stays_well_inside_slacks_block_limit():
    incidents = [{"name": f"i{j}", "status": "s"} for j in range(3)]
    services = parse_services({"services": [raw(f"S{i}", "minor", "x", incidents) for i in range(30)]})

    blocks = build_blocks(services, message="x" * 10_000, now=NOW)

    assert len(blocks) <= 50
    assert all(len(t) <= 3000 for t in texts(blocks))


# --- untrusted text --------------------------------------------------------------------


def test_escape_neutralises_slack_control_sequences():
    assert escape("<!channel> <@U1> <https://evil|click> & co") == (
        "&lt;!channel&gt; &lt;@U1&gt; &lt;https://evil|click&gt; &amp; co"
    )


def test_status_page_text_cannot_ping_or_link():
    nasty = raw(
        "GitHub",
        "minor",
        "<!channel> everyone, see <https://evil.example|this>",
        [{"name": "<!here> outage", "status": "<@U123>"}],
    )

    blocks = build_blocks(parse_services({"services": [nasty]}), now=NOW)

    joined = "\n".join(texts(blocks))
    for control in ("<!channel>", "<!here>", "<@U123>", "<https://evil.example|this>"):
        assert control not in joined
    assert "&lt;!channel&gt;" in joined


def test_long_multiline_text_is_collapsed_and_truncated():
    [service] = parse_services({"services": [raw(description="line one\n\n\nline two " + "x" * 1000)]})

    assert "\n" not in service.description
    assert len(service.description) <= 300
    assert service.description.endswith("…")


# --- the per-request holder ------------------------------------------------------------


def test_summary_text_has_one_line_per_service_with_incidents():
    services = parse_services(
        {"services": [raw(), raw("Discord", "minor", "Degraded", [{"name": "Voice lag", "status": "monitoring"}])]}
    )

    assert summary_text(services).splitlines() == [
        "- GitHub: operational (All Systems Operational)",
        "- Discord: minor issues (Degraded); incident: Voice lag [monitoring]",
    ]


def test_cards_are_none_until_a_tool_has_run():
    assert StatusCards().blocks("hi") is None


def test_a_second_call_adds_services_and_replaces_repeats():
    cards = StatusCards()
    cards.show(parse_services({"services": [raw("GitHub")]}))
    cards.show(parse_services({"services": [raw("Cloudflare"), raw("GitHub", "major", "Outage")]}))

    assert [(s.name, s.indicator) for s in cards.services] == [("GitHub", "major"), ("Cloudflare", "none")]
    assert cards.blocks("hi")[0]["text"]["text"] == "hi"
