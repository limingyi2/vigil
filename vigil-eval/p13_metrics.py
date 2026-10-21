"""Compare before/after Agent harness records without contacting providers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _ratio(num, den):
    return round(num / den, 4) if den else None


def summarize(records):
    grouped = {}
    for row in records:
        key = (str(row.get('model_id') or 'unknown'), str(row.get('variant') or 'unknown'))
        grouped.setdefault(key, []).append(row)
    result = {}
    for (model, variant), rows in sorted(grouped.items()):
        n = len(rows)
        tp = sum(max(0, int(r.get('confirmed_defects') or 0)) for r in rows)
        fp = sum(max(0, int(r.get('false_positive_defects') or 0)) for r in rows)
        attempted_repairs = sum(bool(r.get('repair_attempted')) for r in rows)
        repair_ok = sum(bool(r.get('repair_attempted') and r.get('repair_verified')) for r in rows)
        recovery_expected = sum(bool(r.get('resume_expected')) for r in rows)
        recovery_ok = sum(bool(r.get('resume_expected') and r.get('resume_succeeded')) for r in rows)
        tokens = [float(r['output_tokens']) for r in rows if isinstance(r.get('output_tokens'), (int, float))]
        elapsed = [float(r['elapsed_seconds']) for r in rows if isinstance(r.get('elapsed_seconds'), (int, float))]
        result.setdefault(model, {})[variant] = {
            'runs': n,
            'completion_rate': _ratio(sum(bool(r.get('success')) for r in rows), n),
            'defect_precision': _ratio(tp, tp + fp),
            'repair_verification_rate': _ratio(repair_ok, attempted_repairs),
            'tool_execution_errors': sum(max(0, int(r.get('tool_errors') or 0)) for r in rows),
            'context_overflows': sum(bool(r.get('context_overflow')) for r in rows),
            'resume_success_rate': _ratio(recovery_ok, recovery_expected),
            'mean_output_tokens': round(sum(tokens) / len(tokens), 2) if tokens else None,
            'mean_elapsed_seconds': round(sum(elapsed) / len(elapsed), 3) if elapsed else None,
            'provider_protocols': sorted({str(r.get('provider_protocol')) for r in rows
                                           if r.get('provider_protocol')}),
        }
    return result


def render(records):
    if not records:
        return ('# P13 Agent Harness Evaluation\n\n'
                'Status: NOT RUN. No isolated before/after model-run records were supplied.\n\n'
                'The fixed task set is `dataset/tasks.jsonl` (24 tasks). This report never '
                'loads production configuration or calls a provider. Supply JSONL rows to '
                '`p13_metrics.py --input` after explicitly configured isolated runs.\n')
    summary = summarize(records)
    lines = ['# P13 Agent Harness Evaluation', '',
             'Input is isolated run evidence; missing token and timing values remain unavailable.', '',
             '| Model | Variant | Runs | Completion | Defect precision | Repair verification | Tool errors | Context overflow | Resume success | Mean output tokens | Mean elapsed (s) | Protocols |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for model, variants in summary.items():
        for variant, m in sorted(variants.items()):
            def value(k):
                v = m[k]
                return '—' if v is None else str(v)
            lines.append('| %s | %s | %d | %s | %s | %s | %d | %d | %s | %s | %s | %s |' % (
                model, variant, m['runs'], value('completion_rate'), value('defect_precision'),
                value('repair_verification_rate'), m['tool_execution_errors'], m['context_overflows'],
                value('resume_success_rate'), value('mean_output_tokens'), value('mean_elapsed_seconds'),
                ', '.join(m['provider_protocols']) or '—'))
    lines += ['', 'Records are grouped by exact model ID and variant. Missing metrics are shown as `—`; '
              'no inferred zero values are added. At least two provider protocols should be included '
              'to claim cross-protocol results.']
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', type=Path)
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    records = [] if not args.input else [json.loads(line) for line in args.input.read_text(encoding='utf-8').splitlines() if line.strip()]
    text = render(records)
    if args.output:
        args.output.write_text(text, encoding='utf-8')
        print(args.output)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
