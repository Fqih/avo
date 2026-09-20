# Token Saver Benchmark Results

These estimates use `len(text)//4`, not a provider tokenizer. They are
useful for deterministic comparisons, not billing or context-window claims.

## Fixture digests

| Transcript | SHA-256 |
| --- | --- |
| small | `b28d5f55c627b96a69a4ee574f5c415e2bc505c4b2e1bea9e4b4ca71270d1db1` |
| medium | `add208f32e20055fea0b7fd51a35b7f53e4af3de641a1ce358ef52d2f35956d1` |
| large | `147f563301b13948d1df74ca51cdd8905c19a9cca96f90dd6e5846193feaf69d` |

## Preset measurements

| Transcript | Preset | Before | After | Saved | Stages |
| --- | --- | ---: | ---: | ---: | --- |
| small | terse | 72 | 72 | 0.0% | none |
| small | yagni | 72 | 72 | 0.0% | none |
| small | compact | 72 | 55 | 23.6% | json_minify |
| small | full | 72 | 55 | 23.6% | json_minify |
| medium | terse | 1082 | 1082 | 0.0% | none |
| medium | yagni | 1082 | 1082 | 0.0% | none |
| medium | compact | 1082 | 799 | 26.2% | json_minify, dedupe_tool_results |
| medium | full | 1082 | 799 | 26.2% | json_minify, dedupe_tool_results |
| large | terse | 6129 | 6129 | 0.0% | none |
| large | yagni | 6129 | 6129 | 0.0% | none |
| large | compact | 6129 | 4530 | 26.1% | json_minify, dedupe_tool_results |
| large | full | 6129 | 4530 | 26.1% | json_minify, dedupe_tool_results |
