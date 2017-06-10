# coding=utf-8
import asyncio
import re
from itertools import chain
from typing import NamedTuple, Callable, TYPE_CHECKING, List

from bncbot import util
from bncbot.event import CommandEvent, RawEvent

if TYPE_CHECKING:
    from bncbot.conn import Conn


class Command(NamedTuple):
    name: str
    func: Callable
    admin: bool = False
    param: bool = True


HANDLERS = {}


def raw(*cmds):
    def _decorate(func):
        for cmd in (cmds or ('',)):
            HANDLERS.setdefault('raw', {}).setdefault(cmd, []).append(func)

    cmds = list(cmds)
    if len(cmds) == 1 and callable(cmds[0]):
        return _decorate(cmds.pop())
    return _decorate


def command(name, *aliases, admin=False, require_param=True):
    def _decorate(func):
        cmd = Command(name, func, admin, require_param)
        HANDLERS.setdefault('command', {}).update({
            alias: cmd for alias in chain((name,), aliases)
        })

    return _decorate


@raw
async def on_raw(conn: 'Conn', event: 'RawEvent', irc_command: str):
    for handler in conn.handlers.get('raw', {}).get(irc_command, []):
        await conn.launch_hook(event, handler)


@raw('318')
async def on_whois_end(conn: 'Conn', irc_paramlist: List[str]):
    to_remove = []
    for name, fut in conn.futures.items():
        if name.startswith('whois') and name.endswith(irc_paramlist[1]):
            fut.set_result('')
            to_remove.append(name)
    for name in to_remove:
        del conn.futures[name]


@raw('330')
async def on_whois_acct(conn: 'Conn', irc_paramlist: List[str]):
    if irc_paramlist[-1] == "is logged in as":
        fut = conn.futures.get("whois_acct_" + irc_paramlist[1])
        if fut:
            fut.set_result(irc_paramlist[2])
            del conn.futures['whois_acct_' + irc_paramlist[1]]


@raw('PING')
async def do_ping(irc_paramlist: List[str], conn: 'Conn'):
    conn.send('PONG', *irc_paramlist)


@raw('NOTICE')
async def on_notice(irc_paramlist: List[str], conn: 'Conn', nick: str):
    message = irc_paramlist[-1]
    if nick.lower() == "nickserv" and ':' in message:
        # Registered: May 30 00:53:54 2017 UTC (5 days, 19 minutes ago)
        message = message.strip()
        part, content = message.split(':', 1)
        content = content.strip()
        if part == "Registered" and 'ns_info' in conn.futures:
            conn.futures['ns_info'].set_result(content)


@raw('PRIVMSG')
async def on_privmsg(event: 'RawEvent', irc_paramlist: List[str], conn: 'Conn',
                     nick: str, host: str, bnc_users, mask: str):
    message = irc_paramlist[-1]
    if nick.startswith(conn.prefix) and host == "znc.in":
        znc_module = nick[len(conn.prefix):]
        if znc_module == "status" and conn.futures.get('user_list'):
            match = re.match(
                r'^\|\s*(.+?)\s*\|\s*\d+\s*\|\s*\d+\s*\|$', message
            )
            if match:
                user = match.group(1)
                bnc_users[user] = None
            elif re.match(r'^[=+]+$', message):
                conn.get_users_state += 1
                if conn.get_users_state == 3:
                    conn.futures['user_list'].set_result(None)
        elif znc_module == "controlpanel" and conn.futures.get('bindhost'):
            if message.startswith("BindHost = "):
                _, _, host = message.partition('=')
                conn.futures['bindhost'].set_result(host.strip())
    elif message[0] in conn.cmd_prefix:
        cmd, _, text = message[1:].partition(' ')
        cmd_event = CommandEvent(base_event=event, command=cmd, text=text)
        handler: Command = conn.handlers.get('command', {}).get(cmd)
        if (handler and (not handler.admin or conn.is_admin(mask)) and
                (not handler.param or text)):
            await conn.launch_hook(cmd_event, handler.func)


@raw('NICK')
async def on_nick(conn: 'Conn', irc_paramlist: List[str]):
    conn.nick = irc_paramlist[0]


@command("acceptbnc", admin=True)
async def cmd_acceptbnc(text: str, conn: 'Conn', bnc_queue, message):
    nick = text.split(None, 1)[0]
    if nick not in bnc_queue:
        message(f"{nick} is not in the BNC queue.")
        return
    conn.rem_queue(nick)
    if conn.add_user(nick):
        conn.chan_log(
            f"{nick} has been set with BNC access and memoserved credentials."
        )
    else:
        conn.chan_log(
            f"Error occurred when attempting to add {nick} to the BNC"
        )


@command("denybnc", admin=True)
async def cmd_denybnc(text: str, message, bnc_queue, conn: 'Conn'):
    nick = text.split()[0]
    if nick not in bnc_queue:
        message(f"{nick} is not in the BNC queue.")
        return
    conn.rem_queue(nick)
    message(
        f"SEND {nick} Your BNC auth could not be added at this time",
        "MemoServ"
    )
    conn.chan_log(f"{nick} has been denied. Memoserv sent.")


@command("bncrefresh", admin=True, require_param=False)
async def cmd_bncrefresh(conn: 'Conn', message, nick: str):
    message("Updating user list")
    conn.chan_log(f"{nick} is updating the BNC user list...")
    await conn.get_user_hosts()
    conn.chan_log("BNC user list updated.")


@command("bncqueue", "bncq", admin=True, require_param=False)
async def cmd_bncqueue(bnc_queue, message):
    if bnc_queue:
        for nick, reg_time in bnc_queue.items():
            message(f"BNC Queue: {nick} Registered {reg_time}")
    else:
        message("BNC request queue is empty")


@command("delbnc", admin=True)
async def cmd_delbnc(text: str, conn: 'Conn', bnc_users, chan: str, message,
                     nick: str):
    acct = text.split()[0]
    if acct not in bnc_users:
        message(f"{acct} is not a current BNC user")
        return
    conn.module_msg('controlpanel', f"deluser {acct}")
    conn.send("znc saveconfig")
    del bnc_users[acct]
    conn.chan_log(f"{nick} removed BNC: {acct}")
    if chan != conn.log_chan:
        message(f"BNC removed")
    conn.save_data()


@command("bncresetpass", admin=True)
async def cmd_resetpass(conn: 'Conn', text: str, bnc_users, message):
    nick = text.split()[0]
    if nick not in bnc_users:
        message(f"{nick} is not a BNC user.")
        return
    passwd = util.gen_pass()
    conn.module_msg('controlpanel', f"Set Password {nick} {passwd}")
    conn.send("znc saveconfig")
    message(f"BNC password reset for {nick}")
    message(
        f"SEND {nick} [New Password!] Your BNC auth is Username: {nick} "
        f"Password: {passwd} (Ports: 5457 for SSL - 5456 for NON-SSL) "
        f"Help: /server bnc.snoonet.org 5456 and /PASS {nick}:{passwd}",
        "MemoServ"
    )


@command("addbnc", "bncadd", admin=True)
async def cmd_addbnc(text: str, conn: 'Conn', bnc_users, message):
    acct = text.split()[0]
    if acct in bnc_users:
        message("A BNC account with that name already exists")
    else:
        if conn.add_user(acct):
            conn.chan_log(
                f"{acct} has been set with BNC access and memoserved credentials."
            )
        else:
            conn.chan_log(
                f"Error occurred when attempting to add {acct} to the BNC"
            )


@command("bncsetadmin", admin=True)
def cmd_setadmin(text: str, bnc_users, message, conn: 'Conn'):
    acct = text.split()[0]
    if acct in bnc_users:
        conn.module_msg('controlpanel', f"Set Admin {acct} true")
        conn.send("znc saveconfig")
        message(f"{acct} has been set as a BNC admin")
    else:
        message(f"{acct} does not exist as a BNC account")


@command("requestbnc", "bncrequest", require_param=False)
async def cmd_requestbnc(nick: str, conn: 'Conn', message, bnc_users, loop):
    conn.futures['whois_acct_' + nick] = loop.create_future()
    conn.send("WHOIS", nick)
    acct = await conn.futures['whois_acct_' + nick]
    if not acct:
        message(
            f"You must be identified with services to request a BNC account",
            nick
        )
        return
    if acct in bnc_users:
        message(
            "It appears you already have a BNC account. If this is in error, "
            "please contact staff in #help",
            nick
        )
        return
    if 'ns_info' not in conn.locks:
        conn.locks['ns_info'] = asyncio.Lock(loop=loop)
    async with conn.locks['ns_info']:
        conn.futures['ns_info'] = loop.create_future()
        message(f"INFO {acct}", "NickServ")
        registered_time = await conn.futures['ns_info']
        del conn.futures['ns_info']
    conn.add_queue(acct, registered_time)
    message("BNC request submitted.", nick)
    conn.chan_log(
        f"{acct} added to bnc queue. Registered {registered_time}"
    )
