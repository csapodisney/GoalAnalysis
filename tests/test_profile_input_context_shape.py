"""Pure readback shape controls; the context encoder is unchanged."""
from copy import deepcopy

import pytest

from goal_analysis.agents.profile_input import _context, decode_context


def test_context_category_list_round_trips_without_inventing_fixture_map_keys():
    source = {'categories': [{'category': 'players', 'status': 'UNKNOWN',
        'data': {'injuries': ['TESTONLY:player'], 'minutes': None},
        'source_urls': ['TESTONLY:source'], 'reason': 'Lineup unconfirmed'}]}
    original = deepcopy(source)
    projected = _context('players_squad_xi', source)
    projected_before = deepcopy(projected)
    assert projected['categories']['format'] == 'NAMED_COLUMNS_V1'
    assert decode_context(projected, source_shapes={'categories': 'list'}) == source
    assert source == original and projected == projected_before


def test_ambiguous_or_unrecognized_context_shape_is_not_silently_retyped():
    literal = {'categories': [{'fixture_id': 'TESTONLY:f', 'value': {'future_category': False}}],
               'by_fixture': 'TESTONLY:unrecognized future producer value'}
    projected = _context('players_squad_xi', literal)
    with pytest.raises(ValueError, match='AMBIGUOUS_CONTEXT_SHAPE_REQUIRES_SOURCE_HINT'):
        decode_context(projected)
    assert decode_context(projected, source_shapes={'categories': 'list'}) == literal
    mapping = {'categories': {'TESTONLY:f': 0}}
    projected_mapping = _context('players_squad_xi', mapping)
    assert decode_context(projected_mapping, source_shapes={'categories': 'mapping'}) == mapping
