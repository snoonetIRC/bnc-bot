# coding=utf-8
import asyncio
import json
import os
import random
import re
import ssl
from collections import namedtuple
from fnmatch import fnmatch

Command = namedtuple('Command', 'name handler admin')


class Conn(asyncio.Protocol):
    def __init__(self):
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
        self.commands = {}
        self.config = {}
        self.register_cmd(self.request, "requestbnc")
        self.register_cmd(self.accept, "acceptbnc", admin=True)
        self.register_cmd(self.deny, "denybnc", admin=True)
        self.register_cmd(self.list_queue, "bncq", "bncqueue", admin=True)
        self.register_cmd(self.add_bnc, "addbnc", admin=True)
        self.register_cmd(self.del_bnc, "delbnc", admin=True)
        self.register_cmd(self.update_users, "bncrefresh", admin=True)

    @property
    def admins(self):
        return self.config.get('admins', [])

    def load_config(self):
        with open('config.json') as f:
            self.config = json.load(f)

    def run(self):
        self.load_config()
        self.loop.run_until_complete(self.connect())
        self.load_data(True)
        self.start_timers()
        restart = self.loop.run_until_complete(self.stopped_future)
        self.loop.stop()
        return restart

    async def data_check(self):
        while True:
            await asyncio.sleep(8 * 60 * 60, loop=self.loop)
            await self.get_user_hosts()

    def start_timers(self):
        asyncio.ensure_future(self.data_check(), loop=self.loop)

    def connection_made(self, transport):
        self._transport = transport
        self.connected = True
        self._connected_future.set_result(None)
        del self._connected_future

    def connection_lost(self, exc):
        self.connected = False
        self._connected_future = self.loop.create_future()
        if exc is not None:
            asyncio.ensure_future(self.connect(), loop=self.loop)

    def eof_received(self):
        self.connected = False
        self._connected_future = self.loop.create_future()
        asyncio.ensure_future(self.connect(), loop=self.loop)
        return True

    def send(self, *parts):
        self.loop.call_soon_threadsafe(self._send, *parts)

    def _send(self, *parts):
        print(">>", *parts)
        asyncio.ensure_future(self._send_final(*parts), loop=self.loop)

    async def _send_final(self, *parts):
        if not self.connected:
            await self._connected_future
        line = ' '.join(parts)
        line += '\r\n'
        data = line.encode(errors="replace")
        self._transport.write(data)

    def module_msg(self, name, cmd):
        self.send(
            "PRIVMSG", self.config.get('status_prefix', '*') + name,
            ":{}".format(cmd)
        )

    async def get_user_hosts(self):
        """Should only be run periodically to keep the user list in sync"""
        self.get_users_state = 0
        self.bnc_data['users'] = {}
        self.futures["user_list"] = self.loop.create_future()
        self.send("znc listusers")
        await self.futures["user_list"]
        del self.futures["user_list"]
        for user in self.bnc_data['users']:
            self.futures["bindhost"] = self.loop.create_future()
            self.module_msg("controlpanel", f"Get BindHost {user}")
            self.bnc_data['users'][user] = (await self.futures["bindhost"])
            del self.futures["bindhost"]
        self.save_data()
        self.load_data()

    def load_data(self, update=False):
        self.bnc_data = {}
        if os.path.exists('bnc.json'):
            with open('bnc.json') as f:
                self.bnc_data = json.load(f)
        self.bnc_data.setdefault('queue', {})
        self.bnc_data.setdefault('users', {})
        self.save_data()
        if update and not self.bnc_data.get('users'):
            asyncio.ensure_future(self.get_user_hosts(), loop=self.loop)

    def save_data(self):
        with open('bnc.json', 'w') as f:
            json.dump(self.bnc_data, f, indent=2, sort_keys=True)

    def register_cmd(self, handler, *names, admin=False):
        for name in names:
            self.commands[name] = Command(name, handler, admin)

    async def connect(self):
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

    def quit(self, reason=None):
        if not self._has_quit:
            self._has_quit = True
            if reason:
                self.send("QUIT", reason)
            else:
                self.send("QUIT", reason)

    def close(self):
        self.quit()
        if self.connected:
            self._transport.close()
            self.connected = False

    def data_received(self, data):
        self.buff += data
        while b'\r\n' in self.buff:
            raw_line, self.buff = self.buff.split(b'\r\n', 1)
            line = raw_line.decode()
            print(line)
            tags, prefix, command, params = self.parse(line)
            asyncio.ensure_future(
                self.handle_line(tags, prefix, command, params),
                loop=self.loop
            )

    def parse(self, line):
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
        command = params.pop(0)
        return tags, prefix, command, params

    @property
    def prefix(self):
        return self.config.get('status_prefix', '*')

    async def handle_line(self, tags, prefix, command, params):
        nick = None
        user = None
        host = None
        if prefix:
            match = re.match(r'^(.+?)(?:!(.+?))?(?:@(.+?))?$', prefix)
            if match:
                nick, user, host = match.groups()

        if command == 'PING':
            self.send('PONG', *params)
        elif command == 'NICK':
            self.nick = params[0]
        elif command == 'PRIVMSG':
            chan = params[0]
            if chan == self.nick:
                chan = nick
            message = params[-1]
            if nick == (self.prefix + "status") and \
                    self.futures.get('user_list'):
                match = re.match(
                    r'^\|\s*(.+?)\s*\|\s*\d+\s*\|\s*\d+\s*\|$', message
                )
                if match:
                    user = match.group(1)
                    self.bnc_data['users'][user] = None
                elif re.match(r'^[=+]+$', message):
                    self.get_users_state += 1
                    if self.get_users_state == 3:
                        self.futures['user_list'].set_result(None)
            elif nick == (self.prefix + "controlpanel") and \
                    self.futures.get('bindhost'):
                if message.startswith("BindHost = "):
                    _, _, host = message.partition('=')
                    self.futures['bindhost'].set_result(host.strip())
            elif message[0] == '.':
                cmd, _, args = message[1:].partition(' ')
                await self.handle_command(prefix, cmd, args)
        elif command == 'NOTICE':
            chan = params[0]
            if chan == self.nick:
                chan = nick
            message = params[-1]
            if nick == "NickServ" and ':' in message:
                message = message.strip()
                # Registered: May 30 00:53:54 2017 UTC (5 days, 19 minutes ago)
                part, content = message.split(':', 1)
                content = content.strip()
                if part == "Registered" and 'ns_info' in self.futures:
                    self.futures['ns_info'].set_result(content)
        elif command == '330' and params[-1] == "is logged in as":
            fut = self.futures.get("whois_acct_" + params[1])
            if fut:
                fut.set_result(params[2])
                del self.futures['whois_acct_' + params[1]]
        elif command == '318':
            to_remove = []
            for name, fut in self.futures.items():
                if name.startswith('whois') and name.endswith(params[1]):
                    fut.set_result('')
                    to_remove.append(name)
            for name in to_remove:
                del self.futures[name]

    def is_admin(self, mask):
        return any(fnmatch(mask.lower(), pat.lower()) for pat in self.admins)

    async def handle_command(self, prefix, name, args):
        cmd = self.commands[name]
        if cmd:
            if (not cmd.admin) or self.is_admin(prefix):
                await cmd.handler(prefix, args)

    async def request(self, mask, text):
        nick = mask.split('!', 1)[0]
        self.futures['whois_acct_' + nick] = self.loop.create_future()
        self.send("WHOIS", nick)
        acct = await self.futures['whois_acct_' + nick]
        if not acct:
            self.send(
                f"PRIVMSG {nick} :You must be identified with services to "
                f"request a BNC account"
            )
            return
        if acct in self.bnc_data.get('users', []):
            self.send(
                f"PRIVMSG {nick} :It appears you already have a BNC account. "
                f"If this is in error, please contact staff in #help"
            )
            return
        if 'ns_info' not in self.locks:
            self.locks['ns_info'] = asyncio.Lock(loop=self.loop)
        async with self.locks['ns_info']:
            self.futures['ns_info'] = self.loop.create_future()
            self.send("NS INFO {}".format(acct))
            registered_time = await self.futures['ns_info']
            del self.futures['ns_info']
        self.add_queue(acct, registered_time)
        self.send(f"PRIVMSG {nick} :Request submitted.")
        self.chan_log(
            f"{acct} added to bnc queue. Registered {registered_time}"
        )

    def add_queue(self, nick, registered_time):
        self.bnc_data.setdefault('queue', {})[nick] = registered_time
        self.save_data()

    def rem_queue(self, nick):
        if nick in self.bnc_data['queue']:
            del self.bnc_data['queue'][nick]
        self.save_data()

    async def accept(self, mask, text):
        if text:
            nick = text.strip().split(None, 1)[0]
            if nick in self.bnc_data['queue']:
                self.rem_queue(nick)
                self.add_user(nick)
                self.chan_log(
                    f"{nick} has been set with BNC access and "
                    f"memoserved credentials."
                )
            else:
                self.chan_log(f"{nick} is not in the BNC queue.")

    def chan_log(self, msg):
        self.send(f"PRIVMSG {self.config['log-channel']} :{msg}")

    def add_user(self, nick):
        passwd = self.gen_user_pass()
        host = self.get_bind_host()
        self.module_msg('controlpanel', f"cloneuser BNCClient {nick}")
        self.module_msg('controlpanel', f"Set Password {nick} {passwd}")
        self.module_msg('controlpanel', f"Set BindHost {nick} {host}")
        self.module_msg('controlpanel', f"Set Nick {nick} {nick}")
        self.module_msg('controlpanel', f"Set AltNick {nick} {nick}_")
        self.module_msg('controlpanel', f"Set Ident {nick} {nick}")
        self.module_msg('controlpanel', f"Set Realname {nick} {nick}")
        self.send('znc saveconfig')
        self.module_msg('controlpanel', f"reconnect {nick} Snoonet")
        self.send(
            f"MS Send {nick} Your BNC auth is Username: {nick} Password: "
            f"{passwd} (Ports: 5457 for SSL - 5456 for NON-SSL) Help: "
            f"/server bnc.snoonet.org 5456 and /PASS {nick}:{passwd}"
        )
        self.bnc_data.setdefault('users', {})[nick] = host
        self.save_data()

    async def deny(self, mask, text):
        if text:
            nick = text.strip().split()[0]
            if nick not in self.bnc_data['queue']:
                self.chan_log(f"{nick} is not in the BNC queue.")
                return
            self.rem_queue(nick)
            self.send(
                f"MS Send {nick} Your BNC auth could not be added "
                f"at this time."
            )
            self.chan_log(f"{nick} has been denied. Memoserv sent.")

    async def update_users(self, mask, text):
        await self.get_user_hosts()

    async def list_queue(self, mask, text):
        if self.bnc_data.get('queue'):
            for nick, reg_time in self.bnc_data['queue'].items():
                self.chan_log(f"BNC Queue: {nick} Registered {reg_time}")

    async def add_bnc(self, mask, text):
        pass

    async def del_bnc(self, mask, text):
        if not text:
            return
        nick = text.strip().split()[0]
        if nick not in self.bnc_data['users']:
            self.chan_log(f"{nick} is not a current BNC user")
            return
        self.module_msg('controlpanel', f"deluser {nick}")
        del self.bnc_data['users'][nick]
        self.chan_log(f"{mask} Removed BNC: {nick}")
        self.save_data()

    def get_bind_host(self):
        while True:
            host = f"127.0.{random.randint(1, 253)}.{random.randint(1, 253)}"
            print(host)
            if host not in self.bnc_data['users'].values():
                return host

    def gen_user_pass(self):
        chars = list(map(chr, range(33, 127)))
        return ''.join(random.choice(chars) for _ in range(16))


if __name__ == "__main__":
    conn = Conn()
    conn.run()
