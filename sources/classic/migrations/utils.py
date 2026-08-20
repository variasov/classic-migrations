import random
import re
import string
import unicodedata


def get_random_string(length, chars=(string.ascii_letters + string.digits)):
    """
    Return a random string of ``length`` characters
    """
    rng = random.SystemRandom()
    return "".join(rng.choice(chars) for i in range(length))


def unidecode(s: str) -> str:
    """
    Return ``s`` with unicode diacritics removed.
    """
    combining = unicodedata.combining
    return "".join(c for c in unicodedata.normalize("NFD", s) if not combining(c))


def slugify(message):
    s = unidecode(message)
    s = re.sub(re.compile(r"[^-a-z0-9]+"), "-", s.lower())
    s = re.compile(r"-{2,}").sub("-", s).strip("-")
    return s
