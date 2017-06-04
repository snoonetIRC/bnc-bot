# coding=utf-8
import asyncio
import os
import signal
import sys
import time
from functools import partial

# store the original working directory, for use when restarting
original_wd = os.path.realpath(".")

# set up environment - we need to make sure we are in the install directory
path0 = os.path.realpath(sys.path[0] or '.')
install_dir = os.path.realpath(os.path.dirname(__file__))
if path0 == install_dir:
    sys.path[0] = path0 = os.path.dirname(install_dir)
os.chdir(path0)

from bncbot import bot
from bncbot.conn import Conn


def main():
    with open('.bncbot.pid', 'w') as pid_file:
        pid_file.write(str(os.getpid()))
    conn = Conn(bot.HANDLERS)

    original_sigint = signal.getsignal(signal.SIGINT)

    def handle_sig(sig, frame):
        if sig == signal.SIGINT:
            if conn:
                conn.loop.call_soon_threadsafe(
                    partial(
                        asyncio.ensure_future, conn.shutdown(True),
                        loop=conn.loop
                    )
                )
            signal.signal(signal.SIGINT, original_sigint)

    signal.signal(signal.SIGINT, handle_sig)
    restart = conn.run()
    if restart:
        conn = None
        time.sleep(1)
        os.chdir(original_wd)
        args = sys.argv
        for f in [sys.stdout, sys.stderr]:
            f.flush()
        os.execv(sys.executable, [sys.executable] + args)


if __name__ == "__main__":
    main()
