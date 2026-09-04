# Security Policy

## Supported versions

This project is currently maintained from the latest version of the `main`
branch.

Security fixes may not be backported to older commits or archived releases.

## Reporting a vulnerability

Please do **not** open a public GitHub issue for vulnerabilities that could:

- allow unauthorized access to a camera
- expose credentials, CheckCodes, session data, or private network information
- cause unintended flash writes or device bricking
- bypass validation or safety checks in the flashing tools
- enable remote command execution beyond the behaviour already documented
- expose a vulnerability in supported XM/Hiseeu firmware that should be
  disclosed responsibly

Instead, please use GitHub's **Private Vulnerability Reporting** feature for
this repository if available.

When reporting, please include:

- affected script or component
- hardware/firmware version
- steps to reproduce
- expected and actual behaviour
- potential impact
- whether the issue was tested on real hardware
- any suggested mitigation or fix

Please redact passwords, private IP information where appropriate, captured
credentials, tokens, and unrelated packet-capture data.

## Firmware research

This repository documents behaviour observed on specific Hiseeu/Xiongmai
hardware and firmware.

A finding affecting a vendor firmware vulnerability may need coordinated
disclosure to the relevant vendor or upstream project before technical
details are published publicly.

## Scope

Normal hardware compatibility problems, failed flashes caused by unsupported
hardware, feature requests, and documentation errors are not security
vulnerabilities and should be reported using normal GitHub issues.

## Disclosure

Please allow reasonable time for investigation and remediation before
publishing vulnerability details.
