# coding=utf-8
import secrets
import string


def gen_pass(chars: str = (string.ascii_letters + string.digits)) -> str:
    """
    Generate a password
    :param chars: The characters to use for password generation
    :return: The generated password
    """
    return ''.join(secrets.choice(chars) for _ in range(16))
