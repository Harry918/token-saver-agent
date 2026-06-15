# Token Saver Agent

Token Saver Agent is a local-first orchestration layer for coding and general agent tasks. It sends
bounded work to a GGUF model served by llama.cpp, compresses context with Headroom, and escalates
complex or failed work to Codex.

> Alpha software: review model-generated edits and use isolated workspaces until you trust your setup.

## How it works

```text
CLI -> policy router -> Headroom -> local llama.cpp model
                     -> verifier -> retry -> Codex escalation
                     -> local SQLite history
```

- Low-complexity work runs locally without cloud-model tokens.
- Local coding responses are validated and applied as structured file operations.
- Verification commands determine whether coding work passes.
- Failed local attempts are summarized and escalated to Codex.
- High-risk, research, or complex tasks can route directly to Codex.
- Allowed project roots are configured on first use.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) or pip
- [llama.cpp](https://github.com/ggml-org/llama.cpp) for local inference
- A Codex login or API setup if cloud escalation is enabled

The default local model is
[`yuxinlu1/gemma-4-12B-coder-fable5-composer2.5-v1-GGUF`](https://huggingface.co/yuxinlu1/gemma-4-12B-coder-fable5-composer2.5-v1-GGUF).

## Install

Clone the repository and install it into a virtual environment:

```bash
git clone https://github.com/Harry918/token-saver-agent.git
cd token-saver-agent

uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[all,dev]'
```

Activate the environment if you want to call `token-saver` without the `.venv/bin/` prefix:

```bash
source .venv/bin/activate
```

## First-run setup

Initialize the configuration:

```bash
token-saver init
```

The command asks for one or more absolute folders containing projects the agent may access. It writes:

```text
~/.config/token-saver/config.toml
```

You can configure roots without prompts:

```bash
token-saver init \
  --project-root "$HOME/Projects" \
  --project-root "$HOME/Work"
```

View the active configuration without displaying credentials:

```bash
token-saver config
```

See [`config.example.toml`](config.example.toml) for every setting. Set `TOKEN_SAVER_CONFIG` to use a
different configuration file. Environment variables in [`.env.example`](.env.example) override model
and runtime settings.

## Start local inference

Install llama.cpp using the method appropriate for your operating system. On macOS with Homebrew:

```bash
brew install llama.cpp
```

Start the default model server:

```bash
llama-server \
  -hf yuxinlu1/gemma-4-12B-coder-fable5-composer2.5-v1-GGUF:Q4_K_M \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 16384
```

The first launch downloads several gigabytes of model weights. Adjust the quantization and context size
for your hardware. Confirm readiness with:

```bash
curl http://127.0.0.1:8080/health
```

## Use

Preview routing without calling a model:

```bash
token-saver route "Explain this function" --workspace "$HOME/Projects/example"
```

Run a general task:

```bash
token-saver run "Draft a concise release note"
```

Run a local coding task with executable verification:

```bash
token-saver run "Fix the failing parser tests" \
  --type coding \
  --workspace "$HOME/Projects/example" \
  --files 2 \
  --verify "python -m pytest -q"
```

Force direct frontier routing:

```bash
token-saver run "Review the authentication implementation" \
  --type coding \
  --risk high \
  --workspace "$HOME/Projects/example"
```

Inspect local run history:

```bash
token-saver history
```

By default, history is stored outside the repository at
`~/.local/state/token-saver/tasks.sqlite3`. It may contain prompts, paths, and model responses. Do not
publish it.

## Routing

The router scores subsystem count, expected files, ambiguity, tool depth, novelty, and risk:

| Score or condition | Route |
| --- | --- |
| 0-8 | Local model, then verification and possible escalation |
| 9-12 | Smaller Codex model |
| Above 12 | Frontier Codex model |
| Research or high-risk terms | Frontier Codex model |

Routing policy lives in `src/token_saver/router.py` and is intentionally deterministic.

## Local editing safety

The local model does not receive unrestricted shell access. For coding tasks it returns structured
`write` or `delete` operations. The host rejects:

- Absolute paths and directory traversal
- Hidden and protected directories such as `.git` and `.venv`
- Symlink targets or symlinked parent directories
- More than ten operations in one response
- Edit payloads larger than 250 KB
- Workspaces outside configured project roots

Verification commands are supplied by the user and run through the local shell. Only use commands you
understand and trust.

## Codex authentication

The repository contains no Codex credentials. The optional `openai-codex` SDK uses its normal external
authentication mechanism. Never commit credential files, API keys, model weights, or run-history
databases.

## Telegram bridge

The optional Telegram bridge uses private long polling, so it does not require a public web server.
Create a bot with Telegram's `@BotFather`, then keep the token outside this repository. A private token
file avoids placing the credential in shell history:

```bash
mkdir -p "$HOME/.config/token-saver"
printf '%s' "your-bot-token" > "$HOME/.config/token-saver/telegram-token"
chmod 600 "$HOME/.config/token-saver/telegram-token"
export TELEGRAM_BOT_TOKEN_FILE="$HOME/.config/token-saver/telegram-token"
```

For containers or service managers, `TELEGRAM_BOT_TOKEN` is also supported.

Add a private allowlist and project aliases to `~/.config/token-saver/config.toml`:

```toml
[telegram]
allowed_user_ids = [123456789]
poll_timeout = 30
projects = { agent = "/absolute/path/to/token-saver-agent" }
verify_commands = { agent = "python -m pytest -q" }
```

If you do not know your numeric Telegram user ID, start the bot with an empty allowlist and send
`/whoami`. The bot will reveal only the sender's ID and deny task execution. Add that ID to the config,
then restart the bot.

```bash
token-saver telegram
```

Supported private-chat commands:

- `/projects` lists configured aliases without exposing filesystem paths.
- `/use <alias>` selects a project.
- `/ask <task>` runs a general task.
- `/code <task>` runs a coding task using the configured verification command.

Telegram users cannot provide arbitrary workspace paths or shell verification commands. Group chats are
rejected, and only explicitly allowed numeric user IDs may execute tasks.

### Always-on Docker deployment

Docker Compose can keep the Telegram bridge running and restart it after crashes or Docker Desktop
restarts. llama.cpp continues running on the host and is reached through `host.docker.internal`.

Copy the example deployment environment and replace each path with an absolute host path:

```bash
cp docker.env.example .env.docker
```

Example values on Linux or macOS:

```dotenv
PROJECTS_ROOT=/absolute/path/to/projects
TOKEN_SAVER_CONFIG_FILE=/absolute/path/to/config.toml
TOKEN_SAVER_STATE_DIR=/absolute/path/to/state-directory
TELEGRAM_TOKEN_FILE=/absolute/path/to/telegram-token
CODEX_HOME_DIR=/absolute/path/to/.codex
HOST_UID=1000
HOST_GID=1000
```

The paths inside `config.toml` must match the mounted host paths exactly. For example, if
`PROJECTS_ROOT=/home/alice/projects`, Telegram project aliases must also start with
`/home/alice/projects`.

Start the service:

```bash
docker compose --env-file .env.docker up -d --build
```

Inspect status and logs:

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f telegram-agent
```

Apply new configuration or code:

```bash
docker compose --env-file .env.docker up -d --build --force-recreate
```

Stop the service:

```bash
docker compose --env-file .env.docker down
```

The container mounts the configured project root and Codex home directory because agents need access to
projects and Codex authentication. Do not run untrusted prompts or expose the bot beyond the private
allowlist. Project-specific verification tools may require additional packages in the `Dockerfile`.

## Development

```bash
uv pip install --python .venv/bin/python -e '.[all,dev]'
.venv/bin/pytest -q
```

See [`SECURITY.md`](SECURITY.md) before reporting security issues.
