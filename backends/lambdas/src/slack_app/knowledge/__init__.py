"""Team knowledge memory: what the team worked out in one Slack thread, findable from others.

The Slack thread is still the bot's only conversation history (thread_history.py). This
package adds knowledge *across* threads: every thread in the internal channels the bot
is in is summarised once it has been quiet for a while, and the summary is stored in
S3 Vectors. The agent's search_past_threads tool finds and cites those summaries.

  * events.py   -- slack_events: which Slack events queue work for the indexer.
  * channels.py -- conversations.info and the guest check: may a channel be indexed or searched?
  * summary.py  -- one model call per thread: its problems and learnings as JSON, or SKIP.
  * embeddings.py, store.py -- Titan Text Embeddings v2 and the S3 Vectors index.
  * scope.py    -- agent_worker: which past threads a search in this channel may find.
  * handlers/knowledge_indexer.py runs the jobs and the daily sweep.

Only ever the Slack thread is indexed: never DMs, Slack Connect channels, or anything a
tool returned from someone's own GitHub, Linear, Notion or LinkedIn account.
"""

# Jobs on the knowledge-index queue (see events.py and handlers/knowledge_indexer.py).
CHECK = "check"
"""Summarise the thread, unless a newer message will: that message's own check covers it."""
DELETE_THREAD = "delete_thread"
DELETE_CHANNEL = "delete_channel"

# Bump when the stored summary or metadata changes shape.
# 2: one vector per problem and per learning, instead of one per thread plus side points.
SCHEMA_VERSION = 2

MAX_PROBLEMS = 3
MAX_LEARNINGS = 3
# Schema 1 keys. Still deleted with the thread, so a thread indexed before the change
# doesn't keep a stale vector next to its new ones.
_LEGACY_SUFFIXES = ("main", "side-1", "side-2", "side-3")

PUBLIC = "public"
PRIVATE = "private"


def problem_key(thread_key: str, n: int) -> str:
    return f"{thread_key}#problem-{n}"


def learning_key(thread_key: str, n: int) -> str:
    return f"{thread_key}#learning-{n}"


def vector_keys(thread_key: str) -> list[str]:
    """Every key a thread can use: one per problem, one per learning, and schema 1's. Written and deleted together."""
    return (
        [problem_key(thread_key, n) for n in range(1, MAX_PROBLEMS + 1)]
        + [learning_key(thread_key, n) for n in range(1, MAX_LEARNINGS + 1)]
        + [f"{thread_key}#{suffix}" for suffix in _LEGACY_SUFFIXES]
    )


def thread_key_of(vector_key: str) -> str:
    return vector_key.rsplit("#", 1)[0]


def channel_of(vector_key: str) -> tuple[str, str]:
    """(team_id, channel_id) from a key such as T1:C1:1726500000.000100#problem-1."""
    team_id, channel, _ = thread_key_of(vector_key).split(":", 2)
    return team_id, channel
