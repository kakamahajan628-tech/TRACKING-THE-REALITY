"""TRACKER-BOT-compatible entry point for the scanner and private dashboard.

Keep the adjacent bot/ package: this file is the launcher, not the whole app.
The existing Render start command `python bot.py` continues to work.
"""
import os


def main():
    import uvicorn

    try:
        port = int(os.getenv('PORT', '10000'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit('PORT must be an integer between 1 and 65535') from None
    # One process owns both the scan loop and Telegram long polling.
    uvicorn.run('bot.app:app', host='0.0.0.0', port=port, workers=1)


if __name__ == '__main__':
    main()
