# coding=utf-8
from typing import Optional, TYPE_CHECKING

from bncbot.event import RawEvent

if TYPE_CHECKING:
    from asyncirc.protocol import IrcProtocol
    from asyncirc.irc import Message
    from bncbot.conn import Conn

CMD_PARAMS = {
    'PRIVMSG': ('chan', 'msg'),
    'NOTICE': ('chan', 'msg'),
}


def make_event(conn: 'Conn', line: 'Message', proto: 'IrcProtocol') -> RawEvent:
    cmd = line.command
    params = line.parameters
    nick = line.prefix.nick
    chan: Optional[str] = None
    if cmd in CMD_PARAMS and 'chan' in CMD_PARAMS[cmd]:
        chan = params[CMD_PARAMS[cmd].index('chan')]
        if chan == conn.nick:
            chan = nick

    return RawEvent(
        conn=conn, nick=nick, user=line.prefix.user, host=line.prefix.host, mask=line.prefix.mask, chan=chan,
        irc_rawline=line, irc_command=cmd, irc_paramlist=params
    )
