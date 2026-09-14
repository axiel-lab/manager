"""
Premium Ticket System — AsTral GG
----------------------------------
A "Ticket King"-style ticket system: category dropdown, claiming, priority,
transcripts, add/remove member, and a logging channel — all with persistent
buttons that survive bot restarts.

FILE LOCATION
-------------
Save as: cogs/tickets_cog.py

REQUIRED .env VARS  (see .env.example)
---------------------------------------
TICKET_CATEGORY_ID       - the Discord category channels get created under
TICKET_LOG_CHANNEL_ID    - where open/claim/close logs + transcripts get posted
SUPPORT_ROLE_ID          - role that can see/claim/close tickets
MAX_TICKETS_PER_USER     - how many open tickets one user can have (default 1)

SETUP COMMAND
--------------
Run this once in the channel where you want the ticket panel:
    !ticketpanel

That posts the "open a ticket" embed with the category dropdown. It only
needs to be run once — the bot re-attaches to it automatically on restart.

DATA STORAGE
------------
Ticket state is kept in data/tickets.json (created automatically). No
external database needed.
"""

import os
import json
import io
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", 0))
TICKET_LOG_CHANNEL_ID = int(os.getenv("TICKET_LOG_CHANNEL_ID", 0))
SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID", 0))
MAX_TICKETS_PER_USER = int(os.getenv("MAX_TICKETS_PER_USER", 1))

DATA_PATH = "data/tickets.json"

CATEGORIES = {
    "general": ("🟦", "General Support"),
    "billing": ("💳", "Billing / Payments"),
    "report": ("🚨", "Report a User"),
    "partner": ("🤝", "Partnership / Business"),
}

PRIORITY_COLORS = {
    "Low": discord.Color.green(),
    "Medium": discord.Color.gold(),
    "High": discord.Color.red(),
}


# ---------------------------------------------------------------------------
# Simple JSON persistence
# ---------------------------------------------------------------------------
def _load_data() -> dict:
    if not os.path.exists(DATA_PATH):
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        return {"counter": 0, "tickets": {}}
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_data(data: dict):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _open_ticket_count_for(user_id: int, data: dict) -> int:
    return sum(
        1 for t in data["tickets"].values()
        if t["user_id"] == user_id and t["status"] == "open"
    )


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------
class AddRemoveMemberModal(discord.ui.Modal):
    def __init__(self, action: str):
        super().__init__(title=f"{action.title()} Member")
        self.action = action
        self.user_input = discord.ui.TextInput(
            label="User ID or @mention",
            placeholder="123456789012345678",
            required=True,
        )
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_input.value.strip().strip("<@!>")
        try:
            member = interaction.guild.get_member(int(raw)) or await interaction.guild.fetch_member(int(raw))
        except (ValueError, discord.NotFound):
            await interaction.response.send_message("Couldn't find that member.", ephemeral=True)
            return

        overwrites = interaction.channel.overwrites
        if self.action == "add":
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            await interaction.channel.edit(overwrites=overwrites)
            await interaction.response.send_message(f"✅ Added {member.mention} to the ticket.")
        else:
            overwrites.pop(member, None)
            await interaction.channel.edit(overwrites=overwrites)
            await interaction.response.send_message(f"✅ Removed {member.mention} from the ticket.")


# ---------------------------------------------------------------------------
# Panel view: category dropdown that opens tickets
# ---------------------------------------------------------------------------
class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name, emoji=emoji, value=key)
            for key, (emoji, name) in CATEGORIES.items()
        ]
        super().__init__(
            placeholder="Select a category to open a ticket...",
            options=options,
            custom_id="ticket_category_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cog: "Tickets" = interaction.client.get_cog("Tickets")
        await cog.create_ticket(interaction, self.values[0])


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())


# ---------------------------------------------------------------------------
# In-ticket control view: claim / priority / add / remove / close
# ---------------------------------------------------------------------------
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.blurple, emoji="🙋", custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "Tickets" = interaction.client.get_cog("Tickets")
        await cog.claim_ticket(interaction)

    @discord.ui.select(
        placeholder="Set priority...",
        custom_id="ticket_priority",
        options=[
            discord.SelectOption(label="Low", emoji="🟢"),
            discord.SelectOption(label="Medium", emoji="🟡"),
            discord.SelectOption(label="High", emoji="🔴"),
        ],
    )
    async def priority(self, interaction: discord.Interaction, select: discord.ui.Select):
        cog: "Tickets" = interaction.client.get_cog("Tickets")
        await cog.set_priority(interaction, select.values[0])

    @discord.ui.button(label="Add Member", style=discord.ButtonStyle.secondary, emoji="➕", custom_id="ticket_add")
    async def add_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddRemoveMemberModal("add"))

    @discord.ui.button(label="Remove Member", style=discord.ButtonStyle.secondary, emoji="➖", custom_id="ticket_remove")
    async def remove_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddRemoveMemberModal("remove"))

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "Tickets" = interaction.client.get_cog("Tickets")
        await cog.close_ticket(interaction)


# ---------------------------------------------------------------------------
# Main cog
# ---------------------------------------------------------------------------
class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = _load_data()

    # -- setup command ------------------------------------------------
    @staticmethod
    def build_panel_embed() -> discord.Embed:
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description=(
                "Need help? Select a category below and a private ticket "
                "channel will be created just for you and our support team."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="AsTral GG Support")
        return embed

    @commands.command(name="ticketpanel")
    @commands.has_permissions(manage_guild=True)
    async def ticketpanel(self, ctx: commands.Context):
        await ctx.send(embed=self.build_panel_embed(), view=TicketPanelView())
        await ctx.message.delete()

    # -- core actions ---------------------------------------------------
    async def create_ticket(self, interaction: discord.Interaction, category_key: str):
        guild = interaction.guild
        user = interaction.user

        if _open_ticket_count_for(user.id, self.data) >= MAX_TICKETS_PER_USER:
            await interaction.followup.send(
                f"You already have {MAX_TICKETS_PER_USER} open ticket(s). Close it before opening another.",
                ephemeral=True,
            )
            return

        category = guild.get_channel(TICKET_CATEGORY_ID)
        support_role = guild.get_role(SUPPORT_ROLE_ID)

        self.data["counter"] += 1
        ticket_number = self.data["counter"]
        emoji, cat_name = CATEGORIES[category_key]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        channel = await guild.create_text_channel(
            name=f"ticket-{ticket_number:04d}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket #{ticket_number} | {cat_name} | Opened by {user} ({user.id})",
        )

        self.data["tickets"][str(channel.id)] = {
            "number": ticket_number,
            "user_id": user.id,
            "category": cat_name,
            "priority": "Medium",
            "claimed_by": None,
            "status": "open",
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_data(self.data)

        embed = discord.Embed(
            title=f"{emoji} {cat_name}",
            description=(
                f"Welcome {user.mention}! Support will be with you shortly.\n\n"
                f"**Ticket:** #{ticket_number:04d}\n**Priority:** 🟡 Medium"
            ),
            color=PRIORITY_COLORS["Medium"],
        )
        embed.set_footer(text="Use the buttons below to manage this ticket.")
        ping = support_role.mention if support_role else ""
        await channel.send(content=f"{user.mention} {ping}", embed=embed, view=TicketControlView())

        await interaction.followup.send(f"✅ Ticket created: {channel.mention}", ephemeral=True)
        await self._log(guild, f"🎫 **Ticket #{ticket_number:04d}** opened by {user.mention} ({cat_name})")

    async def claim_ticket(self, interaction: discord.Interaction):
        ticket = self.data["tickets"].get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("This isn't a tracked ticket.", ephemeral=True)
            return
        if ticket["claimed_by"]:
            await interaction.response.send_message(f"Already claimed by <@{ticket['claimed_by']}>.", ephemeral=True)
            return

        ticket["claimed_by"] = interaction.user.id
        _save_data(self.data)
        await interaction.response.send_message(f"🙋 Claimed by {interaction.user.mention}")
        await self._log(interaction.guild, f"🙋 Ticket #{ticket['number']:04d} claimed by {interaction.user.mention}")

    async def set_priority(self, interaction: discord.Interaction, priority: str):
        ticket = self.data["tickets"].get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("This isn't a tracked ticket.", ephemeral=True)
            return
        ticket["priority"] = priority
        _save_data(self.data)
        emoji = {"Low": "🟢", "Medium": "🟡", "High": "🔴"}[priority]
        await interaction.response.send_message(f"{emoji} Priority set to **{priority}**")

    async def close_ticket(self, interaction: discord.Interaction):
        ticket = self.data["tickets"].get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("This isn't a tracked ticket.", ephemeral=True)
            return

        await interaction.response.send_message("🔒 Closing ticket and generating transcript...")
        channel = interaction.channel

        # Build a plain-text transcript
        lines = []
        async for msg in channel.history(limit=None, oldest_first=True):
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{timestamp}] {msg.author}: {msg.content}")
        transcript_text = "\n".join(lines) if lines else "(no messages)"
        transcript_file = discord.File(
            io.BytesIO(transcript_text.encode("utf-8")),
            filename=f"ticket-{ticket['number']:04d}-transcript.txt",
        )

        ticket["status"] = "closed"
        _save_data(self.data)

        await self._log(
            interaction.guild,
            f"🔒 Ticket #{ticket['number']:04d} closed by {interaction.user.mention}",
            file=transcript_file,
        )

        # Try to DM the ticket opener a copy
        opener = interaction.guild.get_member(ticket["user_id"])
        if opener:
            try:
                dm_file = discord.File(io.BytesIO(transcript_text.encode("utf-8")), filename=f"ticket-{ticket['number']:04d}-transcript.txt")
                await opener.send(f"Your ticket #{ticket['number']:04d} was closed. Transcript attached.", file=dm_file)
            except discord.Forbidden:
                pass

        await channel.delete(reason="Ticket closed")

    async def _log(self, guild: discord.Guild, message: str, file: discord.File = None):
        log_channel = guild.get_channel(TICKET_LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(message, file=file)


# ---------------------------------------------------------------------------
# Slash commands — every ticket action, also available as /ticket ...
# ---------------------------------------------------------------------------
class TicketSlashCommands(app_commands.Group):
    """/ticket panel | claim | close | add | remove | priority"""

    def __init__(self, cog: "Tickets"):
        super().__init__(name="ticket", description="Ticket system commands")
        self.cog = cog

    def _in_ticket(self, interaction: discord.Interaction) -> bool:
        return str(interaction.channel.id) in self.cog.data["tickets"]

    @app_commands.command(name="panel", description="Post the ticket panel in this channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=self.cog.build_panel_embed(), view=TicketPanelView()
        )

    @app_commands.command(name="claim", description="Claim this ticket")
    async def claim(self, interaction: discord.Interaction):
        if not self._in_ticket(interaction):
            await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
            return
        await self.cog.claim_ticket(interaction)

    @app_commands.command(name="close", description="Close this ticket and generate a transcript")
    async def close(self, interaction: discord.Interaction):
        if not self._in_ticket(interaction):
            await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
            return
        await self.cog.close_ticket(interaction)

    @app_commands.command(name="add", description="Add a member to this ticket")
    @app_commands.describe(member="The member to add")
    async def add(self, interaction: discord.Interaction, member: discord.Member):
        if not self._in_ticket(interaction):
            await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
            return
        overwrites = interaction.channel.overwrites
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
        await interaction.channel.edit(overwrites=overwrites)
        await interaction.response.send_message(f"✅ Added {member.mention} to the ticket.")

    @app_commands.command(name="remove", description="Remove a member from this ticket")
    @app_commands.describe(member="The member to remove")
    async def remove(self, interaction: discord.Interaction, member: discord.Member):
        if not self._in_ticket(interaction):
            await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
            return
        overwrites = interaction.channel.overwrites
        overwrites.pop(member, None)
        await interaction.channel.edit(overwrites=overwrites)
        await interaction.response.send_message(f"✅ Removed {member.mention} from the ticket.")

    @app_commands.command(name="priority", description="Set this ticket's priority")
    @app_commands.describe(level="Priority level")
    @app_commands.choices(level=[
        app_commands.Choice(name="Low", value="Low"),
        app_commands.Choice(name="Medium", value="Medium"),
        app_commands.Choice(name="High", value="High"),
    ])
    async def priority(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if not self._in_ticket(interaction):
            await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
            return
        await self.cog.set_priority(interaction, level.value)


async def setup(bot: commands.Bot):
    cog = Tickets(bot)
    await bot.add_cog(cog)
    # Register persistent views so buttons/dropdowns work after a restart
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    # Register the /ticket slash command group
    bot.tree.add_command(TicketSlashCommands(cog))
