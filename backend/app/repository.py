"""Shared transaction boundary. Agent implementations must not import ORM sessions."""
from .db import Session

class Repository:
    def transaction(self):
        return Session.begin()

    def read(self):
        return Session()
