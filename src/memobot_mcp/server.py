import json

from mcp.server.fastmcp import FastMCP

from .client import MemobotClient

mcp = FastMCP("memobot-mcp")
client = MemobotClient()


def _json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


@mcp.tool()
def get_current_user() -> str:
    """Returns the profile of the logged-in Memobot account (name, email, phone, group)."""
    return _json(client.get_current_user())


@mcp.tool()
def list_recordings(page: int = 1, limit: int = 10) -> str:
    """Lists the account's voice recordings/meetings, newest first.

    Args:
        page: Page number, 1-indexed.
        limit: Items per page (1-100).
    """
    return _json(client.list_recordings(page=page, limit=limit))


@mcp.tool()
def get_recording_transcript(audio_id: str) -> str:
    """Returns the raw recording detail for one audioId, including its transcript
    and (when present) a pre-signed, unauthenticated URL to the audio file itself.

    Unverified: the account this server was built against had zero recordings, so
    this endpoint's exact response schema was never confirmed against real data.
    The transcript is reported to live under content.document.children as
    paragraphs of word tokens (each with text + stime/etime in ms) — read the raw
    JSON returned here rather than assuming a parsed/flattened structure.

    Args:
        audio_id: The recording's _id, from list_recordings.
    """
    return _json(client.get_audio_detail(audio_id))


@mcp.tool()
def get_recording_summary(audio_id: str) -> str:
    """Returns the raw AI-generated summary feed for one audioId.

    Unverified for the same reason as get_recording_transcript — the summary
    text is reported to live in a "content" or "text" field of the response.

    Args:
        audio_id: The recording's _id, from list_recordings.
    """
    return _json(client.get_recording_summary(audio_id))


@mcp.tool()
def get_user_info() -> str:
    """Returns account usage/stats info (analytic-v2 userStats/user-info)."""
    return _json(client.get_user_info())


@mcp.tool()
def get_user_package(limit: int = 1000) -> str:
    """Returns the account's current subscription package(s), e.g. plan name and quota."""
    return _json(client.get_user_package(limit=limit))


@mcp.tool()
def get_usage_stats() -> str:
    """Returns recording-minute usage stats for the current billing period."""
    return _json(client.get_usage_stats())


@mcp.tool()
def get_user_config() -> str:
    """Returns account-level config/settings (feature flags, remote config)."""
    return _json(client.get_user_config())


@mcp.tool()
def get_api_key() -> str:
    """Returns the account's ASR/TTS API key and its quota (amount in seconds, expiry)."""
    return _json(client.get_api_key())


@mcp.tool()
def get_notifications(max_result: int = 10) -> str:
    """Returns the account's recent in-app notifications.

    Args:
        max_result: Maximum number of notifications to return (1-100).
    """
    return _json(client.get_notifications(max_result=max_result))


# Prompts are what Claude Code surfaces as slash commands
# (`/mcp__memobot__<name>`) — tools alone are only ever called implicitly by
# the model. Each one just runs the matching tool and drops its JSON straight
# into the conversation, for a fast manual lookup without relying on the
# model to decide to call anything.


@mcp.prompt(name="get_current_user")
def prompt_get_current_user() -> str:
    """Show the current Memobot account profile."""
    return get_current_user()


@mcp.prompt(name="list_recordings")
def prompt_list_recordings(page: str = "1", limit: str = "10") -> str:
    """List the account's voice recordings/meetings, newest first."""
    return list_recordings(page=int(page), limit=int(limit))


@mcp.prompt(name="get_recording_transcript")
def prompt_get_recording_transcript(audio_id: str) -> str:
    """Show the raw transcript detail for one recording."""
    return get_recording_transcript(audio_id)


@mcp.prompt(name="get_recording_summary")
def prompt_get_recording_summary(audio_id: str) -> str:
    """Show the raw AI summary for one recording."""
    return get_recording_summary(audio_id)


@mcp.prompt(name="get_user_info")
def prompt_get_user_info() -> str:
    """Show account usage/stats info."""
    return get_user_info()


@mcp.prompt(name="get_user_package")
def prompt_get_user_package() -> str:
    """Show the account's current subscription package."""
    return get_user_package()


@mcp.prompt(name="get_usage_stats")
def prompt_get_usage_stats() -> str:
    """Show recording-minute usage stats for the current billing period."""
    return get_usage_stats()


@mcp.prompt(name="get_user_config")
def prompt_get_user_config() -> str:
    """Show account-level config/settings."""
    return get_user_config()


@mcp.prompt(name="get_api_key")
def prompt_get_api_key() -> str:
    """Show the account's ASR/TTS API key and quota."""
    return get_api_key()


@mcp.prompt(name="get_notifications")
def prompt_get_notifications() -> str:
    """Show recent in-app notifications."""
    return get_notifications()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
