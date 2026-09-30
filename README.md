Discord Bot

A multi-purpose, high-performance Discord bot serving multiple guilds with AI integration, ticketing, and moderation systems. Built natively in Python with `discord.py` and SQLite.

## Core Capabilities

- **AI Integration**: Powered by Groq/OpenAI interfaces for fast, conversational responses.
- **Ticketing System**: Automated channel creation, management, and archiving for support.
- **Verification Gateway**: Role-based assignment handling courses, years, and server access.
- **Interactive Modules**: Built-in games like Truth or Dare and Guess the Number.
- **Resilient**: Implements auto-restart on rate limits (HTTP 429) or API crashes.

## Quick Start

### 1. Requirements
- Python 3.10+
- `pip`

### 2. Installation
```
pip install -r requirements.txt
```

### 3. Configuration
Create a `.env` file in the root directory:
```env
DISCORD_TOKEN=your_bot_token_here
GROQ_API_KEY=your_groq_api_key
```

### 4. Execution
```bash
python test_upd.py
```

## Architecture Notes
- Uses **SQLite** (`chat_logs.db`) for lightweight state management and logging.
- Multi-server config is hardcoded in the `SERVERS` dictionary inside `test_upd.py` for minimal latency.
- Deployment ready for Discloud (`discloud.config` included).

---
*Created by S4NTY*
