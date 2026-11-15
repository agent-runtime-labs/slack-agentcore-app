"""Per-user memory: what one person told the bot about themselves, kept across threads.

The Slack thread is still the conversation history (thread_history.py), and team
knowledge (knowledge/) is still what threads concluded. This adds the person: how they
like answers, what they own, which tools they use. It lives in AgentCore Memory, written
and read by the agent (user_memory.py there). This package is the Slack side of it:

  * consent.py -- nothing is saved or read until the person says yes. The answer is stored here.
  * prompt.py  -- the private Slack message that asks, and the buttons on it.
  * scope.py   -- agent_worker: where what's said here is kept, and what may be recalled here.
  * forget.py  -- deletes everything kept for a person when they turn memory off.
  * handlers/slack_button_clicks.py records the button clicks.

Where something was learned decides where it can be used, because the bot's answer is
read by everyone in the channel. A person's memory is therefore split into tiers, and
each tier is its own AgentCore actor:

  tier          learned in                                   recalled in
  "dm"          a DM with the bot                            DMs only
  "public"      a public channel with no guests              anywhere
  <channel ID>  a private channel, or any channel a guest    that channel, and DMs
                is in

Nothing is learned or recalled in Slack Connect channels or group DMs.
"""

DM = "dm"
PUBLIC = "public"

# Job on the processing queue: delete everything kept for a person (forget.py).
FORGET = "forget_memory"

# Where the memory's two strategies write their records. Must match the namespaces in
# infra-as-code/tf-app/memory.tf and user_memory.py in the agent.
_NAMESPACES = ("/users/{actor}/preferences/", "/users/{actor}/facts/")


def actor_id(user_id: str, tier: str) -> str:
    """The AgentCore Memory actor holding one person's memory from one tier."""
    return f"{user_id}-{tier}"


def namespaces(actor: str) -> list[str]:
    return [namespace.format(actor=actor) for namespace in _NAMESPACES]
