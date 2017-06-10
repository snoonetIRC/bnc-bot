# coding=utf-8
import asyncio
import json
import os
import random
import ssl
from fnmatch import fnmatch
from operator import itemgetter
from typing import List, Optional, Counter, Dict

from bncbot import irc, util


class Conn(asyncio.Protocol):
    def __init__(self, handlers) -> None:
        self.handlers = handlers
        self.futures = {}
        self.locks = {}
        self.loop = asyncio.get_event_loop()
        self._connected_future = self.loop.create_future()
        self._transport = None
        self._protocol = None
        self.bnc_data = {}
        self.stopped_future = self.loop.create_future()
        self.buff = b''
        self.connected = False
        self._has_quit = False
        self.get_users_state = 0
        self.nick = None
        self.config = {}

    def load_config(self) -> None:
        with open('config.json') as f:
            self.config = json.load(f)

    def load_data(self, update: bool = False) -> None:
        self.bnc_data = {}
        if os.path.exists('bnc.json'):
            with open('bnc.json') as f:
                self.bnc_data = json.load(f)
        self.bnc_data.setdefault('queue', {})
        self.bnc_data.setdefault('users', {})
        self.save_data()
        if update and not self.bnc_users:
            asyncio.ensure_future(self.get_user_hosts(), loop=self.loop)

    def save_data(self) -> None:
        with open('bnc.json', 'w') as f:
            json.dump(self.bnc_data, f, indent=2, sort_keys=True)

    def run(self) -> bool:
        self.load_config()
        self.loop.run_until_complete(self.connect())
        self.load_data(True)
        self.start_timers()
        restart = self.loop.run_until_complete(self.stopped_future)
        self.loop.stop()
        return restart

    async def data_check(self) -> None:
        while True:
            await asyncio.sleep(8 * 60 * 60, loop=self.loop)
            await self.get_user_hosts()

    def start_timers(self) -> None:
        asyncio.ensure_future(self.data_check(), loop=self.loop)

    def connection_made(self, transport) -> None:
        self._transport = transport
        self.connected = True
        self._connected_future.set_result(None)
        del self._connected_future

    def connection_lost(self, exc) -> None:
        self._connected_future = self.loop.create_future()
        self.connected = False
        if exc is not None:
            asyncio.ensure_future(self.connect(), loop=self.loop)

    def eof_received(self) -> bool:
        self._connected_future = self.loop.create_future()
        self.connected = False
        asyncio.ensure_future(self.connect(), loop=self.loop)
        return True

    def send(self, *parts) -> None:
        self.loop.call_soon_threadsafe(self._send, ' '.join(parts))

    def _send(self, line: str) -> None:
        print(">>", line)
        asyncio.ensure_future(self._send_final(line), loop=self.loop)

    async def _send_final(self, line: str) -> None:
        if not self.connected:
            await self._connected_future
        line += '\r\n'
        data = line.encode(errors="replace")
        self._transport.write(data)

    def module_msg(self, name: str, cmd: str) -> None:
        self.msg(self.prefix + name, cmd)

    async def get_user_hosts(self) -> None:
        """Should only be run periodically to keep the user list in sync"""
        self.get_users_state = 0
        self.bnc_users.clear()
        self.futures["user_list"] = self.loop.create_future()
        self.send("znc listusers")
        await self.futures["user_list"]
        del self.futures["user_list"]
        for user in self.bnc_users:
            self.futures["bindhost"] = self.loop.create_future()
            self.module_msg("controlpanel", f"Get BindHost {user}")
            self.bnc_users[user] = (await self.futures["bindhost"])
            del self.futures["bindhost"]
        self.save_data()
        self.load_data()
        hosts = list(filter(None, map(itemgetter(0), filter(
            lambda i: i[1] > 1,
            Counter(self.bnc_users.values()).items()
        ))))
        if hosts:
            self.chan_log(
                "WARNING: Duplicate BindHosts found: {}".format(
                    ', '.join(hosts)
                )
            )

    async def connect(self) -> None:
        if self._has_quit:
            self.close()
            return

        if self.connected:
            self._transport.close()

        ctx = None
        if self.config.get('ssl'):
            ctx = ssl.create_default_context()

        self.connected = False
        self._transport, self._protocol = await self.loop.create_connection(
            lambda: self, host=self.config['server'], port=self.config['port'],
            ssl=ctx
        )
        self.send("PASS", self.config['pass'])
        self.send("NICK bnc")
        self.send(
            "USER", self.config.get('user', 'bnc'), "0", "*", ":realname"
        )

    def quit(self, reason: str = None) -> None:
        if not self._has_quit:
            self._has_quit = True
            if reason:
                self.send("QUIT", reason)
            else:
                self.send("QUIT")

    def close(self) -> None:
        self.quit()
        if self.connected:
            self._transport.close()

    async def shutdown(self, restart=False):
        self.close()
        await asyncio.sleep(1, loop=self.loop)
        self.stopped_future.set_result(restart)

    def data_received(self, data: bytes) -> None:
        self.buff += data
        while b'\r\n' in self.buff:
            raw_line, self.buff = self.buff.split(b'\r\n', 1)
            line = raw_line.decode()
            asyncio.ensure_future(self.handle_line(line), loop=self.loop)

    async def handle_line(self, line: str) -> None:
        print(line)
        raw_event = irc.make_event(self, line)
        for handler in self.handlers.get('raw', {}).get('', []):
            await handler(raw_event)

    def is_admin(self, mask: str) -> bool:
        return any(fnmatch(mask.lower(), pat.lower()) for pat in self.admins)

    def add_queue(self, nick: str, registered_time: str) -> None:
        self.bnc_queue[nick] = registered_time
        self.save_data()

    def rem_queue(self, nick: str) -> None:
        if nick in self.bnc_queue:
            del self.bnc_queue[nick]
            self.save_data()

    def chan_log(self, msg: str) -> None:
        if self.log_chan:
            self.msg(self.log_chan, msg)

    def add_user(self, nick: str) -> bool:
        passwd = util.gen_pass()
        try:
            host = self.get_bind_host()
        except ValueError:
            return False
        self.module_msg('controlpanel', f"cloneuser BNCClient {nick}")
        self.module_msg('controlpanel', f"Set Password {nick} {passwd}")
        self.module_msg('controlpanel', f"Set BindHost {nick} {host}")
        self.module_msg('controlpanel', f"Set Nick {nick} {nick}")
        self.module_msg('controlpanel', f"Set AltNick {nick} {nick}_")
        self.module_msg('controlpanel', f"Set Ident {nick} {nick}")
        self.module_msg('controlpanel', f"Set Realname {nick} {nick}")
        self.send('znc saveconfig')
        self.module_msg('controlpanel', f"reconnect {nick} Snoonet")
        self.msg(
            "MemoServ",
            f"SEND {nick} Your BNC auth is Username: {nick} Password: "
            f"{passwd} (Ports: 5457 for SSL - 5456 for NON-SSL) Help: "
            f"/server bnc.snoonet.org 5456 and /PASS {nick}:{passwd}"
        )
        self.bnc_users[nick] = host
        self.save_data()
        return True

    def get_bind_host(self) -> str:
        for _ in range(50):
            host = f"127.0.{random.randint(1, 253)}.{random.randint(1, 253)}"
            if host not in self.bnc_users.values():
                return host
        else:
            self.chan_log(
                "ERROR: get_bind_host() has hit a bindhost collision"
            )
            raise ValueError

    def msg(self, target: str, *messages: str) -> None:
        for message in messages:
            self.send(f"PRIVMSG {target} :{message}")

    @property
    def admins(self) -> List[str]:
        return self.config.get('admins', [])

    @property
    def bnc_queue(self) -> Dict[str, str]:
        return self.bnc_data.setdefault('queue', {})

    @property
    def bnc_users(self) -> Dict[str, str]:
        return self.bnc_data.setdefault('users', {})

    @property
    def prefix(self) -> str:
        return self.config.get('status_prefix', '*')

    @property
    def log_chan(self) -> Optional[str]:
        return self.config.get('log-channel')
