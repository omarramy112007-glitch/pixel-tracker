# core/proxy_manager.py

from lead_engine.config import PROXIES
import random
import itertools

_proxy_cycle = itertools.cycle(PROXIES) if PROXIES else None


def get_proxy():
    """
    Round-robin proxy
    """
    if not _proxy_cycle:
        return None
    return next(_proxy_cycle)


def get_random_proxy():
    if not PROXIES:
        return None
    return random.choice(PROXIES)