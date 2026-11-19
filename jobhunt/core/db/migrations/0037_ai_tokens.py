"""Migration 0037: the tokens and the thinking effort of each AI call, so a document write's cost can be read.

- `ai_usage.tokens_in` and `tokens_out` are what the AI reported reading and writing (thinking and answer
  together), and `tokens_thinking` the thinking's part of `tokens_out`: reported, or estimated by characters
  (`ai.openai_compat.tokens`). NULL where the AI reports none (the Claude CLI) and for every call before this.
- `ai_usage.effort` is the `reasoning_effort` sent with the call (`ai.effort`), NULL when none was sent.
- `ai_usage_action` finds an action's calls, which `jobhunt docs-time` reads.
"""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Added the tokens and thinking effort of each AI call"


def up(con):
    """Adds the token counts and the effort to `ai_usage`, and an index by action."""
    add_column(con, "ai_usage", "tokens_in", "INTEGER")
    add_column(con, "ai_usage", "tokens_out", "INTEGER")
    add_column(con, "ai_usage", "tokens_thinking", "INTEGER")
    add_column(con, "ai_usage", "effort", "TEXT")
    run_sql(con, "CREATE INDEX IF NOT EXISTS ai_usage_action ON ai_usage(action_id);")
