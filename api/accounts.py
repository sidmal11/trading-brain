"""Whose account a request trades in: always the signed-in caller's, derived on the server, never sent by the client."""
from config import Config

from .access import normalise, owner_email

ACCOUNT_OFF = "Mock trading is turned off for your account. Ask the owner."
LOCAL = "local-development"


def caller_email(user: dict) -> str:
    """The email claim, else the subject, else the fixed local-development identity."""
    return normalise((user or {}).get("email") or (user or {}).get("sub") or LOCAL)


def request_email(request) -> str:
    # Without sign-in (local development) require_user sets no request.state.user.
    return caller_email(getattr(request.state, "user", None))


def owner() -> str:
    """The owner's email; local-development when OWNER_EMAIL is unset (the account the shared history went to)."""
    return owner_email() or LOCAL


def is_owner(email: str) -> bool:
    return normalise(email) == owner()


def account_for(email: str) -> dict:
    """The caller's mock account, created on first use."""
    from .trading import get_trades_store  # read at call time: tests replace it

    return get_trades_store().ensure_account(normalise(email), Config.MOCK_STARTING_CASH)
