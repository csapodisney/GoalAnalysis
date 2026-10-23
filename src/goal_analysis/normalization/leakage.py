"""Hard point-in-time assertions for SHADOW model replay inputs.

This checks supplied evidence, never creates a timestamp or availability proof.
Source readers must verify the content-bound receipt before constructing inputs.
Future forecast targets are legal; future knowledge/receipts are not.
"""
from copy import deepcopy

from .canonical import utc
from .training_datasets import sha

VERSION = 'point-in-time-test-v1'
KNOWLEDGE_TIMES = ('observed_at', 'published_at', 'ingested_at', 'available_at')


class LookAheadError(ValueError):
    """A model boundary received unknown, malformed or future availability."""


def require_known_before(times, *, as_of):
    cutoff = utc(as_of)
    if not isinstance(times, dict):
        raise LookAheadError('TIME_EVIDENCE_REQUIRED')
    clean = {}
    for key in KNOWLEDGE_TIMES:
        value = times.get(key)
        if value is None:
            if key in {'observed_at', 'available_at'}:
                raise LookAheadError('UNPROVEN_' + key.upper())
            clean[key] = None
            continue
        try:
            clean[key] = utc(value)
        except (ValueError, TypeError, OverflowError):
            raise LookAheadError('INVALID_' + key.upper()) from None
        if clean[key] > cutoff:
            raise LookAheadError('FUTURE_' + key.upper())
    if times.get('availability_basis') != 'SOURCE_PRESENCE_UPPER_BOUND':
        raise LookAheadError('UNPROVEN_AVAILABILITY_BASIS')
    observed, available = clean['observed_at'], clean['available_at']
    if clean['published_at'] is not None and clean['published_at'] > observed:
        raise LookAheadError('PUBLICATION_AFTER_RECEIPT')
    if clean['ingested_at'] is not None and not observed <= clean['ingested_at'] <= available:
        raise LookAheadError('INVALID_INGESTION_ORDER')
    if observed > available:
        raise LookAheadError('AVAILABILITY_BEFORE_RECEIPT')
    kind = times.get('event_time_kind')
    event = times.get('event_time')
    if event is not None:
        try:
            event = utc(event)
        except (ValueError, TypeError, OverflowError):
            raise LookAheadError('INVALID_EVENT_TIME') from None
        if kind in {'KICKOFF', 'OBSERVATION_TARGET', 'REANALYSIS_TARGET'} and event > observed:
            raise LookAheadError('EVENT_AFTER_RECEIPT')
    return {key: value.isoformat(timespec='microseconds') if value is not None else None
            for key, value in clean.items()}


def model_inputs(envelopes, *, as_of):
    """Validate every envelope before returning any detached model payload.

    Empty inputs are explicit, not proof that a source is fully covered. This
    boundary does not silently drop a bad datum or call a numerical model.
    """
    utc(as_of)
    if not isinstance(envelopes, list):
        raise LookAheadError('ENVELOPES_LIST_REQUIRED')
    identifiers = set()
    for envelope in envelopes:
        if (not isinstance(envelope, dict) or not isinstance(envelope.get('payload'), dict)
                or not isinstance(envelope.get('record_id'), str) or not envelope['record_id']):
            raise LookAheadError('INVALID_MODEL_ENVELOPE')
        identifier = envelope['record_id']
        if identifier in identifiers:
            raise LookAheadError('DUPLICATE_MODEL_ENVELOPE')
        identifiers.add(identifier)
        if envelope.get('payload_sha256') != sha(envelope['payload']):
            raise LookAheadError('MODEL_PAYLOAD_HASH_MISMATCH')
        clean = require_known_before(envelope.get('times'), as_of=as_of)
        for key in KNOWLEDGE_TIMES:
            if key in envelope['payload']:
                value = envelope['payload'][key]
                try:
                    value = utc(value).isoformat(timespec='microseconds') if value is not None else None
                except (ValueError, TypeError, OverflowError):
                    raise LookAheadError('INVALID_PAYLOAD_TIME') from None
                if value != clean[key]:
                    raise LookAheadError('PAYLOAD_TIME_DIFFERS_FROM_PROOF')
    return deepcopy([envelope['payload'] for envelope in envelopes])
