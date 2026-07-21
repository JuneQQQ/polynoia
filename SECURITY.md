# Security Policy

## Supported versions

Only the latest published release of Polynoia receives security updates. Older
releases are not supported; reproduce the issue against the latest release
when possible.

## Report a vulnerability

Do not open a public issue for a suspected vulnerability. Submit it privately
through [GitHub's private vulnerability reporting form](https://github.com/JuneQQQ/polynoia/security/advisories/new).

Include enough information for the maintainers to understand and reproduce the
issue:

- the affected Polynoia version or versions, relevant commits, and affected platform;
- a clear description of the impact and attack scenario;
- step-by-step reproduction instructions or a minimal proof of concept;
- relevant logs, configuration, or screenshots with secrets removed; and
- any known mitigations or suggested fixes.

Please avoid public disclosure before the maintainers can coordinate a fix and
an appropriate disclosure. The maintainers will use the private advisory to
review the report and coordinate next steps with you.

## Development and agent safety

Git worktrees provide Git collaboration isolation; they are not an
operating-system security sandbox. Shell-capable agents run with the authority
of the local user and may access resources available to that user. Use trusted
agent configurations and protect credentials and host data.

The development API must not be exposed to an untrusted network. Restrict its
network reach with trusted host and firewall settings, and use production-grade
authentication and network controls for any real deployment.
