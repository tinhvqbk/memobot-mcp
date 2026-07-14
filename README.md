# memobot-mcp

Personal, unofficial MCP server for [Memobot](https://app.memobot.io), a voice-recording/
transcription app.

There is no public Memobot developer API. Everything here was reverse-engineered from
`app.memobot.io`'s own network traffic against a real logged-in session. Endpoints, field
names, and the token scheme may change or break without notice.

## Auth model

No password ever touches this codebase. The first time a tool needs a token and none is
cached, it pops a real Chromium window at Memobot's login page and waits for you to sign
in by hand (email/password, or the Google/Facebook/Apple buttons) — it just listens for
the app's own login response and reads the token out of it. That token is cached to
`~/.config/memobot-mcp/credentials.json` (0600 permissions) and reused for its ~1 year
validity. When it expires (or an API call gets a 401), the server first tries a silent
refresh via the cached refresh_token (`POST /authen/api/v1/auth/token`) — the browser only
pops up again if there's no cache at all or the refresh_token itself has been revoked
(e.g. after a password change).

## Run standalone

```bash
uvx --from git+https://github.com/tinhvqbk/memobot-mcp memobot
```

## Quick setup

**Claude Code**

```bash
claude mcp add memobot --scope user -- uvx --from git+https://github.com/tinhvqbk/memobot-mcp memobot
```

`--scope user` registers it in `~/.claude.json`, so it's available in every project for
your user. Use `--scope local` to scope it to the current project instead.

Tools are namespaced as `mcp__memobot__<tool>`, e.g. `mcp__memobot__get_current_user`.
Every tool also has a matching MCP *prompt*, surfaced as a slash command
(`/mcp__memobot__get_current_user`, etc.) for a direct manual call.

**Codex**

```bash
codex mcp add memobot -- uvx --from git+https://github.com/tinhvqbk/memobot-mcp memobot
```

or in `~/.codex/config.toml`:

```toml
[mcp_servers.memobot]
command = "uvx"
args = ["--from", "git+https://github.com/tinhvqbk/memobot-mcp", "memobot"]
```

**OpenClaw**

```bash
openclaw mcp add memobot --command uvx --arg --from --arg git+https://github.com/tinhvqbk/memobot-mcp --arg memobot
```

or in `openclaw.json`:

```json
{
  "mcp": {
    "servers": {
      "memobot": {
        "command": "uvx",
        "args": ["--from", "git+https://github.com/tinhvqbk/memobot-mcp", "memobot"]
      }
    }
  }
}
```

**Any other MCP client** (Claude Desktop, etc.) uses the same `command`/`args` shape as
the Codex/OpenClaw JSON above.

## Troubleshooting

**First time on a given machine, run this once by hand before adding it to any client:**

```bash
curl -fsSL https://raw.githubusercontent.com/tinhvqbk/memobot-mcp/main/scripts/preflight.sh | bash
```

It checks/installs `uv`, then does a throwaway launch to clone+build the package. Skipping
this is the usual cause of a client reporting a startup/pipe error (e.g. EPIPE): the very
first `uvx --from git+...` run can take 10-30s to clone and build, and some clients (OpenClaw
included) give up and kill the process before that finishes — which surfaces as a generic
pipe error that has nothing to do with memobot-mcp itself. Once the cache is warm, every
later launch by any client starts near-instantly.

## Tools

- `get_current_user` — account profile
- `list_recordings` — paginated list of voice recordings/meetings
- `get_recording_transcript` — raw detail (including transcript) for one recording, by audioId — see caveat below
- `get_recording_summary` — raw AI-generated summary for one recording, by audioId — see caveat below
- `get_user_info` — account usage/stats
- `get_user_package` — current subscription package
- `get_usage_stats` — recording-minute usage for the current period
- `get_user_config` — account-level feature flags/config
- `get_api_key` — the account's ASR/TTS API key and quota
- `get_notifications` — recent in-app notifications

## Known gaps

`get_recording_transcript` and `get_recording_summary` return raw JSON rather than a
parsed transcript/summary, because their response schema on a real recording is
unverified — a bespoke parser against an unconfirmed shape could silently produce wrong
output instead of an obvious error. The transcript reportedly lives under
`content.document.children` as paragraphs of word tokens (`text` + `stime`/`etime` in
ms); the summary lives in a `content`/`text` field; the audio detail also carries a
pre-signed, unauthenticated S3 URL under `audio_document.url`. Worth adding a parsed
variant once verified against real data.

File-upload and "join meeting" flows are unobserved and unimplemented.

## Development

```bash
uv sync --all-groups   # install runtime + dev dependencies
uv run ruff check .    # lint
uv run ruff format .   # format
uv run pytest -q       # unit tests (mocked — no real network or browser calls)
```

CI runs lint, format-check, and tests on every push/PR to `main`. There's intentionally no
CI job that exercises the real Memobot API or the browser-login flow — that needs a real
account and a display, neither of which CI has.
