"""Offline acceptance report. Absolute effective-answer P50 must be STRICTLY < 2s."""
import argparse
import json
import math
from pathlib import Path


def summarize(report):
    samples = report['samples']
    values = []
    for sample in samples:
        value = sample.get('effective_answer_ms')
        if sample.get('status') == 'success' and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            values.append(value)
    values.sort()
    def percentile(p):
        return values[max(0, math.ceil(len(values)*p)-1)] if values else None
    return {'total': len(samples), 'valid': len(values), 'failed_or_missing': len(samples)-len(values),
            'failure_rate': (len(samples)-len(values))/len(samples) if samples else 1,
            'p50_ms': percentile(.5), 'p95_ms': percentile(.95)}


def compare(baseline, candidate):
    old, new = summarize(baseline), summarize(candidate)
    keys = ('case_set_sha256', 'network_profile', 'client', 'measurement_method')
    compatible = all(baseline.get('metadata', {}).get(k) == candidate.get('metadata', {}).get(k)
                     and baseline.get('metadata', {}).get(k) for k in keys)
    measured = baseline.get('metadata', {}).get('measurement_method') == 'controlled_input_end_to_remote_pcm'
    enough = old['valid'] >= 30 and new['valid'] >= 30
    under_two = new['p50_ms'] is not None and new['p50_ms'] < 2000
    p95_ok = old['p95_ms'] is not None and new['p95_ms'] is not None and new['p95_ms'] <= old['p95_ms']*1.1
    stable = new['failure_rate'] <= old['failure_rate']
    return {'baseline': old, 'candidate': new, 'same_conditions': bool(compatible),
            'controlled_measurement': measured, 'enough_samples': enough,
            'effective_answer_p50_strictly_under_2000ms': under_two,
            'p95_within_baseline_110percent': p95_ok, 'failure_rate_not_worse': stable,
            'relative_p50_improvement': (1-new['p50_ms']/old['p50_ms']) if old['p50_ms'] and new['p50_ms'] is not None else None,
            'latency_gate_passed': bool(compatible and measured and enough and under_two and p95_ok and stable),
            'business_correctness_accepted': False,
            'note': '240ms is a queue cap, never a latency baseline. This report does not establish business correctness or physical speaker latency.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.baseline.read_text(encoding='utf-8')),
                     json.loads(args.candidate.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Latency gate: '+('PASS' if result['latency_gate_passed'] else 'NOT ACCEPTED'))


if __name__ == '__main__':
    main()
