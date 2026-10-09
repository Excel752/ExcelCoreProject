"""ECP Slack Bot: first milestone, Socket Mode test reply."""
import logging
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO)


def required_env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f'Missing environment variable: {name}')
    return value


def create_app(bot_token: str, channel_id: str) -> App:
    app = App(token=bot_token)

    @app.event('message')
    def on_message(event, say):
        if event.get('subtype') or event.get('bot_id'):
            return
        if event.get('channel') != channel_id:
            return
        if event.get('text', '').strip() == 'テスト':
            say(text='ECP-Bot、正常に稼働中です！🤖', thread_ts=event.get('ts'))

    return app


def main():
    app = create_app(required_env('SLACK_BOT_TOKEN'), required_env('ECP_TEST_CHANNEL_ID'))
    SocketModeHandler(app, required_env('SLACK_APP_TOKEN')).start()


if __name__ == '__main__':
    main()
