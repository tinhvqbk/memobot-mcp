# memobot-mcp

Personal, unofficial MCP server for [Memobot](https://app.memobot.io), a voice-recording/
transcription app.

There is no public Memobot developer API. Everything here was reverse-engineered from
`app.memobot.io`'s own network traffic against a real logged-in session. Endpoints, field
names, and the token scheme may change or break without notice.

## Auth model

No password ever touches this codebase, and nothing is downloaded automatically. The
first time a tool needs a token and none is cached, and a display is available, it pops a
real browser at Memobot's actual login page (`app.memobot.io`) and waits for you to sign
in by hand — it just sniffs the JSON response of the login request the app itself fires.
It drives whatever Chromium-family browser is already installed (Chrome, Edge, or Cốc
Cốc) rather than downloading anything; if none of those are found, it falls back to a
local login page instead of downloading (see below). Set
`MEMOBOT_MCP_ALLOW_BROWSER_DOWNLOAD=1` if you'd rather it download and use Playwright's
own bundled Chromium than fall back — useful if you want the real-domain experience but
don't have Chrome/Edge/Cốc Cốc installed.

That resulting token is cached to `~/.config/memobot-mcp/credentials.json` (0600
permissions) and reused for its ~1 year validity. When it expires (or an API call gets a
401), the server first tries a silent refresh via the cached refresh_token (`POST
/authen/api/v1/auth/token`) — the interactive step only reappears if there's no cache at
all or the refresh_token itself has been revoked (e.g. after a password change).

On a machine with no display (a server reached over plain SSH), or if the real-domain
browser attempt fails or is skipped, it instead starts a throwaway local HTTP server and
opens (or, with no display, just prints the URL for you to open) a login page — not the
real Memobot domain, but it submits straight to Memobot's real login endpoint from your
browser (its CORS is wide open, so this works) and posts just the resulting token back to
the local server; the password still never touches this codebase. Works with literally any
browser (Safari, Firefox, whatever), anywhere — using SSH port-forwarding if the server
isn't local. Set `MEMOBOT_MCP_HEADLESS=1` to force this path even on a machine that does
have a display.

That local-callback path never blocks a tool call for long: it polls briefly (a few
seconds) and, if nobody's logged in yet, fails fast with the login URL *in the tool
error's own message* — not just printed to stderr, since some MCP clients (agentic ones
especially, e.g. OpenClaw) don't show a server's stderr and/or kill tool calls that block
too long, which otherwise means the link never actually reaches the user. The server keeps
listening in the background, so simply retrying the same tool call after logging in picks
up the already-captured token immediately instead of starting over.

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
- `check_for_updates` — whether a newer memobot-mcp version exists on GitHub

## Updates

There's no package registry here — `uvx --from git+...` re-resolves and rebuilds against
the latest commit on `main` on every fresh process launch (verified: `uv` queries GitHub's
API for the current HEAD commit each time). So restarting/reconnecting your MCP client
already gets you the latest code for free; there's no separate "update" step. The gap is
just *noticing* that's worth doing, since a long-running session keeps its already-started
process alive even after `main` moves on. The server checks once in the background at
startup and prints a note to stderr if it's outdated, and `check_for_updates` (tool or
`/mcp__memobot__check_for_updates` prompt) does the same check on demand.

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
uv run pytest -q       # unit tests (mocked Memobot API — no external network/browser calls)
```

CI runs lint, format-check, and tests on every push/PR to `main`. There's intentionally no
CI job that exercises the real Memobot API — that needs a real account. The login flow
itself (its HTTP server, page, and token capture, with/without auto-opening a browser) is
tested for real, just against a fake token instead of a real Memobot login.
