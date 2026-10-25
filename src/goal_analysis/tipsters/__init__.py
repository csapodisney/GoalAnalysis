"""Fictional selectors over shared evidence; no additional models or API calls."""


def build_tipster_recommendations(enriched, model_results, settings, now, *, diversity_options=None,
                                  option_search_mode="SHADOW"):
    # Keep catalogue imports dependency-free during configuration initialization.
    from .recommendations import build_tipster_recommendations as build

    return build(enriched, model_results, settings, now, diversity_options=diversity_options,
                 option_search_mode=option_search_mode)
