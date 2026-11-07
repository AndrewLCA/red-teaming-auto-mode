# Auto Mode classifier transcript test harness (Windows)

This tool **observes** live classifier requests and prepares **offline** request bodies for an arbitrary number of YAML contexts. It does not modify live tool authorization or send replay requests. Keep captured prompts and credentials private.

## Setup

```powershell
py -m pip install mitmproxy pyyaml
mitmweb --listen-port 8080 -s .\harness.py
```

Start Claude Code in another terminal with your existing proxy and certificate settings. Trigger a Stage 1 and Stage 2 classification; `captured_templates/` will contain their JSON request templates.

## YAML format

```yaml
name: my_experiment
expected: BLOCK
contexts:
  - name: original
    messages:
      - role: user
        content: "Inspect the test project"
      - role: assistant
        content:
          - type: tool_use
            id: t1
            name: Bash
            input:
              command: "pwd"
  - name: shortened
    messages:
      - role: user
        content: "Inspect the test project"
      - role: assistant
        content:
          - type: tool_use
            id: t2
            name: Bash
            input:
              command: "pwd"
```

The context names and number of contexts are arbitrary. Names must be unique and filename-safe (letters, digits, dots, underscores, hyphens). Tool results are omitted when converting to the observed monitor transcript format. `is_compact_summary` is a YAML annotation only.

## Generate all contexts × both stages

```powershell
py .\compare.py --yaml .\scenario.yaml --stage1 .\captured_templates\YOUR_STAGE1.json --stage2 .\captured_templates\YOUR_STAGE2.json
```

To generate only selected contexts:

```powershell
py .\compare.py --yaml .\scenario.yaml --stage1 .\captured_templates\YOUR_STAGE1.json --stage2 .\captured_templates\YOUR_STAGE2.json --contexts original shortened
```

Outputs are `prepared/<context>_stage1.json`, `prepared/<context>_stage2.json`, and `prepared/manifest.json`. Each request preserves the captured stage-specific model settings and instructions while substituting the serialized `<transcript>` body. **These are request payloads, not results or scores.** No replay is performed.

For clean comparisons, hold the proposed final tool call and relevant user instructions constant when appropriate. Stage 1 escalation and final permission decisions cannot be inferred solely from a severity score.

## Compaction summary provenance

`is_compact_summary: true` accepts only summary text. Do not specify
`tools_used_before_summary`; this field is rejected. For a summary at the
start of a context, declare `previous_contexts: [earlier_context_name]`.
The serializer scans preceding `tool_use` records (including explicitly
referenced ancestry) and generates the tool-name reminder. Context order
alone never implies ancestry. For a known history with no previous tools, specify `previous_contexts: []`
and the tool reminder is omitted. If preceding messages are included within
the same context, those messages also count as known history. When a summary
is the first message and `previous_contexts` is absent, the history is
unknown and serialization fails rather than inventing metadata.

Example:

```yaml
contexts:
  - name: earlier
    messages:
      - role: assistant
        content:
          - type: tool_use
            name: Bash
            input: {command: 'echo test'}
  - name: later
    previous_contexts: [earlier]
    messages:
      - role: user
        is_compact_summary: true
        content: 'Earlier work was completed.'
```

`previous_contexts` supplies provenance for reminder generation; its
messages are not automatically inserted into the serialized compacted
transcript.

### Known empty history example

```yaml
contexts:
  - name: no_prior_tools
    previous_contexts: []
    messages:
      - role: user
        is_compact_summary: true
        content: "We discussed the problem but ran no tools."
```

No `<tools-used-before-summary>` tag is generated for this case. This is a
serialization convention; the exact production formatting for an empty tool
list has not been verified.
