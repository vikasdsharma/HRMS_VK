"""Small security helpers: safe redirects, password policy."""
import re
from urllib.parse import urlparse

COMMON_PASSWORDS = {
    "password", "password1", "password12", "password123", "admin123", "admin1234", "admin12345", "qwerty123",
    "qwertyuiop", "1234567890", "12345678910", "welcome123", "letmein123", "iloveyou123", "hrms12345",
}
MIN_LENGTH = 10


def safe_next(target, default):
    """Only allow same-site relative paths ('/employees'). Rejects '//evil', '/\\evil', 'http://...'."""
    if not target or "\\" in target or "\r" in target or "\n" in target:
        return default
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return default
    return target


def password_problem(pw, username=None, old=None):
    """Return a human-readable problem with the password, or None if acceptable."""
    if len(pw or "") < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters"
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Password must contain both letters and digits"
    if pw.lower() in COMMON_PASSWORDS:
        return "That password is too common - choose another"
    if username and username.lower() in pw.lower():
        return "Password must not contain the username"
    if old and pw == old:
        return "New password must be different from the current one"
    return None
