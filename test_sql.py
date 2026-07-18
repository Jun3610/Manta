import asyncio
from langchain_community.chat_message_histories import SQLChatMessageHistory

async def main():
    try:
        history = SQLChatMessageHistory(
            session_id="test_session",
            connection="sqlite+aiosqlite:///test.db",
            async_mode=True
        )
        await history.aadd_messages([])
        print("Success with connection and async_mode=True")
    except Exception as e:
        print(f"Error 2: {e}")

asyncio.run(main())
