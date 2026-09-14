"""
AsTral GG — Main entrypoint
---------------------------
Run this file to start the bot: python main.py

Folder layout expected:
    main.py
    .env                  <- copy .env.example to .env and fill in real values
    cogs/
        nickname_cog.py
        tickets_cog.py
    data/                 <- created automatically for ticket storage
"""

import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
PREFIX = os.getenv("PREFIX", "!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Every .py file in cogs/ gets loaded automatically — drop new cogs in
# that folder and they'll show up next time the bot starts.
COGS_FOLDER = "cogs"


async def load_all_cogs():
    for filename in os.listdir(COGS_FOLDER):
        if filename.endswith(".py") and not filename.startswith("_"):
            extension = f"{COGS_FOLDER}.{filename[:-3]}"
            try:
                await bot.load_extension(extension)
                print(f"✅ Loaded {extension}")
            except Exception as e:
                print(f"❌ Failed to load {extension}: {e}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Prefix: {PREFIX}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Slash command sync failed: {e}")


async def main():
    async with bot:
        await load_all_cogs()
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
