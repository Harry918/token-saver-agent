# Contributing

Thank you for helping improve Token Saver Agent.

1. Create a focused branch.
2. Keep provider integrations optional and configuration-driven.
3. Add tests for routing, filesystem, configuration, or escalation behavior you change.
4. Run `.venv/bin/pytest -q` before opening a pull request.
5. Do not commit credentials, model weights, local databases, virtual environments, or personal paths.

Security-sensitive changes should preserve the configured workspace boundary and treat model output as
untrusted input.
