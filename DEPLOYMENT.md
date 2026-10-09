# ECP-Bot: connection test

This is a Python Slack bot, not a GitHub Pages website. It requires a persistent Python worker with Slack Socket Mode connectivity.

## What it does

In the designated test channel, when a human posts exactly `テスト`, ECP-Bot replies in a thread with `ECP-Bot、正常に稼働中です！🤖`. It does not yet process work reports or generate 抜けシート.

## Slack setup

- Install ECP-Bot to your workspace.
- Enable Socket Mode and create an app-level token with `connections:write`.
- Bot OAuth scopes: `channels:history`, `channels:read`, `chat:write`.
- Event Subscriptions: `message.channels`.
- Invite ECP-Bot to #完全新作ツール開発-試運転.

## Hosting

Use a persistent worker service that can store secret environment variables.

- Python: 3.10+
- Install: `pip install -r requirements.txt`
- Start: `python bot.py`
- Host environment variables:
  - `SLACK_BOT_TOKEN`: your bot OAuth token (`xoxb-...`)
  - `SLACK_APP_TOKEN`: your app-level token (`xapp-...`)
  - `ECP_TEST_CHANNEL_ID`: `C0C7SSG07L5`

Never commit real tokens, paste them into chat, or include them in screenshots. Rotate any exposed tokens.

## Test

Once the worker is connected, post `テスト` in the test channel and check for a threaded reply. Other posts are ignored.

## Next milestone

Match start/end reports and port the existing formatter's validated rules without deleting existing functionality.
