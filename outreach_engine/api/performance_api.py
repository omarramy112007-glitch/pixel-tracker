# File: outreach_engine/api/performance_api.py

from fastapi import FastAPI
from outreach_engine.core.performance_logger import get_performance_metrics

app = FastAPI()

@app.get("/system/performance")
def performance_dashboard():
    """
    Returns real-time performance metrics.
    """
    return get_performance_metrics()