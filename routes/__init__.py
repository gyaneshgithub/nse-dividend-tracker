"""Flask blueprints for the Dividend Tracker.

`server.py` owns the app object, request guards, security headers, error
handlers and startup. Each module in this package owns one area of the API so
the app stays readable as it grows.
"""

from __future__ import annotations

from .agent_routes import bp as agent_bp
from .analysis import bp as analysis_bp
from .dividends import bp as dividends_bp
from .pages import bp as pages_bp
from .portfolio import bp as portfolio_bp
from .system import bp as system_bp

BLUEPRINTS = (pages_bp, portfolio_bp, dividends_bp, analysis_bp, agent_bp, system_bp)


def register_blueprints(app):
    """Attach every blueprint to the Flask app."""
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)
