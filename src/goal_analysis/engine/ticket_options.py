"""Bounded deterministic combinations of already eligible, immutable selections.

This search supplies alternatives, never relaxes evidence/price eligibility.
Its beam is a disclosed computational bound, not a claim of exhaustive search.
"""

import math
from collections import defaultdict


def bounded_ticket_groups(
    rows, *, rank, fixture, cohort, price, identity, max_legs, max_price,
    beam_width=32, max_options=128, allow_price_overflow=None,
    min_legs=1, min_price=None, beam_rank=None,
    fixture_coverage=False,
):
    """Return ranked tuples with one selection/fixture and one quote cohort.

    All individual sources are compared. Duplicate exposure is consolidated
    only after comparison, so a worse source cannot hide a better quote.
    Minimum legs/price constrain complete outputs, never growth prefixes.
    Singleton fallbacks retain room only when the caller permits them. The
    optional beam rank may favor useful prefixes independently of final rank.
    """
    if type(fixture_coverage) is not bool or (fixture_coverage and min_price is None):
        raise ValueError("Fixture coverage requires a boolean flag and fixed minimum price")
    partial_rank = beam_rank or rank

    def complete(group):
        if not min_legs <= len(group) <= max_legs:
            return False
        prices = [price(row) for row in group]
        return min_price is None or (
            all(value is not None for value in prices) and math.prod(prices) >= min_price
        )

    def retain(groups):
        ordered = sorted(groups, key=partial_rank)
        if min_price is None or len(ordered) <= beam_width or beam_width <= 1:
            return ordered[:beam_width]
        # A quality-only beam can lose every higher-price prefix before it
        # reaches the accumulator floor. Keep half for quality and use the
        # rest for a compliant endpoint plus distinct log-price strata.
        chosen = ordered[:max(1, beam_width // 2)]
        used = {id(group) for group in chosen}

        def add(group):
            if len(chosen) < beam_width and id(group) not in used:
                chosen.append(group)
                used.add(id(group))

        for group in ordered:
            if complete(group):
                add(group)
                break
        priced = [(sum(math.log(price(row)) for row in group), index, group)
                  for index, group in enumerate(ordered)
                  if all(price(row) is not None for row in group)]
        if priced:
            # Retain the high endpoint even when the required price is only
            # reachable after adding a later fixture to this weaker prefix.
            add(max(priced, key=lambda item: (item[0], -item[1]))[2])
            if fixture_coverage:
                # Reserve at most a quarter of the same beam for prefixes
                # introducing eligible fixtures absent from its quality head.
                # Sources/scores/price and complete-ticket gates are unchanged.
                represented = {fixture(row) for group in chosen for row in group}
                footprints = [(index, group, {fixture(row) for row in group})
                              for index, group in enumerate(ordered)]
                for _ in range(max(1, beam_width // 4)):
                    if len(chosen) == beam_width:
                        break
                    useful = [(len(ids - represented), index, group, ids)
                              for index, group, ids in footprints
                              if id(group) not in used and ids - represented]
                    if not useful:
                        break
                    _, _, group, ids = min(useful, key=lambda item: (-item[0], item[1]))
                    add(group)
                    represented.update(ids)
            slots = beam_width - len(chosen)
            if slots:
                low, high = min(item[0] for item in priced), max(item[0] for item in priced)
                strata = {}
                for value, _index, group in priced:
                    if id(group) in used:
                        continue
                    bucket = min(slots - 1, int((value - low) / (high - low) * slots)) if high > low else 0
                    # priced follows partial_rank, so the first is strongest
                    # within this price stratum.
                    strata.setdefault(bucket, group)
                for bucket in sorted(strata):
                    add(strata[bucket])
        for group in ordered:
            add(group)
            if len(chosen) == beam_width:
                break
        return sorted(chosen, key=partial_rank)

    cohorts = defaultdict(lambda: defaultdict(list))
    explored = 0
    bounded = False
    if max_legs < 1:
        return [], {"combinations_examined": 0, "bookmaker_pools_searched": 0,
                    "bounded_search": False, "beam_width": beam_width,
                    "max_options": max_options}
    for row in rows:
        value = price(row)
        if value is not None and (not math.isfinite(value) or value <= 1
                                  or (value > max_price and not (
                                      allow_price_overflow and allow_price_overflow((row,))))):
            continue
        cohorts[cohort(row)][fixture(row)].append(row)
    candidates = []
    for key in sorted(cohorts, key=repr):
        beam = {0: [()]}
        for identifier in sorted(cohorts[key]):
            choices = sorted(cohorts[key][identifier], key=lambda row: partial_rank((row,)))
            next_beam = {count: list(groups) for count, groups in beam.items()}
            for count, groups in beam.items():
                if count >= max_legs:
                    continue
                for group in groups:
                    for row in choices:
                        explored += 1
                        trial = group + (row,)
                        prices = [price(item) for item in trial]
                        if (all(value is not None for value in prices) and math.prod(prices) > max_price
                                and not (allow_price_overflow and allow_price_overflow(trial))):
                            continue
                        next_beam.setdefault(count + 1, []).append(trial)
                        if count == 0:
                            candidates.append(trial)
            beam = {}
            for count, groups in next_beam.items():
                if count == 0:
                    beam[count] = [()]
                    continue
                bounded |= len(groups) > beam_width
                beam[count] = retain(groups)
        candidates.extend(group for count, groups in beam.items() if count for group in groups)
    # identity describes fixture + market + outcome + period, not the source.
    unique = {}
    for group in candidates:
        # A noncompliant source must not hide a different, eligible quote for
        # the same economic selections during source deduplication.
        if not complete(group):
            continue
        signature = tuple(sorted(identity(row) for row in group))
        previous = unique.get(signature)
        if previous is None or rank(group) < rank(previous):
            unique[signature] = group
    ordered = sorted(unique.values(), key=rank)
    # Keep second choices visible to the concentration allocator even when
    # many top combinations share the same small core of fixtures.
    singleton_budget = max_options // 2 if min_legs <= 1 else 0
    singles = [group for group in ordered if len(group) == 1][:singleton_budget]
    multis = [group for group in ordered if len(group) > 1]
    multi_budget = max_options - len(singles)
    first_multi = max(2, min_legs)
    quota = max(1, multi_budget // max(1, max_legs - first_multi + 1))
    chosen = []
    for count in range(first_multi, max_legs + 1):
        chosen.extend([group for group in multis if len(group) == count][:quota])
    chosen = chosen[:multi_budget]
    signatures = {tuple(sorted(identity(row) for row in group)) for group in chosen}
    for group in singles:
        signature = tuple(sorted(identity(row) for row in group))
        if signature not in signatures:
            chosen.append(group)
            signatures.add(signature)
    for group in ordered:
        if len(chosen) >= max_options:
            break
        signature = tuple(sorted(identity(row) for row in group))
        if signature not in signatures:
            chosen.append(group)
            signatures.add(signature)
    bounded |= len(ordered) > max_options
    return sorted(chosen, key=rank), {
        "combinations_examined": explored, "bookmaker_pools_searched": len(cohorts),
        "bounded_search": bounded, "beam_width": beam_width, "max_options": max_options,
        "search_method": "BOUNDED_CALLER_DEFINED_TICKET_RANK",
    }
