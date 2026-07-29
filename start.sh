#!/bin/bash
# Entry point for the Chabokan cloud host.
#
# The bot is a long-running worker, not a web server: it polls the Telegram API
# outbound and never listens on a port. It should run forever; the platform
# restarts it if it exits.
python bot.py
