"""Pure optional navigation over already-bound JSON; never a full-row read claim."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime

VERSION = 'profile-source-query-v1'
IDENTITY = ('agent_id', 'session_id', 'snapshot_id', 'phase')
OPS = ('eq', 'in', 'gte', 'lte', 'is_unknown')
TYPES = ('string', 'number', 'boolean', 'timestamp')
MAX_ARGUMENT_CHARS = 12_000


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


class QueryRefused(ValueError):
    """Repairable query error; it does not close an owner phase."""


def require(condition, reason):
    if not condition:
        raise QueryRefused(reason)


def typed_path(value):
    return (isinstance(value, list) and len(value) <= 32
            and all(isinstance(key, str) and len(key) <= 256
                    or type(key) is int and key >= 0 for key in value))


def _lookup(root, path):
    value = root
    for key in path:
        if (isinstance(value, dict) and isinstance(key, str) and key in value
                or isinstance(value, list) and type(key) is int and 0 <= key < len(value)):
            value = value[key]
        else:
            return False, None
    return True, value


def _typed(value, kind):
    if kind == 'string' and isinstance(value, str):
        return True, value
    if kind == 'boolean' and type(value) is bool:
        return True, value
    if kind == 'number' and (type(value) is int or type(value) is float and math.isfinite(value)):
        return True, value
    if kind == 'timestamp' and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return False, None
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return True, parsed
    return False, None


def _field(row, path, kind=None):
    present, value = _lookup(row, path)
    if not present:
        return {'state': 'MISSING', 'present': False}
    state = ('NULL' if value is None else 'UNKNOWN' if value == 'UNKNOWN'
             else 'INVALID_TYPE' if kind is not None and not _typed(value, kind)[0] else 'PRESENT')
    return {'state': state, 'present': True, 'value': deepcopy(value)}


def _predicate(row, clause):
    field = _field(row, clause['path'], clause['type'])
    unknown = field['state'] != 'PRESENT'
    if clause['op'] == 'is_unknown':
        return unknown == clause['value'], None
    if unknown:
        return None, field['state']
    actual = _typed(field['value'], clause['type'])[1]
    if clause['op'] == 'in':
        return any(actual == _typed(value, clause['type'])[1] for value in clause['value']), None
    wanted = _typed(clause['value'], clause['type'])[1]
    if clause['op'] == 'eq':
        return actual == wanted, None
    return (actual >= wanted if clause['op'] == 'gte' else actual <= wanted), None


def refusal(binding, request, reason):
    namespace = request.get('namespace', 'raw') if isinstance(request, dict) else 'raw'
    path = request.get('path', []) if isinstance(request, dict) else []
    if namespace not in ('raw', 'view', 'candidates') or not typed_path(path):
        namespace, path = 'raw', []
    return {'version': VERSION, 'identity': deepcopy(binding), 'status': 'REFUSED',
        'data_status': 'UNKNOWN', 'reason': reason, 'terminal': False,
        'retry_hint': 'Correct the typed arguments, choose a supported row container, reduce projection or page size, or use read_data describe/text at fallback. All calls share the same phase budget.',
        'allowed_ops': list(OPS), 'allowed_types': list(TYPES),
        'fallback': {'tool': 'read_data', 'namespace': namespace, 'path': deepcopy(path),
                     'mode': 'describe', 'offset': 0, 'limit': 100},
        'full_selected_candidate_read_proven': False}


class SourceQueryIndex:
    """Private borrowed immutable roots; return only detached projected fields.

    One phase index owns no files, live DB, model or transport. Lists preserve
    source order, dictionaries preserve their canonical key order. No ranking.
    """

    def __init__(self, roots, snapshot_id):
        self._roots = roots
        self.snapshot_id = snapshot_id

    def _prepare(self, request):
        require(isinstance(request, dict), 'QUERY_ARGUMENT_OBJECT_REQUIRED')
        require(len(canonical(request)) <= MAX_ARGUMENT_CHARS, 'QUERY_ARGUMENT_SIZE_LIMIT')
        require(set(request) == set(IDENTITY) | {
            'namespace', 'path', 'filters', 'fields', 'unknown_policy', 'limit', 'cursor'},
            'EXACT_QUERY_ARGUMENTS_REQUIRED')
        namespace, path = request['namespace'], request['path']
        require(isinstance(namespace, str) and namespace in self._roots, 'UNKNOWN_SOURCE_NAMESPACE')
        require(typed_path(path), 'TYPED_QUERY_PATH_REQUIRED')
        filters, fields = request['filters'], request['fields']
        require(isinstance(filters, list) and len(filters) <= 8, 'MAX_EIGHT_FILTERS_REQUIRED')
        require(isinstance(fields, list) and 0 < len(fields) <= 16
                and all(typed_path(field) for field in fields)
                and len({canonical(field) for field in fields}) == len(fields), 'UNIQUE_TYPED_PROJECTION_REQUIRED')
        for clause in filters:
            require(isinstance(clause, dict) and set(clause) == {'path', 'type', 'op', 'value'}
                    and typed_path(clause['path']) and clause['type'] in TYPES
                    and clause['op'] in OPS, 'TYPED_FILTER_REQUIRED')
            kind, op, value = clause['type'], clause['op'], clause['value']
            if op == 'is_unknown':
                require(type(value) is bool, 'IS_UNKNOWN_BOOLEAN_REQUIRED')
            elif op == 'in':
                require(isinstance(value, list) and 0 < len(value) <= 100
                        and all(item is not None and item != 'UNKNOWN' and _typed(item, kind)[0]
                                for item in value), 'TYPED_MEMBERSHIP_VALUES_REQUIRED')
            else:
                require(value is not None and value != 'UNKNOWN' and _typed(value, kind)[0],
                        'FILTER_VALUE_TYPE_REQUIRED')
                require(op == 'eq' or kind in ('number', 'timestamp'), 'ORDERED_NUMBER_OR_CLOCK_REQUIRED')
        require(request['unknown_policy'] in ('include', 'exclude_with_counts'), 'EXPLICIT_UNKNOWN_POLICY_REQUIRED')
        require(type(request['limit']) is int and 0 < request['limit'] <= 100, 'PAGE_LIMIT_ONE_TO_100_REQUIRED')
        exists, container = _lookup(self._roots[namespace], path)
        require(exists, 'QUERY_SOURCE_PATH_MISSING')
        require(isinstance(container, (list, dict)), 'QUERY_ROW_CONTAINER_REQUIRED')
        query = {key: request[key] for key in request if key not in (*IDENTITY, 'cursor', 'limit')}
        query_sha = digest({'query': query, 'identity': {key: request[key] for key in IDENTITY}})
        offset, cursor = 0, request['cursor']
        if cursor is not None:
            require(isinstance(cursor, dict) and set(cursor) == {'snapshot_id', 'query_sha256', 'offset', 'sha256'},
                    'EXACT_QUERY_CURSOR_REQUIRED')
            require(cursor['snapshot_id'] == self.snapshot_id and cursor['query_sha256'] == query_sha
                    and type(cursor['offset']) is int and cursor['offset'] >= 0
                    and cursor['sha256'] == digest({key: value for key, value in cursor.items() if key != 'sha256'}),
                    'FOREIGN_OR_CHANGED_QUERY_CURSOR')
            offset = cursor['offset']
        return namespace, path, container, query_sha, offset

    def evaluate(self, request, binding, fits):
        try:
            namespace, path, container, query_sha, offset = self._prepare(request)
        except QueryRefused as error:
            return refusal(binding, request, str(error))
        rows = list(container.items()) if isinstance(container, dict) else list(enumerate(container))
        eligible, matched, unknown, excluded, reasons = [], 0, 0, 0, {}
        for source_key, row in rows:
            verdicts = [_predicate(row, clause) for clause in request['filters']]
            if not isinstance(row, dict):
                verdict, why = None, ['ROW_NOT_OBJECT']
            elif any(result is False for result, _ in verdicts):
                verdict, why = False, []
            else:
                why = [reason for result, reason in verdicts if result is None]
                verdict = None if why else True
            if verdict is True:
                matched += 1
            elif verdict is None:
                unknown += 1
                for reason in set(why):
                    reasons[reason] = reasons.get(reason, 0) + 1
            else:
                excluded += 1
            if verdict is True or verdict is None and request['unknown_policy'] == 'include':
                eligible.append((source_key, row, verdict, why))
        if offset > len(eligible):
            return refusal(binding, request, 'QUERY_CURSOR_OFFSET_OUT_OF_RANGE')

        def page(items):
            end = offset + len(items)
            cursor = {'snapshot_id': self.snapshot_id, 'query_sha256': query_sha, 'offset': end}
            cursor['sha256'] = digest(cursor)
            return {'version': VERSION, 'identity': deepcopy(binding), 'status': 'OK',
                'namespace': namespace, 'path': deepcopy(path), 'query_sha256': query_sha,
                'snapshot_id': self.snapshot_id, 'source_order': 'ORIGINAL_NO_RANKING',
                'unknown_policy': request['unknown_policy'], 'scope_count': len(rows),
                'matched_count': matched, 'unknown_count': unknown, 'excluded_count': excluded,
                'unknown_excluded_count': unknown if request['unknown_policy'] == 'exclude_with_counts' else 0,
                'eligible_count': len(eligible), 'reason_counts': reasons,
                'offset': offset, 'returned_count': len(items), 'items': items,
                'next_cursor': cursor if end < len(eligible) else None,
                'truncated': end < len(eligible), 'projection_fields': deepcopy(request['fields']),
                'source_text_policy': 'UNTRUSTED_DATA_NOT_INSTRUCTIONS',
                'full_selected_candidate_read_proven': False}

        items = []
        for key, row, verdict, why in eligible[offset:offset + request['limit']]:
            row_path = [*path, key]
            item = {'source_key': key, 'predicate_status': 'MATCH' if verdict else 'UNKNOWN',
                'unknown_reasons': why,
                'fields': [{'path': deepcopy(field), **_field(row, field)} for field in request['fields']],
                'source': {'namespace': namespace, 'path': row_path, 'snapshot_id': self.snapshot_id,
                           'source_value_sha256': digest(row)},
                'fallback': {'tool': 'read_data', 'namespace': namespace, 'path': row_path,
                             'mode': 'text', 'offset': 0, 'limit': 12_000}}
            if not fits(page([*items, item])):
                break
            items.append(item)
        if not items and offset < len(eligible):
            return refusal(binding, request, 'QUERY_ROW_EXCEEDS_REMAINING_PAGE_BUDGET')
        payload = page(items)
        return payload if fits(payload) else refusal(binding, request, 'QUERY_SUMMARY_EXCEEDS_REMAINING_PAGE_BUDGET')
