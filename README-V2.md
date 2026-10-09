# ECP-Bot v2

## What this version does

- Keeps the existing `テスト` reply in the test channel.
- Watches `#原` for start and end reports.
- Treats messages with `台番` + `始め` as start reports.
- Treats messages with `抜け` + `獲得` + (`店名` or `名前`) as end reports.
- Ignores intermediate notes such as `159GG` and `現金再開6000〜215`.
- Matches an end report to the latest preceding start report from the same Slack user.
- Falls back to Slack channel history after a process restart, so a database is not required for the pilot.
- Uses the formatter logic copied from `formatter(20261009-072918).html` to generate the sheet.
- Posts the completed sheet to `#原データ作成`.

## Current channel IDs

- `#原`: `C0C7WHETTC6`
- `#原データ作成`: `C0C8STM7WDN`
- Test channel: `C0C7SSG07L5`

The first two may be overridden with `ECP_SOURCE_CHANNEL_ID` and `ECP_OUTPUT_CHANNEL_ID` later.

## Slack requirement

Invite ECP-Bot to both `#原` and `#原データ作成` because the current bot token has `chat:write` but not `chat:write.public`.

## Railway

Keep the existing secret variables:

- `SLACK_BOT_TOKEN` = `xoxb-...`
- `SLACK_APP_TOKEN` = `xapp-...`
- `ECP_TEST_CHANNEL_ID` = `C0C7SSG07L5`

Set the Railway service start command to:

```text
npm start
```

Do not delete the existing Python files during the pilot. `bot.py` remains as the known-good v1 connection test fallback.
