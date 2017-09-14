# coding=utf-8
import secrets
import string


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
