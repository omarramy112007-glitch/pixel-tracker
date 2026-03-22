# outreach_engine/core/proxy_rotator.py

import itertools
from typing import Optional

# ---------------------------------------------------
# Proxy Pool
# ---------------------------------------------------

PROXIES = [
    None,  # allow direct connection
    # Example proxies (replace with real ones if needed)
    # "http://proxy1:8080",
    # "http://proxy2:8080",
    # "http://proxy3:8080",
]

# Cycle iterator
_proxy_cycle = itertools.cycle(PROXIES)


# ---------------------------------------------------
# Proxy Rotator
# ---------------------------------------------------

def get_next_proxy() -> Optional[str]:
    """
    Returns the next proxy in rotation.
    """

    proxy = next(_proxy_cycle)

    if proxy:
        print(f"🌐 Using proxy: {proxy}")

    return proxy