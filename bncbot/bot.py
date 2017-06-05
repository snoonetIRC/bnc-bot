# coding=utf-8
import asyncio
import re
from collections import namedtuple
from itertools import chain

from bncbot import util
from bncbot.event import CommandEvent, RawEvent

Command = namedtuple('Command', 'name func admin param')

HANDLERS = {}


def raw(*cmds):
    def _decorate(func):
        if not cmds:
            HANDLERS.setdefault('raw', {}).setdefault('', []).append(func)
        for cmd in cmds:
            HANDLERS.setdefault('raw', {}).setdefault(cmd, []).append(func)

    if len(cmds) == 1 and callable(cmds[0]):
        return _decorate(cmds[0])
    return _decorate


def command(name, *aliases, admin=False, require_param=True):
    def _decorate(func):
        cmd = Command(
            name=name, func=func, admin=admin, param=require_param
        )
        HANDLERS.setdefault('command', {}).update({
            alias: cmd for alias in chain((name,), aliases)
        })

    return _decorate


@raw
async def on_raw(event: 'RawEvent'):
    handlers = event.conn.handlers
    for handler in handlers.get('raw', {}).get(event.irc_command, []):
        await handler(event)


@raw('318')
async def on_whois_end(event: 'RawEvent'):
    conn = event.conn
    to_remove = []
    for name, fut in conn.futures.items():
        if name.startswith('whois') and name.endswith(event.irc_paramlist[1]):
            fut.set_result('')
            to_remove.append(name)
    for name in to_remove:
        del conn.futures[name]


@raw('330')
async def on_whois_acct(event: 'RawEvent'):
    params = event.irc_paramlist
    conn = event.conn
    if params[-1] == "is logged in as":
        fut = conn.futures.get("whois_acct_" + params[1])
        if fut:
            fut.set_result(params[2])
            del conn.futures['whois_acct_' + params[1]]


@raw('PING')
async def do_ping(event: 'RawEvent'):
    event.conn.send('PONG', *event.irc_paramlist)


@raw('NOTICE')
async def on_notice(event: 'RawEvent'):
    message = event.irc_paramlist[-1]
    conn = event.conn
    if event.nick.lower() == "nickserv" and ':' in message:
        # Registered: May 30 00:53:54 2017 UTC (5 days, 19 minutes ago)
        message = message.strip()
        part, content = message.split(':', 1)
        content = content.strip()
        if part == "Registered" and 'ns_info' in conn.futures:
            conn.futures['ns_info'].set_result(content)


@raw('PRIVMSG')
async def on_privmsg(event: 'RawEvent'):
    message = event.irc_paramlist[-1]
    conn = event.conn
    if event.nick.startswith(conn.prefix) and event.host == "znc.in":
        znc_module = event.nick[len(conn.prefix):]
        if znc_module == "status" and conn.futures.get('user_list'):
            match = re.match(
                r'^\|\s*(.+?)\s*\|\s*\d+\s*\|\s*\d+\s*\|$', message
            )
            if match:
                user = match.group(1)
                event.bnc_users[user] = None
            elif re.match(r'^[=+]+$', message):
                conn.get_users_state += 1
                if conn.get_users_state == 3:
                    conn.futures['user_list'].set_result(None)
        elif znc_module == "controlpanel" and conn.futures.get('bindhost'):
            if message.startswith("BindHost = "):
                _, _, host = message.partition('=')
                conn.futures['bindhost'].set_result(host.strip())
    elif message[0] == '.':
        cmd, _, text = message[1:].partition(' ')
        cmd_event = CommandEvent(base_event=event, command=cmd, text=text)
        handler: Command = event.conn.handlers.get('command', {}).get(cmd)
        if (handler and (not handler.admin or conn.is_admin(event.mask)) and
                (not handler.param or text)):
            await handler.func(cmd_event)


@raw('NICK')
async def on_nick(event: 'RawEvent'):
    event.conn.nick = event.irc_paramlist[0]


@command("acceptbnc", admin=True)
async def cmd_acceptbnc(event: 'CommandEvent'):
    nick = event.text.split(None, 1)[0]
    if nick not in event.bnc_queue:
        event.message(f"{nick} is not in the BNC queue.")
        return
    event.conn.rem_queue(nick)
    event.conn.add_user(nick)
    event.conn.chan_log(
        f"{nick} has been set with BNC access and memoserved credentials."
    )


@command("denybnc", admin=True)
async def cmd_denybnc(event: 'CommandEvent'):
    nick = event.text.split()[0]
    if nick not in event.bnc_queue:
        event.message(f"{nick} is not in the BNC queue.")
        return
    event.conn.rem_queue(nick)
    event.message(
        f"SEND {nick} Your BNC auth could not be added at this time",
        "MemoServ"
    )
    event.conn.chan_log(f"{nick} has been denied. Memoserv sent.")


@command("bncrefresh", admin=True, require_param=False)
async def cmd_bncrefresh(event: 'CommandEvent'):
    conn = event.conn
    event.message("Updating user list")
    conn.chan_log(f"{event.nick} is updating the BNC user list...")
    await conn.get_user_hosts()
    conn.chan_log("BNC user list updated.")


@command("bncqueue", "bncq", admin=True, require_param=False)
async def cmd_bncqueue(event: 'CommandEvent'):
    if event.bnc_queue:
        for nick, reg_time in event.bnc_queue.items():
            event.message(f"BNC Queue: {nick} Registered {reg_time}")
    else:
        event.message("BNC request queue is empty")


@command("delbnc", admin=True)
async def cmd_delbnc(event: 'CommandEvent'):
    nick = event.text.split()[0]
    conn = event.conn
    if nick not in event.bnc_users:
        event.message(f"{nick} is not a current BNC user")
        return
    conn.module_msg('controlpanel', f"deluser {nick}")
    conn.send("znc saveconfig")
    del event.bnc_users[nick]
    conn.chan_log(f"{event.nick} removed BNC: {nick}")
    if event.chan != conn.log_chan:
        event.message(f"BNC removed")
    conn.save_data()


@command("bncresetpass", admin=True)
async def cmd_resetpass(event: 'CommandEvent'):
    conn = event.conn
    nick = event.text.split()[0]
    if nick not in event.bnc_users:
        event.message(f"{nick} is not a BNC user.")
        return
    passwd = util.gen_pass()
    conn.module_msg('controlpanel', f"Set Password {nick} {passwd}")
    conn.send("znc saveconfig")
    event.message(f"BNC password reset for {nick}")
    event.message(
        f"SEND {nick} [New Password!] Your BNC auth is Username: {nick} "
        f"Password: {passwd} (Ports: 5457 for SSL - 5456 for NON-SSL) "
        f"Help: /server bnc.snoonet.org 5456 and /PASS {nick}:{passwd}",
        "MemoServ"
    )


@command("requestbnc", "bncrequest", require_param=False)
async def cmd_requestbnc(event: 'CommandEvent'):
    nick = event.nick
    conn = event.conn
    conn.futures['whois_acct_' + nick] = event.loop.create_future()
    conn.send("WHOIS", nick)
    acct = await conn.futures['whois_acct_' + nick]
    if not acct:
        event.message(
            f"You must be identified with services to request a BNC account",
            nick
        )
        return
    if acct in event.bnc_users:
        event.message(
            "It appears you already have a BNC account. If this is in error, "
            "please contact staff in #help",
            nick
        )
        return
    if 'ns_info' not in conn.locks:
        conn.locks['ns_info'] = asyncio.Lock(loop=event.loop)
    async with conn.locks['ns_info']:
        conn.futures['ns_info'] = event.loop.create_future()
        event.message(f"INFO {acct}", "NickServ")
        registered_time = await conn.futures['ns_info']
        del conn.futures['ns_info']
    conn.add_queue(acct, registered_time)
    event.message("BNC request submitted.", nick)
    conn.chan_log(
        f"{acct} added to bnc queue. Registered {registered_time}"
    )
