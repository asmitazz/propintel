"""ABS Data API client — free, open, no authentication.

The ABS Data API speaks SDMX-JSON. We use a single generic parser
(`fetch_observations`) and build specific indicator pulls on top of it.

Data sources used:
  - ERP_ASGS2021         Estimated Resident Population by SA2 (demand)
  - BA_SA2               Building Approvals by SA2 (future supply — inverted signal)
  - ABS_REGIONAL_MIGRATION  Net internal + overseas migration by SA2 (demand)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from curl_cffi import requests as cf

BASE = "https://data.api.abs.gov.au/rest"
HEADERS = {"Accept": "application/vnd.sdmx.data+json"}


def fetch_observations(
    dataflow: str,
    key: str = "all",
    params: dict[str, str] | None = None,
    timeout: int = 90,
    retries: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Fetch a dataflow and return (observations, region_names).

    Each observation is a dict: {dim_id: code, ..., 'VALUE': float, 'TIME_PERIOD': str}.
    region_names maps region code -> human name for whichever dimension is the region.

    Robust to a slow/unresponsive ABS API: each attempt is hard-capped (the
    curl timeout alone was observed not to fire on a 0-byte hang), and transient
    failures retry with backoff so the daily refresh self-heals.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout

    params = {"dimensionAtObservation": "AllDimensions", **(params or {})}
    url = f"{BASE}/data/{dataflow}/{key}"
    last_err = None
    for attempt in range(retries):
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(cf.get, url, headers=HEADERS, params=params,
                                impersonate="chrome", timeout=timeout)
                r = fut.result(timeout=timeout + 15)   # hard cap, even if curl's doesn't fire
            if r.status_code == 404:
                raise RuntimeError(f"ABS dataflow/key not found: {dataflow}/{key}")
            r.raise_for_status()
            payload = r.json()
            break
        except RuntimeError:
            raise                                        # 404 = permanent, don't retry
        except (_FTimeout, Exception) as e:              # timeout / network / 5xx
            last_err = e
            if attempt < retries - 1:
                time.sleep(8 * (attempt + 1))
            else:
                raise RuntimeError(f"ABS fetch failed after {retries} attempts "
                                   f"({dataflow}/{key}): {last_err}") from last_err

    data = payload["data"]
    struct = (data.get("structures") or [data["structure"]])[0]
    dims = struct["dimensions"]["observation"]
    dim_ids = [d["id"] for d in dims]
    dim_values = [d["values"] for d in dims]

    # Identify the region dimension (holds SA2 codes/names).
    region_dim_idx = None
    for i, did in enumerate(dim_ids):
        if did.upper() in ("ASGS_2021", "REGION", "ASGS_2016") or "ASGS" in did.upper():
            region_dim_idx = i
            break

    region_names: dict[str, str] = {}
    observations: list[dict[str, Any]] = []
    obs = data["dataSets"][0]["observations"]
    for key_str, val in obs.items():
        idx = [int(x) for x in key_str.split(":")]
        row: dict[str, Any] = {}
        for i, pos in enumerate(idx):
            code = dim_values[i][pos].get("id")
            row[dim_ids[i]] = code
            if i == region_dim_idx:
                region_names[code] = dim_values[i][pos].get("name", "")
        row["VALUE"] = val[0]
        observations.append(row)
    return observations, region_names


def _region_key(row: dict[str, Any]) -> str | None:
    for k in ("ASGS_2021", "REGION", "ASGS_2016"):
        if k in row:
            return row[k]
    return None


def pull_population(region_type: str = "SA2") -> dict[str, dict[str, Any]]:
    """Return {region_code: {name, population(latest), pop_growth_pct(CAGR), periods}}.

    region_type: 'SA2' (suburbs, ~10k people) or 'SA4' (cities/large regions,
    e.g. 'Gold Coast', 'Ipswich', 'Cairns'). ERP has no total-age band, so we
    take AGE=TOT, SEX=3 (Persons) directly — one value per region/period.
    """
    # NOTE: SDMX positional-key filtering is unreliable across dataflows, so we
    # fetch rows and select client-side. key = MEASURE.SEX.AGE.REGION_TYPE.ASGS.FREQ
    obs, names = fetch_observations(
        "ERP_ASGS2021", key=f"ERP...{region_type}..A", params={"startPeriod": "2019"}
    )
    totals: dict[str, dict[str, float]] = defaultdict(dict)
    for row in obs:
        if str(row.get("SEX")) != "3" or str(row.get("AGE")) != "TOT":
            continue
        if row.get("REGION_TYPE") not in (None, region_type):
            continue
        code = _region_key(row)
        if not code:
            continue
        totals[code][row["TIME_PERIOD"]] = float(row["VALUE"] or 0)

    result: dict[str, dict[str, Any]] = {}
    for code, byperiod in totals.items():
        periods = sorted(byperiod)
        if not periods:
            continue
        latest = periods[-1]
        earliest = periods[0]
        pop_latest = byperiod[latest]
        pop_earliest = byperiod[earliest]
        years = max(1, int(latest) - int(earliest))
        cagr = None
        if pop_earliest > 0 and pop_latest > 0:
            cagr = ((pop_latest / pop_earliest) ** (1 / years) - 1) * 100
        result[code] = {
            "name": names.get(code, ""),
            "population": pop_latest,
            "pop_growth_pct": cagr,
            "latest_period": latest,
            "periods": {p: byperiod[p] for p in periods},
        }
    return result


def pull_simple_indicator(
    dataflow: str, key: str = "all", start_period: str = "2021"
) -> dict[str, dict[str, Any]]:
    """Generic: latest value per region for a single-measure dataflow.

    Used for building approvals and migration. Sums all non-region breakdown
    dimensions into a single latest-period value per region.
    """
    obs, names = fetch_observations(
        dataflow, key=key, params={"startPeriod": start_period}
    )
    byregion_period: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in obs:
        code = _region_key(row)
        if not code:
            continue
        byregion_period[code][row["TIME_PERIOD"]] += float(row["VALUE"] or 0)

    result: dict[str, dict[str, Any]] = {}
    for code, byperiod in byregion_period.items():
        periods = sorted(byperiod)
        if not periods:
            continue
        latest = periods[-1]
        result[code] = {
            "name": names.get(code, ""),
            "value": byperiod[latest],
            "latest_period": latest,
        }
    return result
