"""Firebase identity verification. No local navigation bypass authorizes API work."""
from dataclasses import dataclass
import os

from firebase_admin import auth, exceptions
from pydantic import TypeAdapter

from .records import Id


class AuthenticationRejected(Exception):
    pass


class AuthenticationUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    uid: str
    display_name: str


class FirebaseIdentityVerifier:
    def __init__(self, app):
        # Admin SDK emulators accept unsigned tokens. Never enable that in this
        # deployable API, including when DEBUG navigation is enabled on iOS.
        if os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
            raise ValueError("The application API requires real Firebase identity")
        self.app = app

    def verify(self, token: str) -> Identity:
        try:
            claims = auth.verify_id_token(token, app=self.app, check_revoked=True)
            uid = TypeAdapter(Id).validate_python(claims["uid"])
            name = claims.get("name")
            name = name.strip()[:80] if isinstance(name, str) else ""
            return Identity(uid, name or "Player")
        except (auth.InvalidIdTokenError, auth.UserDisabledError,
                auth.UserNotFoundError, ValueError, KeyError) as error:
            raise AuthenticationRejected() from error
        except (exceptions.FirebaseError, OSError) as error:
            # Permission/network/certificate failures must fail closed, without
            # pretending the user's password is wrong or leaking upstream data.
            raise AuthenticationUnavailable() from error
