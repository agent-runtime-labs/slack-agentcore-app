"""The Block Kit cards for the service-status tool: layout, ordering and untrusted text."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from status_cards import (  # noqa: E402
    MAX_OPERATIONAL,
    MAX_PROBLEMS,
    MAX_SERVICES,
    StatusCards,
    build_card,
    build_message,
    defang,
    escape,
    parse_services,
    slack_text,
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


def services_of(*items):
    return parse_services({"services": list(items)})


def texts(blocks):
    """Every mrkdwn / plain_text string in the blocks, in order (fields and context elements too)."""
    out = []
    for block in blocks:
        if "text" in block:
            out.append(block["text"]["text"])
        for element in [*block.get("elements", []), *block.get("fields", [])]:
            out.append(element["text"])
    return out


def card(*items):
    return build_card(services_of(*items), now=NOW)


def types(blocks):
    return [b["type"] for b in blocks]


# --- parsing ---------------------------------------------------------------------------


def test_parses_a_statuspulse_result():
    services = services_of(
        raw(incidents=[{"name": "Delayed webhooks", "impact": "minor", "status": "investigating"}]),
        raw("Discord", "major", "Partial Outage", pageUrl="https://discordstatus.com"),
    )

    assert [s.name for s in services] == ["GitHub", "Discord"]
    assert services[0].incidents[0].name == "Delayed webhooks"
    assert (services[1].indicator, services[1].url) == ("major", "https://discordstatus.com")


def test_malformed_results_give_no_services():
    for bad in (None, "x", [], {}, {"services": "nope"}, {"services": [None, 3, {"id": "x"}]}):
        assert parse_services(bad) == []


def test_one_bad_entry_does_not_drop_the_others():
    assert [s.name for s in services_of({"nonsense": True}, raw("Cloudflare"))] == ["Cloudflare"]


def test_malformed_incident_lists_are_ignored_not_fatal():
    for bad in ("oops", {"name": "x"}, 7, [None, "x", {"status": "no name"}]):
        [service] = services_of(raw(incidents=bad))
        assert service.incidents == () and service.more_incidents == 0


def test_unknown_indicator_is_shown_as_unknown_not_passed_through():
    [service] = services_of(raw(indicator="<!channel> pwned"))

    assert service.indicator == "unknown"
    assert service.emoji == "⚪"


def test_parsing_caps_incidents_but_remembers_how_many_were_left_out():
    incidents = [{"name": f"inc{i}", "status": "investigating"} for i in range(9)]

    [service] = services_of(raw(incidents=incidents))

    assert len(service.incidents) == 3
    assert service.more_incidents == 6


def test_parsing_caps_the_number_of_services():
    assert len(services_of(*[raw(f"S{i}") for i in range(MAX_SERVICES + 5)])) == MAX_SERVICES


def test_only_plain_https_urls_without_slack_control_characters_become_links():
    good = "https://www.githubstatus.com"
    bad = ["http://insecure.example", "javascript:alert(1)", "https://a b", "https://x.example/>|<!channel>", "", None, 5]
    urls = [good, *bad]

    got = [s.url for s in services_of(*[raw(f"S{i}", pageUrl=u) for i, u in enumerate(urls)])]

    assert got == [good, "", "", "", "", "", "", ""]


# --- layout ----------------------------------------------------------------------------


def test_problems_get_full_cards_worst_first_and_healthy_ones_one_line():
    blocks = card(
        raw("Discord"),
        raw("GitHub", "critical", "Major Outage"),
        raw("Cloudflare", "minor", "Degraded"),
        raw("npm"),
    )

    assert types(blocks) == ["section", "section", "section", "section", "context"]
    title, first, second, healthy = texts(blocks)[0], blocks[1], blocks[2], blocks[3]
    assert title == "\U0001f6a6 *Service status* · 2 of 4 need attention"
    assert first["text"]["text"] == "\U0001f534 *GitHub* — Critical outage\nMajor Outage"
    assert second["text"]["text"].startswith("\U0001f7e1 *Cloudflare*")
    assert healthy["text"]["text"] == "\U0001f7e2 *2 operational* · Discord · npm"
    assert not any("fields" in b for b in blocks)  # fields stack into one column in a narrow thread pane
    assert f"<!date^{NOW}^{{time}}|just now>" in texts(blocks)[-1]


def test_when_everything_is_healthy_it_is_one_block_and_a_footer():
    blocks = card(raw("GitHub"), raw("Discord"), raw("npm"))

    assert types(blocks) == ["section", "context"]
    assert texts(blocks)[0] == "\U0001f6a6 *Service status* · all 3 operational\nGitHub · Discord · npm"


def test_a_single_service_is_always_shown_in_full():
    healthy = card(raw("GitHub"))
    broken = card(raw("GitHub", "major", "Outage"))

    assert texts(healthy)[0] == "\U0001f6a6 *Service status*"
    assert texts(healthy)[1] == "\U0001f7e2 *GitHub* — Operational\nAll Systems Operational"
    assert types(healthy) == ["section", "section", "context"]
    assert broken[1]["text"]["text"].startswith("\U0001f7e0 *GitHub* — Major outage")


def test_incidents_go_in_a_context_block_under_their_service_with_a_count_of_the_rest():
    incidents = [{"name": f"inc{i}", "status": "investigating"} for i in range(5)]

    blocks = card(raw("GitHub", "minor", "Degraded", incidents), raw("npm"))

    assert types(blocks) == ["section", "section", "context", "section", "context"]
    elements = [e["text"] for e in blocks[2]["elements"]]
    assert elements == [f"⚠️ inc{i} · _investigating_" for i in range(3)] + ["_+2 more_"]


def test_a_status_page_link_goes_on_the_title_line_and_is_not_a_button():
    [_, section, *_] = card(raw("GitHub", "minor", "Degraded", pageUrl="https://www.githubstatus.com"), raw("npm"))

    assert section["text"]["text"] == (
        "\U0001f7e1 *GitHub* — Minor issues · <https://www.githubstatus.com|Status page>\nDegraded"
    )
    assert "accessory" not in section  # buttons make Slack ask for an Interactivity URL


def test_no_url_means_no_link():
    [_, section, *_] = card(raw("GitHub", "minor", "Degraded"), raw("npm"))

    assert section["text"]["text"] == "\U0001f7e1 *GitHub* — Minor issues\nDegraded"


def test_a_url_is_escaped_inside_the_link():
    [_, section, *_] = card(raw("GitHub", "minor", "x", pageUrl="https://s.example/?a=1&b=2"), raw("npm"))

    assert "<https://s.example/?a=1&amp;b=2|Status page>" in section["text"]["text"]


def test_the_message_is_the_models_answer_plus_a_coloured_attachment():
    message = build_message(services_of(raw(), raw("Cloudflare", "major", "Outage")), "  Cloudflare is down.  ", NOW)

    assert message["blocks"] == [{"type": "section", "text": {"type": "mrkdwn", "text": "Cloudflare is down."}}]
    [attachment] = message["attachments"]
    assert attachment["color"] == "#E8912D"
    assert attachment["blocks"][0]["text"]["text"].startswith("\U0001f6a6 *Service status*")


def test_the_bar_colour_follows_the_worst_service():
    def color(*items):
        return build_message(services_of(*items), "x", NOW)["attachments"][0]["color"]

    assert color(raw()) == "#2EB67D"
    assert color(raw(), raw("B", "minor", "x")) == "#ECB22E"
    assert color(raw(), raw("B", "major", "x")) == "#E8912D"
    assert color(raw("A", "minor", "x"), raw("B", "critical", "x")) == "#E01E5A"
    assert color(raw(indicator="mystery")) == "#ECB22E"


def test_without_a_model_answer_the_plain_summary_is_the_answer_block():
    message = build_message(services_of(raw("GitHub", "major", "<!channel> Outage")), "   ", NOW)

    [block] = message["blocks"]
    assert block["text"]["text"] == "- GitHub: major outage (&lt;!channel&gt; Outage)"


def test_stays_well_inside_slacks_limits_however_much_the_server_returns():
    incidents = [{"name": f"i{j}", "status": "s"} for j in range(9)]
    items = [raw(f"Bad{i}", "minor", "x", incidents, pageUrl="https://x.example") for i in range(20)]
    items += [raw(f"Ok{i}") for i in range(20)]

    message = build_message(services_of(*items), "x" * 10_000, NOW)

    [attachment] = message["attachments"]
    assert len(attachment["blocks"]) + len(message["blocks"]) <= 50
    assert all(len(t) <= 3000 for t in texts(attachment["blocks"]) + texts(message["blocks"]))


def test_a_problem_late_in_a_long_list_is_not_lost_to_the_cap():
    # The broken service is the 12th of 14: capping before sorting would have dropped it.
    items = [raw(f"Ok{i}") for i in range(11)] + [raw("Broken", "critical", "Down")] + [raw("Ok11"), raw("Ok12")]

    blocks = card(*items)

    assert texts(blocks)[0].endswith("1 of 14 need attention")
    assert blocks[1]["text"]["text"].startswith("\U0001f534 *Broken*")


def test_overflow_is_summarised_not_dropped_silently():
    items = [raw(f"Bad{i}", "minor", "x") for i in range(MAX_PROBLEMS + 2)] + [raw(f"Ok{i}") for i in range(3)]

    assert any(t == "…and 2 more with issues" for t in texts(card(*items)))

    many_ok = [raw("Bad", "minor", "x")] + [raw(f"Ok{i}") for i in range(MAX_OPERATIONAL + 4)]
    line = next(t for t in texts(card(*many_ok)) if "operational" in t)
    assert line.endswith("· …and 4 more") and line.startswith("\U0001f7e2 *24 operational*")


# --- untrusted text --------------------------------------------------------------------


def test_escape_neutralises_slack_control_sequences():
    assert escape("<!channel> <@U1> <https://evil|click> & co") == (
        "&lt;!channel&gt; &lt;@U1&gt; &lt;https://evil|click&gt; &amp; co"
    )


def test_defang_only_stops_broadcasts_and_leaves_the_models_own_formatting():
    assert defang("<!channel> hi <@U1> see <https://x.example|here> *bold*") == (
        "&lt;!channel> hi <@U1> see <https://x.example|here> *bold*"
    )


def test_status_page_text_cannot_ping_or_link():
    nasty = raw(
        "<!everyone> GitHub",
        "minor",
        "<!channel> everyone, see <https://evil.example|this>",
        [{"name": "<!here> outage", "status": "<@U123>"}],
    )

    joined = "\n".join(texts(card(nasty, raw("<!channel> npm"))))

    for control in ("<!channel>", "<!here>", "<!everyone>", "<@U123>", "<https://evil.example|this>"):
        assert control not in joined
    assert "&lt;!channel&gt;" in joined


def test_slack_text_turns_markdown_bold_into_slack_bold():
    assert slack_text("**Cloudflare** and **Twilio** are *fine*") == "*Cloudflare* and *Twilio* are *fine*"


def test_the_models_answer_cannot_ping_either():
    message = build_message(services_of(raw()), "<!channel> GitHub is fine", NOW)

    assert "<!channel>" not in message["blocks"][0]["text"]["text"]


def test_long_multiline_text_is_collapsed_and_truncated():
    [service] = services_of(raw(description="line one\n\n\nline two " + "x" * 1000))

    assert "\n" not in service.description
    assert len(service.description) <= 300
    assert service.description.endswith("…")


# --- the per-request holder ------------------------------------------------------------


def test_summary_text_has_one_line_per_service_with_incidents():
    services = services_of(raw(), raw("Discord", "minor", "Degraded", [{"name": "Voice lag", "status": "monitoring"}]))

    assert summary_text(services).splitlines() == [
        "- GitHub: operational (All Systems Operational)",
        "- Discord: minor issues (Degraded); incident: Voice lag [monitoring]",
    ]


def test_summary_text_is_escaped_only_for_slack():
    services = services_of(raw("A&B", "minor", "<!here>"))

    assert summary_text(services) == "- A&B: minor issues (<!here>)"
    assert summary_text(services, for_slack=True) == "- A&amp;B: minor issues (&lt;!here&gt;)"


def test_nothing_to_show_until_a_tool_has_run():
    assert StatusCards().message("hi") is None


def test_a_second_call_adds_services_and_replaces_repeats():
    cards = StatusCards()
    cards.show(services_of(raw("GitHub")))
    cards.show(services_of(raw("Cloudflare"), raw("GitHub", "major", "Outage")))

    assert [(s.name, s.indicator) for s in cards.services] == [("GitHub", "major"), ("Cloudflare", "none")]
    assert cards.message("hi")["blocks"][0]["text"]["text"] == "hi"
