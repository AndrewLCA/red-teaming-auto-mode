
#!/usr/bin/env python3
"""Convert simulation multi-context YAML into Arena replay JSONL records."""
import argparse
import json
from pathlib import Path
import yaml


def convert_message(message):
    role = message['role']
    content = message.get('content', '')
    turn = {'role': role}

    if isinstance(content, str):
        turn['text'] = content
    elif isinstance(content, list):
        texts, calls, results = [], [], []
        for block in content:
            kind = block.get('type')
            if kind == 'text':
                texts.append(block.get('text', ''))
            elif kind == 'tool_use':
                calls.append({
                    'id': block['id'],
                    'name': block['name'],
                    'input': block.get('input', {})
                })
            elif kind == 'tool_result':
                results.append({
                    'tool_use_id': block['tool_use_id'],
                    'content': block.get('content', '')
                })
            else:
                raise ValueError(f'Unsupported content block: {kind}')
        if texts:
            turn['text'] = '\n'.join(texts)
        if calls:
            turn['tool_calls'] = calls
        if results:
            turn['tool_results'] = results
    else:
        raise ValueError(f'Unsupported content type: {type(content)}')

    if message.get('is_compact_summary'):
        turn['is_compact_summary'] = True
    return turn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=Path)
    ap.add_argument(
        '--out',
        type=Path,
        default=Path('compaction_transcripts.jsonl')
    )
    args = ap.parse_args()

    doc = yaml.safe_load(args.source.read_text(encoding='utf-8'))
    contexts = {
        c['name']: c['messages']
        for c in doc['contexts']
    }

    if not {'before_compaction', 'after_compaction'} <= contexts.keys():
        raise ValueError(
            'Expected before_compaction and after_compaction contexts'
        )

    before = [
        convert_message(m)
        for m in contexts['before_compaction']
    ]
    after = [
        convert_message(m)
        for m in contexts['after_compaction']
    ]

    if not after or not after[-1].get('tool_calls'):
        raise ValueError('Expected final assistant tool call')

    # Full-history counterfactual: retain original history
    # and subsequent actions, but omit the compact summary.
    post = [
        t for t in after
        if not t.get('is_compact_summary')
    ]

    rows = [
        {
            'metadata': {
                'source': doc.get('name', 'compaction') + '_full',
                'expected': doc.get('expected')
            },
            'turns': before + post
        },
        {
            'metadata': {
                'source': doc.get('name', 'compaction') + '_compacted',
                'expected': doc.get('expected')
            },
            'turns': after
        }
    ]

    args.out.write_text(
        ''.join(
            json.dumps(r, ensure_ascii=False) + '\n'
            for r in rows
        ),
        encoding='utf-8'
    )

    print(f'Wrote {len(rows)} transcripts to {args.out}')
    for row in rows:
        print(
            row['metadata']['source'],
            len(row['turns']),
            'turns; final call:',
            row['turns'][-1]['tool_calls'][0]['input']
        )


if __name__ == '__main__':
    main()
