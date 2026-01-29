"""
PostgreSQL cluster registry management.

This module provides access to the centralized PostgreSQL cluster configuration
stored in /etc/postgres/clusters.yaml (mounted from the postgres-clusters secret).

Each cluster entry contains:
- host: PostgreSQL hostname or IP
- port: PostgreSQL port (default 5432)
- adminUser: Admin username for creating databases/users
- adminPassword: Admin password
- default: Boolean indicating if this is the default cluster

OdooInstances can specify which cluster to use via spec.database.cluster.
If not specified, the cluster with 'default: true' is used.
"""

import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)

# Path to the clusters configuration file (mounted from secret)
CLUSTERS_FILE = os.environ.get("POSTGRES_CLUSTERS_FILE", "/etc/postgres/clusters.yaml")

# Cache for loaded clusters (reloaded on file change)
_clusters_cache: Optional[Dict[str, "PostgresCluster"]] = None
_clusters_mtime: float = 0


@dataclass
class PostgresCluster:
    """Configuration for a PostgreSQL cluster."""

    name: str
    host: str
    port: int
    admin_user: str
    admin_password: str
    is_default: bool = False

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "PostgresCluster":
        """Create a PostgresCluster from a dictionary."""
        return cls(
            name=name,
            host=data.get("host", "localhost"),
            port=int(data.get("port", 5432)),
            admin_user=data.get("adminUser", "postgres"),
            admin_password=data.get("adminPassword", ""),
            is_default=data.get("default", False),
        )


def _load_clusters() -> Dict[str, PostgresCluster]:
    """Load clusters from the configuration file."""
    global _clusters_cache, _clusters_mtime

    # Check if file exists
    if not os.path.exists(CLUSTERS_FILE):
        logger.warning(f"PostgreSQL clusters file not found: {CLUSTERS_FILE}")
        # Return empty dict - will fall back to legacy env vars
        return {}

    # Check if file has been modified
    current_mtime = os.path.getmtime(CLUSTERS_FILE)
    if _clusters_cache is not None and current_mtime == _clusters_mtime:
        return _clusters_cache

    # Load and parse the file
    try:
        with open(CLUSTERS_FILE, "r") as f:
            data = yaml.safe_load(f)

        if not data:
            logger.warning("PostgreSQL clusters file is empty")
            return {}

        clusters = {}
        for name, config in data.items():
            if isinstance(config, dict):
                clusters[name] = PostgresCluster.from_dict(name, config)
                logger.info(
                    f"Loaded PostgreSQL cluster: {name} -> {config.get('host')}"
                )

        _clusters_cache = clusters
        _clusters_mtime = current_mtime
        logger.info(f"Loaded {len(clusters)} PostgreSQL cluster(s)")
        return clusters

    except Exception as e:
        logger.error(f"Failed to load PostgreSQL clusters: {e}")
        return {}


def get_default_cluster() -> Optional[PostgresCluster]:
    """
    Get the cluster marked as default.

    Returns:
        The default PostgresCluster, or None if no default is configured.
    """
    clusters = _load_clusters()
    for cluster in clusters.values():
        if cluster.is_default:
            return cluster
    return None


def get_cluster(name: Optional[str] = None) -> PostgresCluster:
    """
    Get a PostgreSQL cluster configuration by name.

    Resolution order:
    1. If a cluster name is specified, use exact match
    2. If no name specified, use the cluster marked as default

    Args:
        name: Cluster name (optional)

    Returns:
        PostgresCluster configuration

    Raises:
        ValueError: If the cluster is not found
    """
    clusters = _load_clusters()

    # 1. Exact match if name specified
    if name and name in clusters:
        return clusters[name]

    # If name specified but not found, that's an error
    if name:
        available = list(clusters.keys()) if clusters else ["(none configured)"]
        raise ValueError(
            f"PostgreSQL cluster '{name}' not found. Available clusters: {available}"
        )

    # 2. No name specified - use the default cluster
    default_cluster = get_default_cluster()
    if default_cluster:
        logger.info(f"Using default cluster: {default_cluster.name}")
        return default_cluster

    raise ValueError(
        "No PostgreSQL cluster available: no cluster specified and no default cluster configured. "
        "Ensure exactly one cluster has 'default: true' in the postgres-clusters secret."
    )


def get_cluster_for_instance(spec: dict) -> PostgresCluster:
    """
    Get the PostgreSQL cluster for an OdooInstance based on its spec.

    Args:
        spec: The OdooInstance spec dictionary

    Returns:
        PostgresCluster configuration
    """
    database_config = spec.get("database", {})
    cluster_name = database_config.get("cluster")  # None if not specified
    return get_cluster(cluster_name)


def list_clusters() -> Dict[str, PostgresCluster]:
    """List all available PostgreSQL clusters."""
    return _load_clusters()
