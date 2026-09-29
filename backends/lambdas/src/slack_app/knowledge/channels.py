"""What kind of channel this is, as far as team knowledge goes.

Only internal channels the bot is a member of are indexed or searched: never DMs, group
DMs or Slack Connect channels shared with another organisation. Whether a channel is
public or private decides where its threads can be found (scope.py).

Both checks fail closed. channel_info raises when Slack doesn't give a clear answer, and
callers then skip the thread (indexer) or the search (scope.py). has_guests answers True
when it can't tell.
"""

import logging
import time
from dataclasses import dataclass

from slack_app.knowledge import PRIVATE, PUBLIC

logger = logging.getLogger(__name__)

# Slack's answers when the bot can't see a channel (any more): it was removed, or the
# channel was deleted. The channel is then treated as not indexable, not as unknown.
_GONE = {"channel_not_found", "not_in_channel"}

GUEST_CACHE_SECONDS = 3600
# conversations.members + users.info is one call per member the first time. Past this
# many members, the check gives up and assumes a guest may be there.
MAX_MEMBERS_CHECKED = 1000
_PAGE_SIZE = 200


class ChannelUnknown(Exception):
    """Slack didn't say what the channel is. Callers skip rather than guess."""


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    visibility: str | None
    """PUBLIC or PRIVATE for an internal channel; None for a DM, a group DM or a Slack Connect channel."""
    is_member: bool

    @property
    def indexable(self) -> bool:
        return self.visibility is not None and self.is_member


def channel_info(client, channel_id: str) -> Channel:
    if channel_id.startswith("D"):
        return Channel(channel_id, "", None, False)  # a DM: no need to ask
    try:
        response = client.conversations_info(channel=channel_id)
    except Exception as err:
        if _error(err) in _GONE:
            return Channel(channel_id, "", None, False)
        raise ChannelUnknown(channel_id) from err
    info = response.get("channel") or {}
    if "is_ext_shared" not in info or "is_private" not in info:
        raise ChannelUnknown(channel_id)
    return Channel(channel_id, info.get("name") or "", _visibility(info), bool(info.get("is_member")))


def _visibility(info: dict) -> str | None:
    if info.get("is_im") or info.get("is_mpim"):
        return None
    if info.get("is_ext_shared") or info.get("is_pending_ext_shared"):
        return None  # Slack Connect: people from another organisation are in it
    if info.get("is_private") or info.get("is_group"):
        return PRIVATE
    return PUBLIC if info.get("is_channel") else None


def _error(err: Exception) -> str:
    """Slack's error code from a slack_sdk SlackApiError, or "" for anything else."""
    response = getattr(err, "response", None)
    try:
        return response.get("error") or "" if response is not None else ""
    except (AttributeError, TypeError):
        return ""


# Per Lambda container. Guests come and go rarely; an hour's delay is the trade-off
# for not listing every member before every search.
_channel_guests: dict[str, tuple[float, bool]] = {}
_user_guest: dict[str, tuple[float, bool]] = {}


def has_guests(client, channel_id: str) -> bool:
    """Whether a single- or multi-channel guest is in the channel. True if that can't be checked."""
    now = time.time()
    cached = _channel_guests.get(channel_id)
    if cached and cached[0] > now:
        return cached[1]
    try:
        answer = _any_guest(client, channel_id, now)
    except Exception:
        logger.warning("Guest check failed for %s; limiting search to the channel", channel_id, exc_info=True)
        return True
    _channel_guests[channel_id] = (now + GUEST_CACHE_SECONDS, answer)
    return answer


def _any_guest(client, channel_id: str, now: float) -> bool:
    checked = 0
    cursor = None
    while True:
        kwargs = {"cursor": cursor} if cursor else {}
        response = client.conversations_members(channel=channel_id, limit=_PAGE_SIZE, **kwargs)
        for user_id in response.get("members") or []:
            checked += 1
            if checked > MAX_MEMBERS_CHECKED:
                logger.info("Channel %s has over %d members; assuming a guest may be in it", channel_id, checked - 1)
                return True
            if _is_guest(client, user_id, now):
                return True
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return False


def _is_guest(client, user_id: str, now: float) -> bool:
    cached = _user_guest.get(user_id)
    if cached and cached[0] > now:
        return cached[1]
    user = client.users_info(user=user_id).get("user") or {}
    answer = bool(user.get("is_restricted") or user.get("is_ultra_restricted"))
    _user_guest[user_id] = (now + GUEST_CACHE_SECONDS, answer)
    return answer


def clear_caches() -> None:
    _channel_guests.clear()
    _user_guest.clear()
