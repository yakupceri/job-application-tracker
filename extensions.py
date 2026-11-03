"""Shared Flask extension instances.

Kept in their own module (rather than created in app.py) so that app.py,
models.py and migrations/env.py can all import the same ``db`` instance
without circular imports.
"""

from __future__ import annotations

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "main.login"
