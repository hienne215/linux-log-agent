"""Uncalibrated, read-only visible-journal rate rule. Not a statistical diagnosis."""
from datetime import timedelta
import secrets

from agent.diagnostics.journal_policy import UNIT, parse_stamp, stamp, project_message, valid_window

SIGNAL = 'journal_errors'
SOURCE = 'collector.query_journal.journalctl.v1'
MIN_CURRENT = 5
RATE_MULTIPLIER = 2
RULE = 'visible-journal-error-rate-v1'
REFERENCE_LIMIT = 8  # Canonical permits 32; bound the LLM projection more tightly.


def authorized(arguments):
    return (arguments.get('unit')==UNIT and arguments.get('signals')==[SIGNAL]
            and arguments.get('max_findings')==1 and valid_window(arguments, minutes=30)
            and parse_stamp(arguments['until'])-parse_stamp(arguments['since'])==timedelta(minutes=30))


def describe(arguments,baseline_count,current_count):
    start,end = parse_stamp(arguments['since']),parse_stamp(arguments['until'])
    mid = start+(end-start)/2
    return (f'rule={RULE}; baseline_since={stamp(start)}; baseline_until_exclusive={stamp(mid)}; baseline_visible_count={baseline_count}; '
                f'current_since={stamp(mid)}; current_until_exclusive={stamp(end)}; current_visible_count={current_count}; '
                f'threshold=current>={MIN_CURRENT} and current>={RATE_MULTIPLIER}*baseline with baseline>0; '
                'visibility incomplete; uncalibrated rule; no cause or repair established')


def evaluate(arguments, entries):
    """One received journal record is one visible event, not an invented finding.

    Baseline [since,mid), current [mid,until). Equal durations avoid denominator
    differences. A zero baseline is not assigned an infinite rate or confidence.
    More than 100 visible records fails closed in the collector. No deduplication
    silently discards repeated journal records. All event times/IDs are preserved
    in the returned canonical evidence ledger.
    """
    if not authorized(arguments) or type(entries) is not list or len(entries)>100:
        raise ValueError()
    start,end = parse_stamp(arguments['since']),parse_stamp(arguments['until'])
    mid = start+(end-start)/2
    baseline,current = [],[]
    for entry in entries:
        observed=parse_stamp(entry['timestamp'])
        if not start<=observed<end or type(entry['priority']) is not int or not 0<=entry['priority']<=3:
            raise ValueError()
        (baseline if observed<mid else current).append(entry)
    baseline_count,current_count=len(baseline),len(current)
    evaluated=baseline_count>0
    triggered=evaluated and current_count>=MIN_CURRENT and current_count>=RATE_MULTIPLIER*baseline_count
    comparison=describe(arguments,baseline_count,current_count)
    evidence=[]
    for index,entry in enumerate(entries):
        evidence.append(dict(evidence_id='rate-'+secrets.token_hex(16),source=SOURCE,
            observed_at=entry['timestamp'],unit=UNIT,excerpt=(comparison+'\n' if index==0 else '')+project_message(entry['message']),
            redacted=True,truncated=False))
    findings=[]
    if triggered:
        # References include a real baseline event and real current events. Other
        # observed records remain in the full ledger; max 32 refs is canonical.
        baseline_ref=next(e['evidence_id'] for e in evidence if parse_stamp(e['observed_at'])<mid)
        current_refs=[e['evidence_id'] for e in evidence if parse_stamp(e['observed_at'])>=mid]
        findings=[dict(id=RULE,type=SIGNAL,severity='medium',summary=comparison,
            observed_count=current_count,window={k:arguments[k] for k in ('since','until')},
            evidence_refs=[baseline_ref,*current_refs[:REFERENCE_LIMIT-1]],detection_method='rule_based')]
    data=dict(anomalies=findings,analyzed_signals=[SIGNAL] if evaluated else [],
        insufficient_signals=[] if evaluated else [SIGNAL],findings_truncated=False,confidence=None)
    return data,evidence
