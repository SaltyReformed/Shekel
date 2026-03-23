"""
Shekel Budget App -- Health Check Endpoint

Provides a lightweight health check for Docker HEALTHCHECK, load
balancers, and external monitoring.  Returns database connectivity
status as JSON.

This endpoint:
- Requires no authentication (external monitors must reach it).
- Is excluded from request logging (avoid noise from frequent checks).
- Does not trigger audit log writes (SELECT 1 touches no audited tables).
"""

import logging

from flask import Blueprint, jsonify

from app.extensions import db

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__)


@health_bp.route("/health")
def health_check():
    """Return application and database health status.

    Returns:
        200 JSON: {"status": "healthy", "database": "connected"}
        500 JSON: {"status": "unhealthy", "database": "error"}

    The error response intentionally omits exception details to prevent
    information disclosure (connection strings, hostnames, credentials).
    Full details are logged server-side for operator diagnostics.
    """
    try:
        # Verify database connectivity with a lightweight query.
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Health check failed: %s", exc)
        # Do NOT include str(exc) in the response -- it may contain
        # database hostnames, ports, or credentials.  See audit M5.
        return jsonify({
            "status": "unhealthy",
            "database": "error",
        }), 500
