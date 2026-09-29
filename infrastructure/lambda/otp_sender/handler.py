"""Cognito custom-message trigger: deliver the OTP for a CUSTOM_AUTH challenge.

This is the pattern AWS documents for passwordless OTP on a user pool. The
trigger fires when Cognito builds the message for a challenge, we drop the code
into `codeParameter`, and Cognito carries the code out to the user. The handler
never calls AdminRespondToAuthChallenge: Cognito owns the challenge, so the only
thing this function has to do is put a code in the message.

The code is logged at INFO and not sent anywhere. That is a deliberate
development stand-in: it makes the pool usable end to end without an email or
SMS provider, and it is why this function must not face live traders until SES
or SNS replaces the log line. The warning below fires on every invocation so the
log cannot be mistaken for a delivery path.

`customMessageSource` is the switch that keeps a normal sign-up email, a forgot
password email, or an admin invite from receiving a six digit code instead of
the text it is supposed to carry. Only a custom-auth challenge has a source.
"""

import logging
import os
import secrets

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_DELIVERY_MEDIUM = os.environ.get("DEFAULT_DELIVERY_MEDIUM")
CODE_LENGTH = 6

PRODUCTION_WARNING = (
    "otp_sender is LOGGING the verification code, not delivering it. It is a "
    "development stand-in. Wire SES or SNS before a real trader uses this pool."
)

# When the medium cannot be determined the code is still generated, because
# Cognito has already committed to building a challenge by the time this runs.
# Refusing here would fail the sign-in with an opaque error, so the medium is
# logged instead and the trader still gets a working code.
UNKNOWN_MEDIUM = "UNSPECIFIED"


def generate_code() -> str:
    """A uniform six-digit code from the system CSPRNG.

    `secrets.choice`, not a modulo of raw bytes: 256 is not a multiple of 10, so
    `b % 10` would make 0 roughly twice as likely as any other digit. That bias
    is small, but this is a sign-in code, so the cheapest correct generator wins.
    """
    return "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))


def medium_for(username: str, attributes: dict) -> str:
    """Pick the delivery medium from the user attributes."""
    if DEFAULT_DELIVERY_MEDIUM:
        return DEFAULT_DELIVERY_MEDIUM

    if attributes.get("phone_number"):
        return "SMS"
    if attributes.get("email"):
        return "EMAIL"
    # Cognito uses the email as the username when the pool allows email
    # usernames; a phone username carries no @.
    if "@" in username:
        return "EMAIL"
    return UNKNOWN_MEDIUM


def lambda_handler(event, context):
    logger.warning(PRODUCTION_WARNING)

    request = event.get("request", {})
    attributes = request.get("userAttributes", {})
    username = request.get("userName", "")

    # Only a custom auth challenge gets a code. Every other trigger (sign-up
    # confirmation, forgot password, admin invite) returns untouched so it keeps
    # the message Cognito built for it.
    if event.get("customMessageSource") != "CustomAuthChallenge":
        return event

    medium = medium_for(username, attributes)
    code = generate_code()

    logger.warning("OTP for %s via %s: %s", username, medium, code)

    request["codeParameter"] = f"{code} is your Trading Brain sign-in code."

    return event
