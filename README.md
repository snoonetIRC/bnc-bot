# bnc-bot

**This project has moved to [TotallyNotRobots/bnc-bot](https://github.com/TotallyNotRobots/bnc-bot).**

A bot to handle BNC account requests for Snoonet

## Designed for
- [Anope](https://anope.org) IRC Services v2.0.5
- [ZNC](https://znc.in) v1.6.5
- [InspIRCd](https://inspircd.org) with the `m_services_account` module

This bot may work with other systems, but this is the setup it was specifically written to work with

## Requirements
- Python 3.6+

## Features
- Assigns each user a unique bindhost in the 127.0.0.0/16 range
- Generates a temporary password for a user on request approval and sends it to them through MemoServ
- Tracks existing BNC user accounts to avoid overwriting existing accounts

## Installation
1. Set up a Python 3.6 virtualenv
2. `pip install -Ur requirements.txt`
3. Copy `config.default.json` to `config.json` and modify the values as needed
4. Run `python -m bncbot` to start the bot

## Commands
### User Commands
#### `requestbnc`
Submit a BNC account request

### Admin Commands
#### `acceptbnc <username>`
Accept a BNC account request for [username]

#### `denybnc <username>`
Deny a BNC account request for [username]

#### `delbnc <username>`
Delete [username]'s BNC account

#### `bncresetpass <username>`
Reset [username]'s BNC account password

#### `bncqueue`
List all current entries in the BNC account request queue awaiting approval

#### `bncsetadmin <username>`
Grant [username] BNC admin access

#### `bncrefresh`
Update the cached version of the BNC user list


