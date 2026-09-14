"""Initialize reliable public DNS resolver.

Bypasses ISP DNS blocking/poisoning by using Cloudflare and Google
public DNS resolvers.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def setup_dns_resolver() -> None:
    """Configure dnspython to use reliable public DNS servers.

    Uses Cloudflare (1.1.1.1) and Google (8.8.8.8) DNS as fallbacks.
    This helps in regions where ISP DNS may block or poison certain domains.
    """
    try:
        import dns.resolver

        resolver = dns.resolver.Resolver()
        resolver.nameservers = [
            "1.1.1.1",   # Cloudflare
            "1.0.0.1",   # Cloudflare secondary
            "8.8.8.8",   # Google
            "8.8.4.4",   # Google secondary
        ]
        resolver.lifetime = 10.0
        resolver.timeout = 5.0

        # Set as the default resolver
        dns.resolver.default_resolver = resolver
        logger.info("DNS resolver configured with Cloudflare + Google DNS")
    except ImportError:
        logger.debug("dnspython not installed, using system DNS")
    except Exception as e:
        logger.warning(f"Failed to configure DNS resolver: {e}")
