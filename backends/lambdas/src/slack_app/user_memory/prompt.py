"""The private Slack message that asks a person whether the bot may remember them.

Ephemeral, like the Connect link: only that person sees it. The buttons are handled by
handlers/slack_button_clicks.py, which trusts who clicked (Slack signs it), never the
button's value.

Changing what NOTICE says the bot remembers means bumping consent.NOTICE_VERSION.
"""

import logging

from slack_app.user_memory.consent import Consent

logger = logging.getLogger(__name__)

GRANT = "memory_consent_grant"
DECLINE = "memory_consent_decline"
FORGET = "memory_consent_forget"
KEEP = "memory_consent_keep"

NOTICE = (
    "*Want me to remember your preferences?*\n"
    "I can remember things you tell me about yourself, such as how you like answers and which projects you own, "
    "so you don't have to repeat them. I never save other people's messages, your files, or anything from your "
    "connected accounts. What you tell me in a DM or a private channel is only used there."
)
TURN_OFF = (
    "*Memory is on.*\n"
    "I remember things you tell me about yourself. Turning it off also deletes everything I've kept about you."
)

GRANTED_TEXT = "✅ Memory is on. I'll start remembering from your next message. Say “turn off memory” any time."
DECLINED_TEXT = "👍 No problem, I won't remember anything. Say “turn on memory” if you change your mind."
FORGOTTEN_TEXT = "🧹 Memory is off, and I'm deleting everything I'd kept about you."
KEPT_TEXT = "👍 Memory stays on."


def blocks(consent: Consent | None) -> tuple[str, list[dict]]:
    """(fallback text, blocks): the question, or the off switch for someone who already said yes."""
    if consent and consent.granted:
        text = TURN_OFF
        buttons = [_button("Turn off and delete everything", FORGET, "danger"), _button("Keep it on", KEEP)]
    else:
        text = NOTICE
        buttons = [_button("Yes, remember", GRANT, "primary"), _button("No thanks", DECLINE)]
    return text, [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "actions", "elements": buttons},
    ]


def offer(slack, channel: str, user: str, thread_ts: str, consent: Consent | None) -> None:
    """Best-effort: the answer is already posted, so a failure here is only logged."""
    text, message_blocks = blocks(consent)
    try:
        slack.chat_postEphemeral(channel=channel, user=user, thread_ts=thread_ts, text=text, blocks=message_blocks)
    except Exception:
        logger.warning("Failed to post the memory question", exc_info=True)


def _button(label: str, action_id: str, style: str | None = None) -> dict:
    button = {"type": "button", "text": {"type": "plain_text", "text": label}, "action_id": action_id}
    if style:
        button["style"] = style
    return button
