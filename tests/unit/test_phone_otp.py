"""Tests for PhoneOTP — phone number verification for store registration.

This model had zero test coverage before this file. Covers: code generation
uses cryptographically secure randomness (not the predictable `random`
module), verification logic (success, wrong code, expiry, single-use,
brute-force lockout), and that generating a new OTP invalidates prior ones.
"""
from datetime import datetime, timedelta, timezone

from smart_mart.extensions import db
from smart_mart.models.phone_otp import PhoneOTP


def test_generate_creates_six_digit_numeric_code(db):
    otp = PhoneOTP.generate("9800000001")
    assert len(otp.code) == 6
    assert otp.code.isdigit()


def test_generate_uses_cryptographically_secure_randomness(db):
    """Regression test: code generation must use `secrets`, not `random`.
    `random` (Mersenne Twister) is not cryptographically secure — its
    internal state can theoretically be reconstructed from enough observed
    outputs, which matters for a control whose entire purpose is proving
    phone number possession."""
    import smart_mart.models.phone_otp as otp_module
    assert not hasattr(otp_module, "random"), (
        "phone_otp.py should not import the 'random' module — use 'secrets' "
        "for any security-sensitive code generation"
    )
    assert hasattr(otp_module, "secrets")


def test_verify_succeeds_with_correct_code(db):
    otp = PhoneOTP.generate("9800000002")
    assert PhoneOTP.verify("9800000002", otp.code) is True


def test_verify_fails_with_wrong_code(db):
    PhoneOTP.generate("9800000003")
    assert PhoneOTP.verify("9800000003", "000000") is False


def test_verify_fails_for_wrong_phone_number(db):
    otp = PhoneOTP.generate("9800000004")
    assert PhoneOTP.verify("9800000099", otp.code) is False


def test_verify_is_single_use(db):
    """A code cannot be verified twice — first success marks it used."""
    otp = PhoneOTP.generate("9800000005")
    assert PhoneOTP.verify("9800000005", otp.code) is True
    assert PhoneOTP.verify("9800000005", otp.code) is False


def test_verify_fails_after_expiry(db):
    otp = PhoneOTP.generate("9800000006", ttl_minutes=10)
    # Force it into the past
    otp.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.session.commit()
    assert PhoneOTP.verify("9800000006", otp.code) is False


def test_verify_locks_out_after_five_wrong_attempts(db):
    otp = PhoneOTP.generate("9800000007")
    for _ in range(5):
        assert PhoneOTP.verify("9800000007", "000000") is False
    # 6th attempt: even the CORRECT code should now be rejected (locked out)
    assert PhoneOTP.verify("9800000007", otp.code) is False


def test_generating_new_otp_invalidates_previous_unused_ones(db):
    """Requesting a new OTP for the same phone must invalidate the old one
    — otherwise an attacker could accumulate multiple valid codes."""
    first = PhoneOTP.generate("9800000008")
    second = PhoneOTP.generate("9800000008")
    assert PhoneOTP.verify("9800000008", first.code) is False
    assert PhoneOTP.verify("9800000008", second.code) is True


def test_verify_returns_false_when_no_otp_exists_for_phone(db):
    assert PhoneOTP.verify("9800009999", "123456") is False
