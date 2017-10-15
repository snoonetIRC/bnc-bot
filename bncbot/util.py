# coding=utf-8
import hashlib
import ipaddress
import random
import secrets
import string

VALID_USER_CHARS = string.ascii_letters + string.digits + "@.-_"
VALID_USER_START_CHARS = string.ascii_letters

BIND_HOST_NET = ipaddress.ip_network("127.0.0.0/16")


def gen_pass(chars: str = (string.ascii_letters + string.digits), length: int = 16) -> str:
    """
    Generate a password
    :param chars: The characters to use for password generation
    :return: The generated password
    """
    return ''.join(secrets.choice(chars) for _ in range(length))


def chunk_str(text, length=256):
    chunks = (text[i:i + length] for i in range(0, len(text), length))
    yield from chunks


def is_username_valid(name: str) -> bool:
    if name[:1] not in VALID_USER_START_CHARS:
        return False

    for c in name:
        if c not in VALID_USER_CHARS:
            return False

    return True


def sanitize_username(user: str) -> str:
    valid = True
    if user[:1] not in VALID_USER_START_CHARS:
        new_user = '-'
        valid = False
    else:
        new_user = user[:1]

    for c in user[1:]:
        if c in VALID_USER_CHARS:
            new_user += c
        else:
            new_user += '-'
            valid = False

    if valid:
        return user

    m = hashlib.md5(user.encode())
    md5hash = int.from_bytes(m.digest(), 'big')
    chars = VALID_USER_CHARS
    out = ""
    while md5hash > len(chars):
        md5hash, rem = divmod(md5hash, len(chars))
        out += chars[rem]

    out += chars[md5hash]
    new_user += '@' + out[:8]
    return new_user


def gen_bindhost():
    size = 2 ** BIND_HOST_NET.prefixlen
    return BIND_HOST_NET[random.randrange(size)]
