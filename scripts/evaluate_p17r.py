"""Reproducible offline before/after retrieval evaluation (stdlib + existing storage).

Run in separate processes with --module-root pointing at an untouched baseline
archive or the current brain directory. No real model or embedding API is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--module-root', required=True)
    parser.add_argument('--phase', choices=['before', 'after'], required=True)
    parser.add_argument('--mode', choices=['bm25', 'hybrid'], required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, default=30)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.module_root).resolve()))
    from storage import db, search, unified, vector
    import paths
    fixture_path = Path(__file__).resolve().parents[1] / 'tests/fixtures/p17r_retrieval.json'
    raw = fixture_path.read_bytes()
    fixture = json.loads(raw)
    cases = list(fixture['cases'])
    chain = fixture['chain']
    for turn in range(chain['turns']):
        cases.append(dict(id='chain-%02d' % turn, query=chain['query'],
            history=[chain['initial']] + [chain['query']] * turn,
            entities=chain['entities'], gold=chain['gold'], status='resolved'))

    class OfflineEmbedding:
        def available(self):
            return True, ''

        def tag(self):
            return 'p17r-fixture-sha256-32'

        def embed(self, texts):
            vectors = []
            for text in texts:
                values = [0.] * 32
                for token in re.findall(r'[\u4e00-\u9fff]|[a-z0-9_.]+', text.lower()):
                    values[int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], 'big') % 32] += 1
                vectors.append(values)
            return vectors, self.tag()

    with tempfile.TemporaryDirectory(prefix='vigil-p17r-eval-') as temp:
        paths.DATA_DIR = str(Path(temp) / 'data')
        paths.CONFIG_PATH = str(Path(temp) / 'config.json')
        Path(paths.CONFIG_PATH).write_text(json.dumps({'retrieval': {'semantic_enabled': args.mode == 'hybrid'}}))
        db.set_dir(str(Path(temp) / 'db'))
        try:
            scope = fixture['scope']
            for item in fixture['documents']:
                doc_scope = {k: scope[k] for k in ('owner', 'src', 'persona', 'chan')}
                doc_scope.update({k: item[k] for k in ('owner', 'persona', 'workspace_id') if k in item})
                search.upsert_doc(item['id'], item.get('kind', 'kb'), item['text'],
                                  meta=item.get('meta'), **doc_scope)
            backend = OfflineEmbedding() if args.mode == 'hybrid' else vector.EmbeddingBackend(mode='none')
            if args.mode == 'hybrid':
                search.index_embeddings([x['id'] for x in fixture['documents']], backend=backend)
            names = sorted(set(['张三', '李四', '王伟', '王伟强', '陈明', '赵六', 'config.py']))
            records, latencies = [], []
            for case in cases:
                kwargs = dict(scope, top_k=10, budget_chars=20000, backend=backend)
                if args.phase == 'after':
                    from storage import query_resolver as qr
                    kwargs['query_context'] = qr.QueryContext('eval-session', qr.scope_key(scope), tuple(case['history']))
                # Cold call is recorded separately; p50/p95 are warm request timings.
                start = time.perf_counter()
                rows, info = unified.retrieve(case['query'], **kwargs)
                cold_ms = (time.perf_counter() - start) * 1000
                for _ in range(args.repeats):
                    start = time.perf_counter()
                    unified.retrieve(case['query'], **kwargs)
                    latencies.append((time.perf_counter() - start) * 1000)
                resolution = info.get('resolution') or {}
                predicted = ([e['name'] for e in resolution.get('anchor_entities', [])]
                             if args.phase == 'after' else [n for n in names if n in case['query']])
                ids = [r.doc_id for r in rows]
                gold = set(case['gold'])
                # This is evidence safety, not a claim about untested LLM answers.
                # A returned fragment can be a valid trimmed subset of a mixed doc.
                doc_entities = {d['id']: d['entity'] for d in fixture['documents']}
                expected_identities = {doc_entities[did] for did in gold}
                wrong = any(doc_entities[r.doc_id] not in expected_identities for r in rows)
                if case['id'] == 'same-name':
                    wrong = bool(rows)
                records.append(dict(id=case['id'], original_query=case['query'],
                    resolved_query=resolution.get('resolved_query', case['query']),
                    predicted_entities=predicted, expected_entities=case['entities'],
                    expected_status=case['status'], actual_status=resolution.get('resolution_status', 'unreported'),
                    gold=case['gold'], retrieved=ids, wrong_entity_evidence=wrong,
                    recall5=len(gold.intersection(ids[:5])) / len(gold) if gold else None,
                    rr10=next((1 / (i+1) for i, did in enumerate(ids[:10]) if did in gold), 0.) if gold else None,
                    cold_ms=cold_ms))
            evaluable = [r for r in records if r['expected_entities']]
            # Parsing accuracy is query entity identity, NOT document hit accuracy.
            entity_accuracy = sum(r['predicted_entities'] == r['expected_entities'] for r in evaluable) / len(evaluable)
            chain_records = [r for r in records if r['id'].startswith('chain-')]
            sorted_ms = sorted(latencies)
            output = dict(phase=args.phase, mode=args.mode, fixture_sha256=hashlib.sha256(raw).hexdigest(),
                cases=len(records), repeats=args.repeats, embedding='deterministic offline SHA-256 features (not production embeddings)',
                metrics=dict(entity_accuracy=entity_accuracy,
                    recall_at_5=statistics.mean(r['recall5'] for r in records if r['recall5'] is not None),
                    mrr_at_10=statistics.mean(r['rr10'] for r in records if r['rr10'] is not None),
                    wrong_entity_answer_rate=None,
                    wrong_entity_evidence_rate=sum(r['wrong_entity_evidence'] for r in records)/len(records),
                    multi_turn_drift_rate=sum(r['wrong_entity_evidence'] for r in chain_records)/len(chain_records),
                    chain_abstention_rate=sum(not r['retrieved'] for r in chain_records)/len(chain_records),
                    p50_ms=statistics.median(latencies), p95_ms=sorted_ms[max(0, int(len(sorted_ms)*.95)-1)]),
                records=records)
            Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: v for k, v in output.items() if k != 'records'}, ensure_ascii=False))
        finally:
            db.set_dir(None)


if __name__ == '__main__':
    main()
