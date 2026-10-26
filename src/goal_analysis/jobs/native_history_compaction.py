"""Lossless SHADOW input compaction after source validation, before sample selection.

Only exact normalized truth is grouped. All original source envelopes can be
reconstructed; a representative keeps its own clocks and receipt. This never
creates availability or removes a differing/unknown truth version.
"""
from copy import deepcopy

from goal_analysis.normalization.training_datasets import sha

PROVENANCE = ('observed_at', 'available_at', 'ingested_at', 'published_at', 'source_id')


class HistoryIdentityConflict(RuntimeError):
    """Hard failure: do not downgrade an inconsistent identity to a skipped row."""


def _order(envelope):
    payload = envelope['payload']
    return payload['observed_at'], payload['source_id'], envelope['record_id']


class HistoryCompactor:
    """Bounded by the caller's source/page and distinct-truth limits.

    The global identity map holds only canonical SHA256 values, not a second
    full copy of every envelope. Alias provenance remains complete separately.
    """

    def __init__(self):
        self._groups = {}
        self._identities = {}

    def add(self, envelope):
        identifier, payload = envelope['record_id'], envelope['payload']
        identity_hash = sha(envelope)
        if identifier in self._identities and self._identities[identifier] != identity_hash:
            raise HistoryIdentityConflict('CONFLICTING_RAW_HISTORY_IDENTITY')
        if sha(payload) != envelope['payload_sha256']:
            raise ValueError('HISTORY_PAYLOAD_HASH_MISMATCH')
        provenance = {k: deepcopy(payload[k]) for k in PROVENANCE}
        if any(envelope['times'][k] != provenance[k] for k in PROVENANCE[:-1]):
            raise ValueError('HISTORY_CLOCK_BINDING_MISMATCH')
        truth = {k: deepcopy(v) for k, v in payload.items() if k not in PROVENANCE}
        key = sha(truth)
        group = self._groups.setdefault(key, {'truth': truth, 'chosen': None, 'aliases': {}})
        if group['truth'] != truth:
            raise HistoryIdentityConflict('HISTORY_TRUTH_HASH_COLLISION')
        alias = {k: deepcopy(v) for k, v in envelope.items() if k != 'payload'}
        alias['payload_provenance'] = provenance
        alias['envelope_sha256'] = identity_hash
        group['aliases'][identifier] = alias
        self._identities[identifier] = identity_hash
        if group['chosen'] is None or _order(envelope) < _order(group['chosen']):
            group['chosen'] = deepcopy(envelope)

    def __len__(self):
        return len(self._groups)

    def envelopes(self):
        return sorted((deepcopy(g['chosen']) for g in self._groups.values()),
                      key=lambda e: e['record_id'])

    def aliases(self):
        output = []
        for key, group in sorted(self._groups.items()):
            aliases = sorted(group['aliases'].values(), key=lambda a: a['record_id'])
            output.append({'truth_sha256': key, 'truth': group['truth'],
                'chosen_record_id': group['chosen']['record_id'], 'aliases': aliases})
        return deepcopy(output)


def reconstruct(groups):
    """Verify and restore every complete original envelope, without clock grafts."""
    output, identities = [], {}
    for group in groups:
        if sha(group['truth']) != group['truth_sha256']:
            raise ValueError('HISTORY_TRUTH_HASH_MISMATCH')
        for alias in group['aliases']:
            payload = {**deepcopy(group['truth']), **deepcopy(alias['payload_provenance'])}
            if sha(payload) != alias['payload_sha256']:
                raise ValueError('HISTORY_PAYLOAD_HASH_MISMATCH')
            envelope = {**{k: deepcopy(v) for k, v in alias.items()
                          if k not in ('payload_provenance', 'envelope_sha256')},
                        'payload': payload}
            identity_hash = sha(envelope)
            if identity_hash != alias['envelope_sha256']:
                raise ValueError('HISTORY_ENVELOPE_HASH_MISMATCH')
            if any(envelope['times'][k] != payload[k] for k in PROVENANCE[:-1]):
                raise ValueError('HISTORY_CLOCK_BINDING_MISMATCH')
            identifier = envelope['record_id']
            if identifier in identities and identities[identifier] != identity_hash:
                raise HistoryIdentityConflict('CONFLICTING_RAW_HISTORY_IDENTITY')
            identities[identifier] = identity_hash
            output.append(envelope)
    return sorted(output, key=lambda e: e['record_id'])
