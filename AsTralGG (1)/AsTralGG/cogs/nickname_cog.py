"""
Nickname Channel Cog — AsTral GG
---------------------------------
Drop this file in your bot's `cogs/` folder as `nickname_cog.py`
and load it like your other cogs, e.g.:

    await bot.load_extension("cogs.nickname_cog")

HOW IT WORKS
------------
- Pick ONE channel to be the "nickname channel" (set its ID below or in .env).
- Any message a member sends there is used as their new server nickname.
- Restricted characters ! , . ' " are stripped out automatically.
- The nickname is trimmed to Discord's 32-character limit.
- Messages are left untouched — nothing gets deleted.
- After every successful change, the bot posts a confirmation with a
  reminder that typing `.reset` reverts the nickname.
- Typing `.reset` in the channel clears the nickname back to the member's
  original Discord username.

CONFIG
------
Add this to your .env file:

    NICKNAME_CHANNEL_ID=123456789012345678

(Right-click the channel in Discord with Developer Mode on -> Copy Channel ID)
"""

import os
import re
from typing import Optional

import discord
from discord.ext import commands

# Characters that are not allowed to appear in the new nickname
RESTRICTED_CHARS = r"[!,.'\"]"

# Discord's hard cap on nickname length
MAX_NICKNAME_LENGTH = 32

# Typing this in the nickname channel resets the member's nickname
RESET_KEYWORD = ".reset"


def clean_nickname(raw_text: str) -> str:
    """Strip restricted characters and excess whitespace, then trim to the length limit."""
    cleaned = re.sub(RESTRICTED_CHARS, "", raw_text)
    cleaned = " ".join(cleaned.split())  # collapse multiple spaces
    return cleaned[:MAX_NICKNAME_LENGTH]


class NicknameChannel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        channel_id = os.getenv("NICKNAME_CHANNEL_ID")
        self.channel_id: Optional[int] = int(channel_id) if channel_id else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore DMs, bots, and messages outside the configured channel
        if message.guild is None or message.author.bot:
            return
        if self.channel_id is None or message.channel.id != self.channel_id:
            return

        # .reset clears the nickname back to the member's original username
        if message.content.strip().lower() == RESET_KEYWORD:
            await self._reset_nickname(message)
            return

        new_nick = clean_nickname(message.content)

        if not new_nick:
            await message.channel.send(
                f"{message.author.mention} that message had nothing usable left after removing "
                f"`! , . ' \"` — please try again."
            )
            return

        try:
            await message.author.edit(nick=new_nick)
        except discord.Forbidden:
            # Usually means the bot's role is below the member's role,
            # or the member is the server owner (owner nicks can't be changed by bots).
            await message.channel.send(
                f"{message.author.mention} I don't have permission to change your nickname "
                f"(my role needs to be above yours in Server Settings > Roles)."
            )
            return
        except discord.HTTPException:
            await message.channel.send(
                f"{message.author.mention} something went wrong setting that nickname — please try again."
            )
            return

        await message.channel.send(
            f"✅ {message.author.mention} your nickname has been updated to **{new_nick}**.\n"
            f"If you'd like to restore your original nickname at any time, simply type `{RESET_KEYWORD}`."
        )

    async def _reset_nickname(self, message: discord.Message):
        try:
            await message.author.edit(nick=None)
        except discord.Forbidden:
            await message.channel.send(
                f"{message.author.mention} I don't have permission to reset your nickname "
                f"(my role needs to be above yours in Server Settings > Roles)."
            )
            return
        except discord.HTTPException:
            await message.channel.send(
                f"{message.author.mention} something went wrong resetting your nickname — please try again."
            )
            return

        await message.channel.send(
            f"🔄 {message.author.mention} your nickname has been reset to your original username."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(NicknameChannel(bot))
