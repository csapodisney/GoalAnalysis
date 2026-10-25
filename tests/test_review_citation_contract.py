"""Source citation boundaries at the actual API and CLI request interfaces."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_astra_review import KEY, SOURCE, Transport, candidate, researched, response, review
from test_codex_review import Cli

from goal_analysis.agents.astra_review import AstraReviewer, _schema
from goal_analysis.agents.codex_review import CodexReviewer


def citation_fields(schema):
    fields = schema['properties']['reviews']['items']['properties']
    return (fields['source_urls'],
            fields['contrarian_rationale']['anyOf'][1]['properties']['cause_source_urls'],
            fields['veto_override']['anyOf'][1]['properties']['source_urls'])


def test_all_citation_channels_are_closed_to_the_exact_allowlist():
    urls = [SOURCE, SOURCE + '?edition=2', SOURCE]
    before = copy.deepcopy(urls)
    schema = _schema(['candidate-1'], contrarian=True, veto_overrides=True,
                     allowed_source_urls=urls)
    for field in citation_fields(schema):
        assert field['items']['enum'] == sorted(set(urls))
        assert SOURCE + '/' not in field['items']['enum']
    assert urls == before
    assert 'enum' not in schema['properties']['reviews']['items']['properties']['risk_notes']['items']


def test_no_sources_allows_only_empty_citation_arrays_in_all_channels():
    schema = _schema(['candidate-1'], contrarian=True, veto_overrides=True,
                     allowed_source_urls=[])
    assert all(field['maxItems'] == 0 for field in citation_fields(schema))


def test_api_request_uses_verified_sources_and_keeps_existing_review_semantics():
    transport = Transport(researched(), response({'reviews': [review(urls=[SOURCE])]}))
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], '2026-09-21')
    assert result['status'] == 'COMPLETE' and result['reviews'] == [review(urls=[SOURCE])]
    fields = transport.calls[1][2]['text']['format']['schema']['properties']['reviews']['items']['properties']
    assert fields['source_urls']['items']['enum'] == [SOURCE]
    assert len(transport.calls) == 2


def test_unverified_research_does_not_expand_the_request_allowlist():
    transport = Transport(response(text='Source: https://invented.example.com/news'),
                          response({'reviews': [review()]}))
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], '2026-09-21')
    assert result['status'] == 'COMPLETE' and result['research_status'] == 'UNAVAILABLE'
    fields = transport.calls[1][2]['text']['format']['schema']['properties']['reviews']['items']['properties']
    assert fields['source_urls']['maxItems'] == 0


def test_cli_receives_the_same_closed_schema_without_an_extra_request():
    class SchemaWitness(Cli):
        schema = None

        def __call__(self, command, **kwargs):
            if '--output-schema' in command and 'web_search="disabled"' in command:
                self.schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text('utf-8'))
            return super().__call__(command, **kwargs)

    cli = SchemaWitness()
    result = CodexReviewer(command=['codex'], runner=cli).review([candidate()], '2026-09-21')
    assert result['status'] == 'COMPLETE'
    assert cli.schema['properties']['reviews']['items']['properties']['source_urls']['items']['enum'] == [SOURCE]
    assert sum('exec' in command for command, *_ in cli.calls) == 2


@pytest.mark.parametrize('url', [SOURCE + '/', SOURCE.replace('https:', 'http:'),
                                 SOURCE + '?utm_source=changed', 'https://invented.example.com/news'])
def test_nonmatching_reply_remains_rejected_with_stable_private_fingerprints(url, tmp_path):
    payload = {'reviews': [review(urls=[url])]}
    transport = Transport(researched(), response(payload))
    result = AstraReviewer(api_key=KEY, transport=transport, cache_dir=tmp_path).review([candidate()], '2026-09-21')
    assert result['status'] == 'FAILED' and result['reviews'] == []
    assert len(transport.calls) == 2 and not list(tmp_path.iterdir())
    info = result['citation_diagnostics']
    assert info['status'] == 'EXACT_SOURCE_ALLOWLIST_MISMATCH'
    assert info['rejected_url_sha256'] == hashlib.sha256(url.encode()).hexdigest()
    assert info['allowed_source_count'] == 1 and info['raw_reply_retained'] is False
    assert url not in json.dumps(info) and KEY not in json.dumps(result)


def test_old_unconstrained_cache_key_is_not_reused(tmp_path):
    reviewer = AstraReviewer(api_key=KEY, cache_dir=tmp_path)
    packed, prompt_hash = reviewer._prepare([candidate()], '2026-09-21', None)
    old_key = hashlib.sha256(json.dumps({
        'input': packed, 'prompt_sha256': prompt_hash, 'model': reviewer.model,
        'effort': reviewer.reasoning_effort, 'web_search': reviewer.web_search,
        'max_output_tokens': reviewer.max_output_tokens,
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    transport = Transport(researched(), response({'reviews': [review(urls=[SOURCE])]}))
    reviewer.transport = transport
    assert reviewer.review([candidate()], '2026-09-21')['status'] == 'COMPLETE'
    current = next(tmp_path.glob('*.json'))
    assert current.stem != old_key
    current.rename(tmp_path / (old_key + '.json'))
    reviewer.transport = Transport(researched(), response({'reviews': [review(urls=[SOURCE])]}))
    assert reviewer.review([candidate()], '2026-09-21')['status'] == 'COMPLETE'
    assert len(reviewer.transport.calls) == 2
