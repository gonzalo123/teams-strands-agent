import json
import logging
from importlib.resources import files
from typing import Literal

from strands import tool

logger = logging.getLogger("rq.worker")


def load_scenario() -> dict:
    """Read the packaged, fixed incident snapshot without depending on the working directory."""
    resource = files("teams_agent").joinpath("data", "incident.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _evidence(service: str, section: Literal["metrics", "deployments", "runbook"]) -> dict:
    scenario = load_scenario()
    service = service.strip().casefold()
    record = scenario["services"].get(service)
    if record is None:
        return {
            "source": scenario["source"],
            "error": "unknown_service",
            "available_services": sorted(scenario["services"]),
        }
    logger.info("Incident tool: %s service=%s", section, service)
    return {
        "source": scenario["source"],
        "snapshot": scenario["snapshot"],
        "service": service,
        section: record[section],
    }


@tool
def get_service_metrics(service: str) -> dict:
    """Read fictional current/baseline latency, errors and database pool metrics.

    Args:
        service: Service name: checkout or catalog.
    """
    return _evidence(service, "metrics")


@tool
def get_recent_deployments(service: str) -> dict:
    """Read fictional deployment history, changes and times relative to the fixed snapshot.

    Args:
        service: Service name: checkout or catalog.
    """
    return _evidence(service, "deployments")


@tool
def get_runbook(service: str) -> dict:
    """Read the service's fictional diagnostic checklist and possible mitigation; performs no actions.

    Args:
        service: Service name: checkout or catalog.
    """
    return _evidence(service, "runbook")
