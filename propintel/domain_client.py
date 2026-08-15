"""Domain API client — official, ToS-compliant.

Auth is OAuth2 client-credentials. Tokens are cached to data/.domain_token
(gitignored) until ~expiry. Endpoints use Domain's real routes:

  POST /v1/listings/residential/_search      (Agents & Listings package)
  GET  /v2/suburbPerformanceStatistics/...   (Properties & Locations package)
  GET  /v2/demographics/{state}/{suburb}/{postcode}
  GET  /v1/properties/{id}

A 403 "Operation not permitted on project" means the relevant package is not
attached to your Domain project. We raise PackageNotEnabled with guidance
rather than a bare HTTP error.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests as cf

from .config import settings

TOKEN_CACHE = settings.db_path.parent / ".domain_token"
SCOPES = (
    "api_listings_read api_agencies_read api_locations_read "
    "api_properties_read api_suburbperformance_read api_demographics_read"
)


class DomainError(RuntimeError):
    pass


class PackageNotEnabled(DomainError):
    """The Domain project lacks the API package for this operation."""

    def __init__(self, operation: str, package: str):
        self.operation = operation
        self.package = package
        super().__init__(
            f"Domain returned 403 'Operation not permitted on project' for {operation}.\n"
            f"  -> Add the '{package}' package to your project at "
            f"https://developer.domain.com.au (Projects -> your project -> Add package),\n"
            f"     then re-run. Auth works; only the package attachment is missing."
        )


class DomainClient:
    def __init__(self) -> None:
        if not settings.has_domain_creds:
            raise DomainError("DOMAIN_CLIENT_ID / DOMAIN_CLIENT_SECRET missing from .env")
        self.cfg = settings.domain
        self.api_calls = 0
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # ---- auth ----------------------------------------------------------
    def _load_cached_token(self) -> None:
        if TOKEN_CACHE.exists():
            try:
                data = json.loads(TOKEN_CACHE.read_text())
                if data.get("expiry", 0) > time.time() + 60:
                    self._token = data["access_token"]
                    self._token_expiry = data["expiry"]
            except Exception:
                pass

    def token(self) -> str:
        if self._token and self._token_expiry > time.time() + 60:
            return self._token
        self._load_cached_token()
        if self._token:
            return self._token
        r = cf.post(
            self.cfg["auth_url"],
            data={"grant_type": "client_credentials", "scope": SCOPES},
            auth=(settings.domain_client_id, settings.domain_client_secret),
            impersonate="chrome",
            timeout=30,
        )
        if r.status_code != 200:
            raise DomainError(f"Token request failed: HTTP {r.status_code} {r.text[:200]}")
        j = r.json()
        self._token = j["access_token"]
        self._token_expiry = time.time() + int(j.get("expires_in", 43200))
        try:
            TOKEN_CACHE.write_text(
                json.dumps({"access_token": self._token, "expiry": self._token_expiry})
            )
            TOKEN_CACHE.chmod(0o600)
        except Exception:
            pass
        return self._token

    # ---- low-level request --------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        package: str,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        url = self.cfg["api_base"] + path
        headers = {"Authorization": f"Bearer {self.token()}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        self.api_calls += 1
        r = cf.request(
            method, url, headers=headers, json=json_body, params=params,
            impersonate="chrome", timeout=40,
        )
        time.sleep(float(self.cfg.get("request_delay_seconds", 1.0)))
        if r.status_code == 403 and "not permitted on project" in r.text.lower():
            raise PackageNotEnabled(operation, package)
        if r.status_code == 401:
            raise DomainError(f"401 Unauthorized on {operation}: {r.text[:200]}")
        if r.status_code == 429:
            raise DomainError(f"Rate limited (429) on {operation}. Back off and retry later.")
        if r.status_code >= 400:
            raise DomainError(f"HTTP {r.status_code} on {operation}: {r.text[:300]}")
        return r.json()

    # ---- endpoints -----------------------------------------------------
    def search_listings(
        self,
        locations: list[dict],
        *,
        max_price: int,
        min_price: int = 0,
        property_types: list[str] | None = None,
        min_bedrooms: int | None = None,
        listing_type: str = "Sale",
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict]:
        body: dict[str, Any] = {
            "listingType": listing_type,
            "minPrice": min_price,
            "maxPrice": max_price,
            "locations": locations,
            "pageSize": page_size,
            "pageNumber": page,
        }
        if property_types:
            body["propertyTypes"] = property_types
        if min_bedrooms:
            body["minBedrooms"] = min_bedrooms
        return self._request(
            "POST", "/v1/listings/residential/_search",
            operation="listings search", package="Agents & Listings",
            json_body=body,
        )

    def suburb_performance(
        self,
        state: str,
        suburb: str,
        postcode: str,
        *,
        property_category: str = "House",
        bedrooms: int = 3,
        period_size: str = "years",
        total_periods: int = 5,
    ) -> dict:
        path = f"/v2/suburbPerformanceStatistics/{state}/{suburb}/{postcode}"
        params = {
            "propertyCategory": property_category,
            "bedrooms": bedrooms,
            "periodSize": period_size,
            "startingPeriodRelativeToCurrent": 0,
            "totalPeriods": total_periods,
        }
        return self._request(
            "GET", path,
            operation="suburb performance", package="Properties & Locations",
            params=params,
        )

    def demographics(self, state: str, suburb: str, postcode: str) -> dict:
        path = f"/v2/demographics/{state}/{suburb}/{postcode}"
        return self._request(
            "GET", path,
            operation="demographics", package="Properties & Locations",
        )

    def property_details(self, property_id: str) -> dict:
        return self._request(
            "GET", f"/v1/properties/{property_id}",
            operation="property details", package="Properties & Locations",
        )
