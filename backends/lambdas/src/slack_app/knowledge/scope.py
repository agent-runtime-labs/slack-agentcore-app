"""Which past threads a search from this channel may find (agent_worker -> the agent).

The bot's answer is posted where everyone in the channel can read it, so past-thread
content may only appear where everyone could already read the channel it came from:

  asked in                          may find                           scope
  public channel, no guests         public threads                     "public"
  private channel P, no guests      public threads, plus P's own       "public+channel"
  any channel with a guest in it    only this channel's own threads    "channel"
  DM, Slack Connect, or unknown     nothing: no tool at all            "none"

Guests can only see the channels they were invited to, hence the third row. The scope
is decided here and the agent builds its QueryVectors filter from it: the model never
chooses what it may see.
"""

import logging

from slack_app.engaged_threads import thread_key
from slack_app.knowledge import PRIVATE
from slack_app.knowledge.channels import ChannelUnknown, channel_info, has_guests

logger = logging.getLogger(__name__)

NONE = "none"
PUBLIC_ONLY = "public"
PUBLIC_AND_CHANNEL = "public+channel"
CHANNEL_ONLY = "channel"


def search_scope(client, team_id: str, channel: str, thread_ts: str) -> dict:
    """The "knowledge" field of the agent payload."""
    return {
        "scope": _scope(client, channel),
        "teamId": team_id,
        "channelId": channel,
        # The thread being answered is already in front of the agent; never return it.
        "threadKey": thread_key(team_id, channel, thread_ts),
    }


def _scope(client, channel_id: str) -> str:
    try:
        channel = channel_info(client, channel_id)
    except ChannelUnknown:
        logger.warning("Couldn't tell what channel %s is; past threads are off for this answer", channel_id)
        return NONE
    if channel.visibility is None:
        return NONE
    if has_guests(client, channel_id):
        return CHANNEL_ONLY
    return PUBLIC_AND_CHANNEL if channel.visibility == PRIVATE else PUBLIC_ONLY
