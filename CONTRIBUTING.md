---
title: Contributing to copilot-otel
description: Guidelines for contributing issues, pull requests, and feedback to this project.
ms.date: 2026-07-09
ms.topic: reference
---

## How to contribute

Thank you for your interest in contributing. This project welcomes bug reports,
feature requests, and pull requests.

## Reporting issues

Before opening an issue, search the existing issues to avoid duplicates. When
filing a bug report, include:

- Operating system and version
- Python version (`.venv/bin/python --version`)
- Azure CLI / `azd` versions for provisioning or authentication issues
- Steps to reproduce
- Expected behavior vs actual behavior
- Sanitized command output or collector errors from
  `~/Library/Application Support/copilot-otel/logs/collector.stderr.log`

Do not include credentials, environment dumps, raw histories, model responses,
or telemetry payloads containing content.

## Submitting pull requests

1. Fork the repository and create a feature branch from `main`.
2. Make your changes. Keep commits focused and atomic.
3. Run the relevant tests locally:
   ```bash
   .venv/bin/python -m unittest discover -s tests
   az bicep lint --file infra/main.bicep
   ```
   See [the README](README.md#try-it-locally) for dependencies and conditional
   tests. For infrastructure changes, review `azd provision --preview` in a
   disposable environment before any explicitly approved deployment. Tests do
   not require cloud provisioning or model calls.
4. Open a pull request against `main` with a clear description of what changes
   and why.

## Code style

- Bicep: follow standard linting rules (`az bicep lint`).
- YAML: 2-space indent, no trailing whitespace.
- Keep secrets out of all committed files. Use environment variables.

## Code of conduct

This project adheres to the
[Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating,
you agree to uphold this standard.
