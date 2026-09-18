# Token savers

Avo includes a deterministic, opt-in token saver for long tool-heavy chats.
It is an internal implementation inspired by terse-output and context
compression patterns; it does not require an external `rtk` or `caveman`
binary.

## Choose a preset

```bash
avo saver list
avo saver show compact
avo saver use full
```

The setting is stored in `~/.config/avo/saver.json` (or the directory selected
by `AVO_CONFIG_DIR`). Existing chats keep their provider wrapper, so restart a
chat after changing the preset.

| Preset | Behavior |
| --- | --- |
| `terse` | Adds the `caveman-terse` concise-answer style guide. |
| `yagni` | Adds a minimal-build style guide for implementation tasks. |
| `compact` | Minifies JSON tool output and removes exact duplicate results. |
| `full` | Combines concise output, compression, verbose-output elision, and pre-trimming. |

Disable the persisted setting with:

```bash
avo saver off
```

For one process or CI invocation, use the environment variable. It takes
precedence over `saver.json`:

```bash
AVO_SAVER=compact avo
```

Unknown preset names fail closed and list the valid choices. The original
messages remain in the event history; compression happens only on the request
sent to the provider. A `SAVER_APPLIED` trace event reports the preset, stages,
estimated tokens before/after, and percentage saved.

## What the numbers mean

The pipeline is deterministic and provider-independent. Its token estimates use
`len(text)//4`, so the measurements are useful for comparing presets but are
not billing data or exact tokenizer counts. See the committed
[benchmark results](https://github.com/Fqih/avo/blob/main/benchmark/savers/RESULTS.md).
