# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Construct institution-pinned OIDC adapters from active registry versions."""
from __future__ import annotations

import threading
import time
from typing import Callable

from institutional_identity import IdentityProviderAdapter
from institutional_identity_provider_registry import InstitutionalIdentityProviderRegistry
from oidc_discovery import OIDCDiscoveryJWKSLoader
from oidc_https_transport import OIDCHTTPSJSONTransport
from oidc_identity_adapter import OIDCIdentityProviderAdapter


class OIDCInstitutionalAdapterFactory:
    """Resolve provider configuration only from the receiver's tenant registry.

    A cached adapter is keyed by exact immutable provider version. Suspension,
    revocation or a new head is observed on the next loader call; the current
    factory never accepts an issuer, JWKS URL or client ID from a credential.
    """

    def __init__(self, *, registry: InstitutionalIdentityProviderRegistry,
                 clock: Callable[[], float] = time.time):
        if not isinstance(registry, InstitutionalIdentityProviderRegistry) or not callable(clock):
            raise ValueError("OIDC_PROVIDER_FACTORY_CONFIGURATION_INVALID")
        self._registry = registry
        self._clock = clock
        self._lock = threading.RLock()
        self._adapters: dict[tuple[str, str, str], IdentityProviderAdapter] = {}

    def __call__(self, tenant_id: str, provider_id: str) -> IdentityProviderAdapter | None:
        config = self._registry.resolve_active_provider(
            tenant_id, provider_id, now=int(self._clock())
        )
        with self._lock:
            if config is None:
                for cached_key in tuple(self._adapters):
                    if cached_key[:2] == (tenant_id, provider_id):
                        del self._adapters[cached_key]
                return None
            key = (tenant_id, provider_id, config["version"])
            for cached_key in tuple(self._adapters):
                if cached_key[:2] == (tenant_id, provider_id) and cached_key != key:
                    del self._adapters[cached_key]
            existing = self._adapters.get(key)
            if existing is not None:
                return existing
            transport = OIDCHTTPSJSONTransport(
                allowed_hosts=tuple(config["allowed_hosts"])
            )
            loader = OIDCDiscoveryJWKSLoader(
                issuer=config["issuer"],
                allowed_hosts=tuple(config["allowed_hosts"]),
                fetch_json=transport,
                allowed_algorithms=tuple(config["allowed_algorithms"]),
                cache_ttl_seconds=config["cache_ttl_seconds"],
                clock=self._clock,
            )
            adapter = OIDCIdentityProviderAdapter(
                issuer=config["issuer"],
                client_id=config["client_id"],
                jwks_loader=loader,
                allowed_algorithms=tuple(config["allowed_algorithms"]),
                clock=self._clock,
            )
            self._adapters[key] = adapter
            return adapter
