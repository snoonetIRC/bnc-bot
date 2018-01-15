# coding=utf-8
import hashlib
import random
import secrets
import string
from ipaddress import IPv4Address, IPv6Address, IPv4Network, IPv6Network
from typing import Union

VALID_USER_CHARS = string.ascii_letters + string.digits + "@.-_"
VALID_USER_START_CHARS = string.ascii_letters

IPNetwork = Union[IPv4Network, IPv6Network]
IPAddress = Union[IPv4Address, IPv6Address]


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
    start = user[:1]
    # Username must start with a letter
    if start not in VALID_USER_START_CHARS:
        new_user = 'a'
        valid = False
    else:
        new_user = start

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
    size = len(chars)
    while md5hash > size:
        md5hash, rem = divmod(md5hash, size)
        out += chars[rem]

    out += chars[md5hash]
    new_user += '@' + out[:8]
    return new_user


def get_random_address(net: IPNetwork) -> IPAddress:
    return net[random.randrange(net.num_addresses)]
