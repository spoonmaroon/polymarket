from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def normalize_binary_probability_pairs(
    rows: Sequence[Mapping[str, Any]],
    *,
    tolerance: float = 0.002,
) -> list[dict[str, Any]]:
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be nonnegative and finite")

    normalized_rows = [dict(row) for row in rows]
    groups: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(normalized_rows):
        key = _pair_key(row)
        if key is not None:
            groups[key].append(index)

    for indices in groups.values():
        up_indices = [index for index in indices if _side(normalized_rows[index]) == "UP"]
        down_indices = [index for index in indices if _side(normalized_rows[index]) == "DOWN"]
        if len(up_indices) != 1 or len(down_indices) != 1:
            continue
        up_index = up_indices[0]
        down_index = down_indices[0]
        up_p = _probability(normalized_rows[up_index].get("p_finish"))
        down_p = _probability(normalized_rows[down_index].get("p_finish"))
        if up_p is None or down_p is None:
            continue
        pair_sum = up_p + down_p
        if pair_sum <= 0 or not math.isfinite(pair_sum):
            continue
        gap = abs(1.0 - pair_sum)
        should_normalize = gap > tolerance
        normalized_up = up_p / pair_sum if should_normalize else up_p
        normalized_down = down_p / pair_sum if should_normalize else down_p
        for index, own_p, counterparty_p in (
            (up_index, normalized_up, normalized_down),
            (down_index, normalized_down, normalized_up),
        ):
            normalized_rows[index]["p_finish"] = own_p
            normalized_rows[index]["p_hat"] = own_p
            normalized_rows[index]["pair_probability_sum_before"] = pair_sum
            normalized_rows[index]["pair_complement_gap"] = gap
            normalized_rows[index]["pair_normalized"] = should_normalize
            normalized_rows[index]["counterparty_p_finish"] = counterparty_p
    return normalized_rows


def _pair_key(row: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    asset = _string(row.get("asset"))
    market = _string(row.get("market_slug"))
    start_ts = _string(row.get("start_ts"))
    expiry_ts = _string(row.get("expiry_ts"))
    if not asset or not expiry_ts:
        return None
    return (asset.upper(), market, start_ts, expiry_ts)


def _side(row: Mapping[str, Any]) -> str:
    return _string(row.get("side")).upper()


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _probability(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        return None
    return number
