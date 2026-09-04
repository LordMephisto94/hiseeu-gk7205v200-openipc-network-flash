# Pull Request

## What does this change?

Describe the change clearly and concisely.

## Why is this change needed?

Explain the problem being fixed or the improvement being made.

## Type of change

- [ ] Documentation only
- [ ] Bug fix
- [ ] Script/tool improvement
- [ ] Hardware compatibility update
- [ ] Flash-layout or U-Boot change
- [ ] Recovery-related change
- [ ] Other

## Hardware / firmware tested

```text
Brand/model:
XM hardware ID:
SoC:
Sensor:
SPI flash chip:
Flash size:
Stock firmware:
OpenIPC build:
```

## Testing status

- [ ] Offline/static review only
- [ ] Python compile check passed
- [ ] Dry-run validated
- [ ] Tested on real hardware
- [ ] Tested on more than one hardware revision

If Python files changed:

```sh
python -m py_compile scripts/*.py
```

## Flash safety

If this PR changes flash offsets, partition sizes, burn order, U-Boot environment values, or destructive behaviour, explain why and provide evidence.

The known-good tested conversion preserves:

```text
0x7B0000-0x800000  factory/persistent region
```

The initial OpenIPC package intentionally burns:

```text
kernel
rootfs
rootfs_data
env
```

with the environment **last**.

**Does this PR change any flash layout or destructive behaviour?**

- [ ] No
- [ ] Yes — evidence and explanation are included below

## Evidence / validation

Paste relevant logs, hashes, packet details, screenshots, or test results.

## Security and privacy checklist

- [ ] No passwords, tokens, credentials, or private keys are included
- [ ] Packet captures/logs have been checked for unrelated private data
- [ ] Untested behaviour is clearly labelled as untested
- [ ] Hardware compatibility claims are based on actual identification/testing

## Related issue

Closes #

## Additional notes

Anything else reviewers should know.
