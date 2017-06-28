# coding=utf-8
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncirc.irc import ParamList, Message
    from bncbot.conn import Conn


class Event:
    def __init__(self, *, conn: 'Conn' = None, base_event: 'Event' = None,
                 nick: str = None, user: str = None, host: str = None,
                 mask: str = None, chan: str = None) -> None:
        if base_event:
            self.conn = base_event.conn
            self.nick = base_event.nick
            self.user = base_event.user
            self.host = base_event.host
            self.mask = base_event.mask
            self.chan = base_event.chan
        else:
            self.conn = conn
            self.nick = nick
            self.user = user
            self.host = host
            self.mask = mask
            self.chan = chan

    def message(self, message: str, target: str = None) -> None:
        if not target:
            assert self.chan
            target = self.chan
        self.conn.msg(target, message)

    @property
    def bnc_data(self):
        return self.conn.bnc_data

    @property
    def bnc_queue(self):
        return self.conn.bnc_queue

    @property
    def bnc_users(self):
        return self.conn.bnc_users

    @property
    def event(self):
        return self

    @property
    def loop(self):
        return self.conn.loop


class RawEvent(Event):
    def __init__(self, *, conn: 'Conn' = None, base_event=None,
                 nick: str = None, user: str = None, host: str = None,
                 mask: str = None, chan: str = None, irc_rawline: 'Message' = None,
                 irc_command: str = None, irc_paramlist: 'ParamList' = None) -> None:
        super().__init__(
            conn=conn, base_event=base_event, nick=nick, user=user, host=host,
            mask=mask, chan=chan
        )
        self.irc_rawline = irc_rawline
        self.irc_command = irc_command
        self.irc_paramlist = irc_paramlist


class CommandEvent(Event):
    def __init__(self, *, conn: 'Conn' = None, base_event=None,
                 nick: str = None, user: str = None, host: str = None,
                 mask: str = None, chan: str = None, command: str,
                 text: str = None) -> None:
        super().__init__(
            conn=conn, base_event=base_event, nick=nick, user=user, host=host,
            mask=mask, chan=chan
        )
        self.command = command
        self.text = text
