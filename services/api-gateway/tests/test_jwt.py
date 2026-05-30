import time

import jwt
import pytest

from app.auth.jwt_handler import create_token, decode_token
from app.config import settings


class TestCreateToken:
    def test_returns_string(self):
        token = create_token("user-123", "testuser")
        assert isinstance(token, str)

    def test_token_contains_user_id(self):
        token = create_token("user-123", "testuser")
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        assert payload["user_id"] == "user-123"

    def test_token_contains_username(self):
        token = create_token("user-123", "testuser")
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        assert payload["username"] == "testuser"

    def test_token_has_expiry(self):
        token = create_token("user-123", "testuser")
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        assert "exp" in payload

    def test_token_has_issued_at(self):
        token = create_token("user-123", "testuser")
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        assert "iat" in payload


class TestDecodeToken:
    def test_roundtrip(self):
        token = create_token("user-456", "roundtrip")
        payload = decode_token(token)
        assert payload["user_id"] == "user-456"
        assert payload["username"] == "roundtrip"

    def test_invalid_token_raises(self):
        with pytest.raises(jwt.InvalidTokenError):
            decode_token("not.a.valid.token")

    def test_wrong_secret_raises(self):
        token = jwt.encode(
            {"user_id": "x", "username": "x"},
            "wrong-secret",
            algorithm="HS256",
        )
        with pytest.raises(jwt.InvalidSignatureError):
            decode_token(token)

    def test_expired_token_raises(self):
        token = jwt.encode(
            {"user_id": "x", "username": "x", "exp": time.time() - 10},
            settings.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token)
