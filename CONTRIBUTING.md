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
- `azd` version (`azd version`)
- Docker version (`docker version`)
- Steps to reproduce
- Expected behavior vs actual behavior
- Relevant log output (`docker logs otel-collector`)

## Submitting pull requests

1. Fork the repository and create a feature branch from `main`.
2. Make your changes. Keep commits focused and atomic.
3. Test your changes end-to-end (`azd up` on a fresh environment) where possible.
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
