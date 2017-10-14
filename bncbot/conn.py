# coding=utf-8
import asyncio
import inspect
import json
import logging
import logging.config
from collections import defaultdict
from datetime import timedelta
from fnmatch import fnmatch
from operator import itemgetter
from pathlib import Path
from typing import List, Optional, Counter, Dict, TYPE_CHECKING

from asyncirc.protocol import IrcProtocol
from asyncirc.server import Server

from bncbot import irc, util
from bncbot.async_util import call_func

if TYPE_CHECKING:
    from asyncirc.irc import Message


class Conn:
    def __init__(self, handlers) -> None:
        self.run_dir = Path().resolve()
        self._protocol = None
        self.handlers = handlers
        self.futures = {}
        self.locks = defaultdict(asyncio.Lock)
        self.loop = asyncio.get_event_loop()
        self.bnc_data = {}
        self.stopped_future = self.loop.create_future()
        self.get_users_state = 0
        self.config = {}
        if not self.log_dir.exists():
            self.log_dir.mkdir()

        logging.config.dictConfig({
            "version": 1,
            "formatters": {
                "brief": {
                    "format": "[%(asctime)s] [%(levelname)s] %(message)s",
                    "datefmt": "%H:%M:%S"
                },
                "full": {
                    "format": "[%(asctime)s] [%(levelname)s] %(message)s",
                    "datefmt": "%Y-%m-%d][%H:%M:%S"
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "brief",
                    "level": "DEBUG",
                    "stream": "ext://sys.stdout"
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "maxBytes": 1000000,
                    "backupCount": 5,
                    "formatter": "full",
                    "level": "INFO",
                    "encoding": "utf-8",
                    "filename": self.log_dir / "bot.log"
                },
                "debug_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "maxBytes": 1000000,
                    "backupCount": 5,
                    "formatter": "full",
                    "encoding": "utf-8",
                    "level": "DEBUG",
                    "filename": self.log_dir / "debug.log"
                }
            },
            "loggers": {
                "bncbot": {
                    "level": "DEBUG",
                    "handlers": ["console", "file"]
                },
                "asyncio": {
                    "level": "DEBUG",
                    "handlers": ["console", "debug_file"]
                }
            }
        })
        self.logger = logging.getLogger("bncbot")

    def load_config(self) -> None:
        with self.config_file.open(encoding='utf8') as f:
            self.config = json.load(f)

    def load_data(self, update: bool = False) -> None:
        """Load cached BNC information from the file"""
        self.bnc_data = {}
        if self.data_file.exists():
            with self.data_file.open(encoding='utf8') as f:
                self.bnc_data = json.load(f)

        self.bnc_data.setdefault('queue', {})
        self.bnc_data.setdefault('users', {})
        self.save_data()
        if update and not self.bnc_users:
            asyncio.ensure_future(self.get_user_hosts(), loop=self.loop)

    def save_data(self) -> None:
        with self.data_file.open('w', encoding='utf8') as f:
            json.dump(self.bnc_data, f, indent=2, sort_keys=True)

    def run(self) -> bool:
        self.load_config()
        self.loop.run_until_complete(self.connect())
        self.load_data(True)
        self.start_timers()
        restart = self.loop.run_until_complete(self.stopped_future)
        self.loop.stop()
        return restart

    async def timer(self, interval, func, *args, initial_interval=None):
        if initial_interval is None:
            initial_interval = interval

        if isinstance(interval, timedelta):
            interval = interval.total_seconds()

        if isinstance(initial_interval, timedelta):
            initial_interval = initial_interval.total_seconds()

        await asyncio.sleep(initial_interval)
        while True:
            await call_func(func, *args)
            await asyncio.sleep(interval)

    def create_timer(self, interval, func, *args, initial_interval=None):
        asyncio.ensure_future(self.timer(interval, func, *args, initial_interval=initial_interval), loop=self.loop)

    def start_timers(self) -> None:
        self.create_timer(timedelta(hours=8), self.get_user_hosts)

    def send(self, *parts) -> None:
        self._protocol.send(' '.join(parts))

    def module_msg(self, name: str, cmd: str) -> None:
        self.msg(self.prefix + name, cmd)

    async def get_user_hosts(self) -> None:
        """Should only be run periodically to keep the user list in sync"""
        self.get_users_state = 0
        self.bnc_users.clear()
        user_list_fut = self.loop.create_future()
        self.futures["user_list"] = user_list_fut
        self.send("znc listusers")
        await user_list_fut
        del self.futures["user_list"]

        for user in self.bnc_users:
            bindhost_fut = self.loop.create_future()
            self.futures["bindhost"] = bindhost_fut
            self.module_msg("controlpanel", f"Get BindHost {user}")
            self.bnc_users[user] = bindhost_fut
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
        servers = [
            Server(
                self.config['server'], self.config['port'], self.config.get('ssl', False), self.config['pass']
            )
        ]
        self._protocol = IrcProtocol(
            servers, "bnc", user=self.config['user'], loop=self.loop, logger=self.logger
        )
        self._protocol.register('*', self.handle_line)
        await self._protocol.connect()

    def close(self) -> None:
        self._protocol.quit()

    async def shutdown(self, restart=False):
        self.close()
        await asyncio.sleep(1, loop=self.loop)
        self.stopped_future.set_result(restart)

    async def handle_line(self, proto: 'IrcProtocol', line: 'Message') -> None:
        raw_event = irc.make_event(self, line, proto)
        for handler in self.handlers.get('raw', {}).get('', []):
            await self.launch_hook(raw_event, handler)

    async def launch_hook(self, event, func) -> bool:
        try:
            params = [
                getattr(event, name)
                for name in inspect.signature(func).parameters.keys()
            ]
            await call_func(func, *params)
        except Exception as e:
            self.logger.exception("Error occurred in hook")
            self.chan_log(f"Error occurred in hook {func.__name__} '{type(e).__name__}: {e}'")
            return False
        else:
            return True

    def is_admin(self, mask: str) -> bool:
        return any(fnmatch(mask.lower(), pat.lower()) for pat in self.admins)

    async def is_bnc_admin(self, name) -> bool:
        lock = self.locks["controlpanel_bncadmin"]
        async with lock:
            fut = self.futures.setdefault("bncadmin", self.loop.create_future())
            self.module_msg("controlpanel", "Get Admin {}".format(name))
            res = await fut
            del self.futures["bncadmin"]

        return res

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
        if not util.is_username_valid(nick):
            username = util.sanitize_username(nick)
            self.chan_log(f"WARNING: Invalid username '{nick}'; sanitizing to {username}")
        else:
            username = nick

        passwd = util.gen_pass()
        try:
            host = self.get_bind_host()
        except ValueError:
            return False

        self.module_msg('controlpanel', f"cloneuser BNCClient {username}")
        self.module_msg('controlpanel', f"Set Password {username} {passwd}")
        self.module_msg('controlpanel', f"Set BindHost {username} {host}")
        self.module_msg('controlpanel', f"Set Nick {username} {nick}")
        self.module_msg('controlpanel', f"Set AltNick {username} {nick}_")
        self.module_msg('controlpanel', f"Set Ident {username} {nick}")
        self.module_msg('controlpanel', f"Set Realname {username} {nick}")
        self.send('znc saveconfig')
        self.module_msg('controlpanel', f"reconnect {username} Snoonet")
        self.msg(
            "MemoServ",
            f"SEND {nick} Your BNC auth is Username: {username} Password: "
            f"{passwd} (Ports: 5457 for SSL - 5456 for NON-SSL) Help: "
            f"/server bnc.snoonet.org 5456 and /PASS {username}:{passwd}"
        )
        self.bnc_users[username] = host
        self.save_data()
        return True

    def get_bind_host(self) -> str:
        for _ in range(50):
            host = str(util.gen_bindhost())
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

    def notice(self, target: str, *messages: str) -> None:
        for message in messages:
            self.send(f"NOTICE {target} :{message}")

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
    def cmd_prefix(self):
        return self.config.get('command_prefix', '.')

    @property
    def log_chan(self) -> Optional[str]:
        return self.config.get('log_channel')

    @property
    def log_dir(self):
        return self.run_dir / "logs"

    @property
    def data_file(self):
        return self.run_dir / "bnc.json"

    @property
    def config_file(self):
        return self.run_dir / "config.json"

    @property
    def nick(self) -> str:
        return self._protocol.nick

    @nick.setter
    def nick(self, value: str) -> None:
        self._protocol.nick = value
