# coding=utf-8
import re
from typing import Tuple, Optional, List, TYPE_CHECKING

from bncbot.event import RawEvent

if TYPE_CHECKING:
    from bncbot.conn import Conn

CMD_PARAMS = {
    'PRIVMSG': ('chan', 'msg'),
    'NOTICE': ('chan', 'msg'),
}

ParsedLine = Tuple[Optional[str], Optional[str], str, List[str]]


def parse(line: str) -> ParsedLine:
    tags = None
    prefix = None
    if line[0] == '@':
        tags, line = line[1:].split(None, 1)
    if line[0] == ':':
        prefix, line = line[1:].split(None, 1)
    params = line
    trail = None
    if ' :' in params:
        params, trail = params.split(' :', 1)
    params = params.split()
    if trail:
        params.append(trail)
    cmd = params.pop(0)
    return tags, prefix, cmd, params


def make_event(conn: 'Conn', line: str) -> RawEvent:
    tags, prefix, cmd, params = parse(line)
    nick = None
    user = None
    host = None
    if prefix:
        match = re.match(r'^(.+?)(?:!(.+?))?(?:@(.+?))?$', prefix)
        if match:
            nick, user, host = match.groups()

    chan: Optional[str] = None
    if cmd in CMD_PARAMS and 'chan' in CMD_PARAMS[cmd]:
        chan = params[CMD_PARAMS[cmd].index('chan')]
        if chan == conn.nick:
            chan = nick

    return RawEvent(
        conn=conn, nick=nick, user=user, host=host, mask=prefix, chan=chan,
        irc_rawline=line, irc_command=cmd, irc_paramlist=params
    )
