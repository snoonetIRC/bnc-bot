# coding=utf-8
from bncbot import bot
from bncbot.conn import Conn


def main():
    conn = Conn(bot.HANDLERS)
    conn.run()


if __name__ == "__main__":
    main()
