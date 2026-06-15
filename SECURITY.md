# Security

## Reporting

Please report vulnerabilities privately through GitHub Security Advisories rather than a public issue.

## Credential handling

Token Saver Agent does not copy API keys or Codex credentials into its repository or configuration
file. Provider SDKs discover credentials through their standard authentication mechanisms. Never add
credential files, access tokens, task-history databases, or model weights to a commit.

## Workspace boundary

The first-run configuration records the project roots the agent may access. Local model file operations
are additionally restricted to relative, non-hidden paths inside the selected workspace. Treat every
model-generated edit as untrusted until verification passes.
