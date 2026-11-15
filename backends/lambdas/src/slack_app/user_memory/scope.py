"""Where what a person says here is kept, and what may be recalled here (agent_worker -> the agent).

The bot's answer is read by everyone in the channel, so a memory may only shape an answer
where everyone could already read the place it was said (the tiers in __init__.py):

  asked in                        learned into      may recall
  a DM with the bot               "dm"              "dm", "public", this person's channel tiers
  public channel, no guests       "public"          "public"
  private channel P, no guests    P                 "public", P
  any channel with a guest in it  that channel      that channel only
  Slack Connect, group DM,        nothing: no memory for this answer
  or a channel Slack can't describe

Guests can only see the channels they were invited to, hence the fourth row. The scope
is decided here and the agent reads exactly these tiers: the model never chooses what
it may see. Same rules, and the same Slack calls, as past threads (knowledge/scope.py).
"""

import logging

from slack_app.knowledge import PRIVATE
from slack_app.knowledge.channels import ChannelUnknown, channel_info, has_guests
from slack_app.user_memory import DM, PUBLIC

logger = logging.getLogger(__name__)

# A DM recalls every tier the person has, two AgentCore calls each. Enough for the
# channels someone actually talks to the bot in.
MAX_RECALL_TIERS = 8


def memory_scope(client, channel_id: str, channel_tiers: frozenset[str] = frozenset()) -> dict | None:
    """{"learn": tier, "recall": [tiers]} for this channel, or None for no memory here.

    channel_tiers are the channel tiers this person already has memory in (consent.py).
    """
    if channel_id.startswith("D"):
        return {"learn": DM, "recall": [DM, PUBLIC, *sorted(channel_tiers)][:MAX_RECALL_TIERS]}
    try:
        channel = channel_info(client, channel_id)
    except ChannelUnknown:
        logger.warning("Couldn't tell what channel %s is; memory is off for this answer", channel_id)
        return None
    if channel.visibility is None:
        return None
    if has_guests(client, channel_id):
        return {"learn": channel_id, "recall": [channel_id]}
    if channel.visibility == PRIVATE:
        return {"learn": channel_id, "recall": [PUBLIC, channel_id]}
    return {"learn": PUBLIC, "recall": [PUBLIC]}
