"""Read-only native source readiness; never fabricate market/model forecasts."""
from goal_analysis.agents.weather_market_validation import FAMILIES, VERSION, market_outcome
from goal_analysis.agents.weather_native_dataset import load_native_weather_dataset
from goal_analysis.storage.weather_protocols import WeatherProtocols


def inspect_weather_market_readiness(root, *, as_of):
    source = load_native_weather_dataset(root, as_of=as_of)
    try:
        protocols = WeatherProtocols(root).inspect(as_of=as_of)
    except ValueError as error:
        if 'BUDGET' not in str(error):
            raise
        protocols = {'protocols': [], 'gaps': [str(error)]}
    rows = []
    for family in FAMILIES:
        targets, paired = 0, 0
        for match in source['matches']:
            try:
                market_outcome(match, family)
            except ValueError:
                continue
            targets += 1
            paired += 'weather' in match and 'prematch_weather' in match
        rows.append({'family': family, 'result_targets_available': targets,
                     'result_reanalysis_prematch_forecast_pairs': paired,
                     'status': 'UNAVAILABLE_VERIFIED_PROTOCOL_AND_MODEL_FORECASTS_REQUIRED',
                     'registered_protocol_store_integrated': True,
                     'verified_registered_protocol_count': sum(
                         p['envelope']['payload']['family'] == family for p in protocols['protocols']),
                     'protocol_inventory_complete': not bool(protocols['gaps']),
                     'verified_market_probability_forecast_store_integrated': False,
                     'heldout_market_validation_complete': False, 'live_weight_eligible': False})
    return {'version': VERSION, 'mode': 'SHADOW', 'status': 'SOURCE_AND_FORECAST_EVIDENCE_PENDING',
            'as_of': source['as_of'], 'markets': rows, 'source_coverage': source['coverage'],
            'source_gaps': source['gaps'], 'protocol_source_gaps': protocols['gaps'],
            'source_dataset_id': source['dataset_manifest']['dataset_id'],
            'external_requests': 0, 'production_writes': 0, 'live_decision_effect': False}
