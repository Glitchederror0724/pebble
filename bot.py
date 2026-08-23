import os
import random
import asyncio
import aiohttp
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from google import genai


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Optional: put your Discord server ID here while testing.
# Slash commands will appear almost instantly in this server.
TEST_GUILD_ID = os.getenv("TEST_GUILD_ID")

## ============================================================
# GEMINI AI
# ============================================================
# Roblox verification role name
VERIFY_ROLE_NAME = os.getenv("VERIFY_ROLE_NAME", "Verified")

# Ticket category name
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "Tickets")

# Roblox verification using bloxlink
BLOXLINK_API_KEY = os.getenv("BLOXLINK_API_KEY")

print("RAILWAY_ENV:", os.getenv("RAILWAY_ENVIRONMENT_NAME"))
print("RAILWAY_SERVICE:", os.getenv("RAILWAY_SERVICE_NAME"))
print("DISCORD_TOKEN_EXISTS:", bool(os.getenv("DISCORD_TOKEN")))
print("DISCORD_TOKEN_LENGTH:", len(os.getenv("DISCORD_TOKEN") or ""))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing.")

if not BLOXLINK_API_KEY:
    print("⚠️ BLOXLINK_API_KEY is not configured. Roblox verification commands will be unavailable.")




GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

gemini_client = None

if GEMINI_API_KEY:
    gemini_client = genai.Client(
        api_key=GEMINI_API_KEY
    )
else:
    print("⚠️ GEMINI_API_KEY is not configured.")


# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


# ============================================================
# HELPERS
# ============================================================

def is_moderator(interaction: discord.Interaction) -> bool:
    """Check whether the user has moderation permissions."""
    return (
        interaction.user.guild_permissions.manage_messages
        or interaction.user.guild_permissions.moderate_members
        or interaction.user.guild_permissions.administrator
    )


def has_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator


def format_dt(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:F>"


async def roblox_request(url: str):
    """Make a Roblox API request."""
    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url,
            headers={
                "User-Agent": "DiscordBot/1.0"
            }
        ) as response:

            if response.status != 200:
                return None

            return await response.json()


async def get_roblox_user(username: str):
    """Find a Roblox user by username."""

    url = "https://users.roblox.com/v1/usernames/users"

    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            url,
            json={
                "usernames": [username],
                "excludeBannedUsers": False
            },
            headers={
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot/1.0"
            }
        ) as response:

            if response.status != 200:
                return None

            data = await response.json()

            if not data.get("data"):
                return None

            return data["data"][0]


# ============================================================
# BOT EVENTS
# ============================================================

@bot.tree.command(
    name="setup",
    description="Set up the basic server structure."
)
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    created_channels = []
    created_roles = []

    # ============================================================
    # ROLES
    # ============================================================

    verified_role = discord.utils.get(
        guild.roles,
        name="Verified"
    )

    if verified_role is None:
        await guild.create_role(
            name="Verified",
            reason="Bot server setup"
        )
        created_roles.append("Verified")

    muted_role = discord.utils.get(
        guild.roles,
        name="Muted"
    )

    if muted_role is None:
        await guild.create_role(
            name="Muted",
            reason="Bot server setup"
        )
        created_roles.append("Muted")

    # ============================================================
    # CATEGORIES
    # ============================================================

    categories = {}

    for category_name in [
        "SERVER INFO",
        "SUPPORT",
        "BOT LOGS"
    ]:

        category = discord.utils.get(
            guild.categories,
            name=category_name
        )

        if category is None:
            category = await guild.create_category(
                category_name,
                reason="Bot server setup"
            )

        categories[category_name] = category

    # ============================================================
    # CHANNELS
    # ============================================================

    channel_categories = {
        "welcome": "SERVER INFO",
        "rules": "SERVER INFO",
        "announcements": "SERVER INFO",
        "tickets": "SUPPORT",
        "mod-logs": "BOT LOGS",
        "server-logs": "BOT LOGS"
    }

    for channel_name, category_name in channel_categories.items():

        existing_channel = discord.utils.get(
            guild.text_channels,
            name=channel_name
        )

        if existing_channel is None:

            await guild.create_text_channel(
                channel_name,
                category=categories[category_name],
                reason="Bot server setup"
            )

            created_channels.append(channel_name)

    # ============================================================
    # RESULT
    # ============================================================

    embed = discord.Embed(
        title="✅ Server Setup Complete",
        color=discord.Color.green()
    )

    if created_channels:
        embed.add_field(
            name="Channels Created",
            value="\n".join(
                f"• #{name}"
                for name in created_channels
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="Channels",
            value="All channels already existed.",
            inline=False
        )

    if created_roles:
        embed.add_field(
            name="Roles Created",
            value="\n".join(
                f"• {name}"
                for name in created_roles
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="Roles",
            value="All roles already existed.",
            inline=False
        )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True
    )


@setup.error
async def setup_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):
        await interaction.response.send_message(
            "❌ You need Administrator permission to use `/setup`.",
            ephemeral=True
        )
        return

    print(f"/setup error: {error}")

    if interaction.response.is_done():
        await interaction.followup.send(
            "❌ Something went wrong with `/setup`.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "❌ Something went wrong with `/setup`.",
            ephemeral=True
        )

@bot.event
async def on_ready():
    print("=" * 50)
    print(f"Logged in as {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print(f"Servers: {len(bot.guilds)}")

    if not TEST_GUILD_ID:
        print("❌ TEST_GUILD_ID is not set.")
        return

    try:
        guild = discord.Object(id=int(TEST_GUILD_ID))

        # Remove the old /ai command from the test server
        try:
            bot.tree.remove_command(
                "ai",
                guild=guild
            )
        except Exception:
            pass

        # Sync the current command tree
        synced = await bot.tree.sync(guild=guild)

        print(f"Synced {len(synced)} commands to test guild.")

        # Show exactly what Discord received
        print("Commands:")
        for command in synced:
            print(f"  /{command.name}")

    except Exception as e:
        print(f"Command sync error: {e}")

    print("=" * 50)
# ============================================================
# SERVER INFO
# ============================================================

@bot.tree.command(
    name="serverinfo",
    description="Shows information about the server."
)
async def serverinfo(interaction: discord.Interaction):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    total_members = guild.member_count or 0
    bot_count = sum(member.bot for member in guild.members)
    human_count = total_members - bot_count

    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    categories = len(guild.categories)

    embed = discord.Embed(
        title=f"📊 {guild.name}",
        description="Server information",
        color=discord.Color.blurple()
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(
        name="👑 Owner",
        value=guild.owner.mention if guild.owner else "Unknown",
        inline=True
    )

    embed.add_field(
        name="🆔 Server ID",
        value=f"`{guild.id}`",
        inline=True
    )

    embed.add_field(
        name="📅 Created",
        value=format_dt(guild.created_at),
        inline=True
    )

    embed.add_field(
        name="👥 Members",
        value=(
            f"Total: **{total_members:,}**\n"
            f"Humans: **{human_count:,}**\n"
            f"Bots: **{bot_count:,}**"
        ),
        inline=True
    )

    embed.add_field(
        name="💬 Channels",
        value=(
            f"Text: **{text_channels}**\n"
            f"Voice: **{voice_channels}**\n"
            f"Categories: **{categories}**"
        ),
        inline=True
    )

    embed.add_field(
        name="🎭 Roles",
        value=f"**{len(guild.roles):,}**",
        inline=True
    )

    embed.add_field(
        name="😀 Emojis",
        value=f"**{len(guild.emojis):,}**",
        inline=True
    )

    embed.add_field(
        name="🚀 Boosts",
        value=(
            f"Level: **{guild.premium_tier}**\n"
            f"Boosts: **{guild.premium_subscription_count or 0}**"
        ),
        inline=True
    )

    embed.set_footer(
        text=f"Requested by {interaction.user}"
    )

    await interaction.response.send_message(embed=embed)


# ============================================================
# BLOXLINK / ROBLOX VERIFICATION
# ============================================================

BLOXLINK_API_BASE = "https://api.blox.link/v4/public"


async def bloxlink_request(
    method: str,
    endpoint: str
):
    if not BLOXLINK_API_KEY:
        return None, "Bloxlink API key is not configured."

    url = f"{BLOXLINK_API_BASE}{endpoint}"

    headers = {
        "Authorization": BLOXLINK_API_KEY
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                url,
                headers=headers
            ) as response:

                try:
                    data = await response.json()
                except Exception:
                    data = {}

                if response.status == 200:
                    return data, None

                if response.status == 404:
                    return None, "not_found"

                return None, (
                    f"Bloxlink API returned HTTP {response.status}"
                )

    except aiohttp.ClientError as e:
        print(f"Bloxlink API error: {e}")
        return None, "Bloxlink API connection failed."


# ============================================================
# /VERIFY
# ============================================================

@bot.tree.command(
    name="verify",
    description="Get the Bloxlink Roblox verification page."
)
async def verify(interaction: discord.Interaction):

    embed = discord.Embed(
        title="🔗 Roblox Verification",
        description=(
            "Link your Roblox account with Bloxlink, "
            "then use `/verify-check` here."
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="How to verify",
        value=(
            "1. Open the Bloxlink verification page.\n"
            "2. Sign in with Discord.\n"
            "3. Link your Roblox account.\n"
            "4. Select this server.\n"
            "5. Come back and run `/verify-check`."
        ),
        inline=False
    )

    view = discord.ui.View()

    view.add_item(
        discord.ui.Button(
            label="Verify with Bloxlink",
            url="https://blox.link/dashboard/user/verifications/verify",
            style=discord.ButtonStyle.link
        )
    )

    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True
    )


# ============================================================
# /VERIFY-CHECK
# ============================================================

@bot.tree.command(
    name="verify-check",
    description="Check your linked Roblox account."
)
async def verify_check(
    interaction: discord.Interaction
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    await interaction.response.defer(
        ephemeral=True
    )

    data, error = await bloxlink_request(
        "GET",
        f"/guilds/{interaction.guild.id}/discord-to-roblox/{interaction.user.id}"
    )

    if error == "not_found":
        embed = discord.Embed(
            title="❌ Not Verified",
            description=(
                "I couldn't find a Roblox account linked "
                "to your Discord account through Bloxlink."
            ),
            color=discord.Color.red()
        )

        embed.add_field(
            name="What to do",
            value=(
                "Run `/verify` and complete the Bloxlink "
                "verification process."
            ),
            inline=False
        )

        await interaction.followup.send(
            embed=embed,
            ephemeral=True
        )
        return

    if error:
        await interaction.followup.send(
            f"❌ {error}",
            ephemeral=True
        )
        return

    roblox_id = data.get("robloxID")

    if not roblox_id:
        await interaction.followup.send(
            "❌ Bloxlink didn't return a Roblox ID.",
            ephemeral=True
        )
        return

    # Ask Bloxlink to update the user's roles/nickname.
    update_data, update_error = await bloxlink_request(
        "POST",
        f"/guilds/{interaction.guild.id}/update-user/{interaction.user.id}"
    )

    embed = discord.Embed(
        title="✅ Roblox Verified",
        color=discord.Color.green()
    )

    embed.add_field(
        name="Roblox ID",
        value=str(roblox_id),
        inline=False
    )

    if update_data:
        added_roles = update_data.get(
            "addedRoles",
            []
        )

        nickname = update_data.get(
            "nickname"
        )

        if added_roles:
            embed.add_field(
                name="Roles Updated",
                value=", ".join(added_roles),
                inline=False
            )

        if nickname:
            embed.add_field(
                name="Bloxlink Nickname",
                value=str(nickname),
                inline=False
            )

    if update_error and update_error != "not_found":
        embed.set_footer(
            text="Roblox account found, but Bloxlink role update failed."
        )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# BLOXLINK / ROBLOX VERIFICATION
# ============================================================

@bot.tree.command(
    name="roblox",
    description="Look up the Roblox account linked to a Discord user."
)
@app_commands.describe(
    user="The Discord user to look up."
)
async def roblox(
    interaction: discord.Interaction,
    user: discord.Member
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    data, error = await bloxlink_request(
        "GET",
        f"/guilds/{interaction.guild.id}/discord-to-roblox/{user.id}"
    )

    if error == "not_found":
        embed = discord.Embed(
            title="❌ Roblox Account Not Found",
            description=(
                f"{user.mention} doesn't appear to have "
                "a Bloxlink-linked Roblox account in this server."
            ),
            color=discord.Color.red()
        )

        await interaction.followup.send(
            embed=embed
        )
        return

    if error:
        await interaction.followup.send(
            f"❌ {error}"
        )
        return

    roblox_id = data.get("robloxID")

    if not roblox_id:
        await interaction.followup.send(
            "❌ Bloxlink didn't return a Roblox ID."
        )
        return

    embed = discord.Embed(
        title="🎮 Roblox Account",
        color=discord.Color.blurple()
    )

    embed.set_thumbnail(
        url=f"https://www.roblox.com/headshot-thumbnail/image?userId={roblox_id}&width=420&height=420&format=png"
    )

    embed.add_field(
        name="Discord",
        value=user.mention,
        inline=True
    )

    embed.add_field(
        name="Roblox ID",
        value=str(roblox_id),
        inline=True
    )

    embed.add_field(
        name="Roblox Profile",
        value=(
            f"[View Roblox Profile]"
            f"(https://www.roblox.com/users/{roblox_id}/profile)"
        ),
        inline=False
    )

    await interaction.followup.send(
        embed=embed
    )


# ============================================================
# USER INFO
# ============================================================

@bot.tree.command(
    name="userinfo",
    description="Shows information about a user."
)
@app_commands.describe(user="The user to inspect.")
async def userinfo(
    interaction: discord.Interaction,
    user: discord.Member = None
):

    user = user or interaction.user

    roles = [
        role.mention
        for role in user.roles
        if role != interaction.guild.default_role
    ]

    embed = discord.Embed(
        title=f"👤 {user}",
        color=user.color if user.color != discord.Color.default()
        else discord.Color.blurple()
    )

    embed.set_thumbnail(url=user.display_avatar.url)

    embed.add_field(
        name="🆔 User ID",
        value=f"`{user.id}`",
        inline=True
    )

    embed.add_field(
        name="📅 Account Created",
        value=format_dt(user.created_at),
        inline=True
    )

    embed.add_field(
        name="📥 Joined Server",
        value=format_dt(user.joined_at) if user.joined_at else "Unknown",
        inline=True
    )

    embed.add_field(
        name="🎭 Roles",
        value=", ".join(roles[-10:]) if roles else "None",
        inline=False
    )

    await interaction.response.send_message(embed=embed)


# ============================================================
# AVATAR
# ============================================================

@bot.tree.command(
    name="avatar",
    description="Shows a user's avatar."
)
@app_commands.describe(user="The user whose avatar you want.")
async def avatar(
    interaction: discord.Interaction,
    user: discord.User = None
):

    user = user or interaction.user

    embed = discord.Embed(
        title=f"🖼️ {user.display_name}'s Avatar",
        color=discord.Color.blurple()
    )

    embed.set_image(url=user.display_avatar.url)

    await interaction.response.send_message(embed=embed)


# ============================================================
# MODERATION - BAN
# ============================================================

@bot.tree.command(
    name="ban",
    description="Ban a member."
)
@app_commands.describe(
    user="Member to ban.",
    reason="Reason for the ban."
)
@app_commands.checks.has_permissions(ban_members=True)
async def ban(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "No reason provided"
):

    if user == interaction.user:
        await interaction.response.send_message(
            "❌ You cannot ban yourself.",
            ephemeral=True
        )
        return

    if user.top_role >= interaction.user.top_role:
        await interaction.response.send_message(
            "❌ You cannot ban someone with an equal or higher role.",
            ephemeral=True
        )
        return

    await user.ban(reason=reason)

    await interaction.response.send_message(
        f"🔨 **{user}** has been banned.\nReason: **{reason}**"
    )


# ============================================================
# MODERATION - KICK
# ============================================================

@bot.tree.command(
    name="kick",
    description="Kick a member."
)
@app_commands.describe(
    user="Member to kick.",
    reason="Reason for the kick."
)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "No reason provided"
):

    if user == interaction.user:
        await interaction.response.send_message(
            "❌ You cannot kick yourself.",
            ephemeral=True
        )
        return

    if user.top_role >= interaction.user.top_role:
        await interaction.response.send_message(
            "❌ You cannot kick someone with an equal or higher role.",
            ephemeral=True
        )
        return

    await user.kick(reason=reason)

    await interaction.response.send_message(
        f"👢 **{user}** has been kicked.\nReason: **{reason}**"
    )


# ============================================================
# MODERATION - TIMEOUT
# ============================================================

@bot.tree.command(
    name="timeout",
    description="Timeout a member."
)
@app_commands.describe(
    user="Member to timeout.",
    minutes="How many minutes.",
    reason="Reason."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    user: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
    reason: str = "No reason provided"
):

    until = discord.utils.utcnow() + timedelta(minutes=minutes)

    await user.timeout(until, reason=reason)

    await interaction.response.send_message(
        f"⏳ **{user}** has been timed out for **{minutes} minutes**."
    )


# ============================================================
# MODERATION - WARN
# ============================================================

warnings = {}


@bot.tree.command(
    name="warn",
    description="Warn a member."
)
@app_commands.describe(
    user="Member to warn.",
    reason="Reason for the warning."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "No reason provided"
):

    guild_id = interaction.guild.id

    if guild_id not in warnings:
        warnings[guild_id] = {}

    if user.id not in warnings[guild_id]:
        warnings[guild_id][user.id] = []

    warnings[guild_id][user.id].append({
        "reason": reason,
        "moderator": interaction.user.id,
        "time": datetime.utcnow()
    })

    count = len(warnings[guild_id][user.id])

    await interaction.response.send_message(
        f"⚠️ **{user}** has been warned.\n"
        f"Reason: **{reason}**\n"
        f"Warnings: **{count}**"
    )


# ============================================================
# MODERATION - PURGE
# ============================================================

@bot.tree.command(
    name="purge",
    description="Delete messages from a channel."
)
@app_commands.describe(amount="Number of messages to delete.")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 100]
):

    await interaction.response.defer(ephemeral=True)

    deleted = await interaction.channel.purge(limit=amount)

    await interaction.followup.send(
        f"🧹 Deleted **{len(deleted)}** messages.",
        ephemeral=True
    )


# ============================================================
# MODERATION - LOCK
# ============================================================

@bot.tree.command(
    name="lock",
    description="Lock the current channel."
)
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction):

    channel = interaction.channel

    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False

    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message(
        "🔒 This channel has been locked."
    )


# ============================================================
# MODERATION - UNLOCK
# ============================================================

@bot.tree.command(
    name="unlock",
    description="Unlock the current channel."
)
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction):

    channel = interaction.channel

    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None

    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message(
        "🔓 This channel has been unlocked."
    )


# ============================================================
# FUN - 8BALL
# ============================================================

@bot.tree.command(
    name="8ball",
    description="Ask the magic 8ball a question."
)
@app_commands.describe(question="Your question.")
async def eightball(
    interaction: discord.Interaction,
    question: str
):

    answers = [
        "Yes.",
        "No.",
        "Definitely!",
        "Absolutely not.",
        "Maybe.",
        "Ask again later.",
        "It is very likely.",
        "I don't think so.",
        "Without a doubt!",
        "The future is unclear."
    ]

    embed = discord.Embed(
        title="🎱 Magic 8-Ball",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="Question",
        value=question,
        inline=False
    )

    embed.add_field(
        name="Answer",
        value=random.choice(answers),
        inline=False
    )

    await interaction.response.send_message(embed=embed)

##################################################################
################# PING ###########################################
##################################################################

@bot.tree.command(
    name="ping",
    description="Check the bot's latency."
)
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)

    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Bot latency: **{latency}ms**",
        color=discord.Color.green()
    )

    await interaction.response.send_message(embed=embed)

# ============================================================
# FUN - COINFLIP
# ============================================================

@bot.tree.command(
    name="coinflip",
    description="Flip a coin."
)
async def coinflip(interaction: discord.Interaction):

    result = random.choice(["Heads", "Tails"])

    await interaction.response.send_message(
        f"🪙 The coin landed on **{result}**!"
    )


# ============================================================
# FUN - DICE
# ============================================================

@bot.tree.command(
    name="dice",
    description="Roll a six-sided die."
)
async def dice(interaction: discord.Interaction):

    result = random.randint(1, 6)

    await interaction.response.send_message(
        f"🎲 You rolled **{result}**!"
    )


# ============================================================
# FUN - RPS
# ============================================================

@bot.tree.command(
    name="rps",
    description="Play rock paper scissors."
)
@app_commands.describe(choice="Choose rock, paper, or scissors.")
@app_commands.choices(
    choice=[
        app_commands.Choice(name="Rock", value="rock"),
        app_commands.Choice(name="Paper", value="paper"),
        app_commands.Choice(name="Scissors", value="scissors"),
    ]
)
async def rps(
    interaction: discord.Interaction,
    choice: app_commands.Choice[str]
):

    bot_choice = random.choice(
        ["rock", "paper", "scissors"]
    )

    player = choice.value

    if player == bot_choice:
        result = "It's a tie!"
    elif (
        (player == "rock" and bot_choice == "scissors")
        or
        (player == "paper" and bot_choice == "rock")
        or
        (player == "scissors" and bot_choice == "paper")
    ):
        result = "You win! 🎉"
    else:
        result = "I win! 🤖"

    await interaction.response.send_message(
        f"🪨📄✂️ You chose **{player}**.\n"
        f"I chose **{bot_choice}**.\n\n"
        f"**{result}**"
    )


# ============================================================
# FUN - JOKE
# ============================================================

@bot.tree.command(
    name="joke",
    description="Get a random joke."
)
async def joke(interaction: discord.Interaction):

    jokes = [
        "Why did the computer go to the doctor? It had a virus.",
        "Why do programmers prefer dark mode? Because light attracts bugs.",
        "I told my computer I needed a break. Now it won't stop sending me vacation ads.",
        "Why was the JavaScript developer sad? Because they didn't know how to 'null' their feelings."
    ]

    await interaction.response.send_message(
        f"😂 {random.choice(jokes)}"
    )


# ============================================================
# ROBLOX LOOKUP
# ============================================================

@bot.tree.command(
    name="lookup",
    description="Look up a Roblox user."
)
@app_commands.describe(username="Roblox username.")
async def lookup(
    interaction: discord.Interaction,
    username: str
):

    await interaction.response.defer()

    user = await get_roblox_user(username)

    if not user:
        await interaction.followup.send(
            f"❌ I couldn't find a Roblox user named **{username}**."
        )
        return

    user_id = user["id"]
    display_name = user.get("displayName", user["name"])

    profile_url = f"https://www.roblox.com/users/{user_id}/profile"

    avatar_url = (
        f"https://www.roblox.com/headshot-thumbnail/"
        f"image?userId={user_id}&width=420&height=420&format=png"
    )

    embed = discord.Embed(
        title=f"🎮 Roblox User — {user['name']}",
        color=discord.Color.red(),
        url=profile_url
    )

    embed.set_thumbnail(url=avatar_url)

    embed.add_field(
        name="Username",
        value=user["name"],
        inline=True
    )

    embed.add_field(
        name="Display Name",
        value=display_name,
        inline=True
    )

    embed.add_field(
        name="User ID",
        value=f"`{user_id}`",
        inline=True
    )

    embed.add_field(
        name="Profile",
        value=f"[Open Roblox Profile]({profile_url})",
        inline=False
    )

    await interaction.followup.send(embed=embed)


# ============================================================
# ROBLOX VERIFY
# ============================================================

@bot.tree.command(
    name="old_verify",
    description="Verify your Roblox account."
)
@app_commands.describe(username="Your Roblox username.")
async def verify(
    interaction: discord.Interaction,
    username: str
):

    await interaction.response.defer(ephemeral=True)

    user = await get_roblox_user(username)

    if not user:
        await interaction.followup.send(
            "❌ Roblox username not found.",
            ephemeral=True
        )
        return

    role = discord.utils.get(
        interaction.guild.roles,
        name=VERIFY_ROLE_NAME
    )

    if role is None:
        try:
            role = await interaction.guild.create_role(
                name=VERIFY_ROLE_NAME,
                reason="Create verification role"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to create the Verified role.",
                ephemeral=True
            )
            return

    if role not in interaction.user.roles:

        try:
            await interaction.user.add_roles(role)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I can't give you the verification role. "
                "Make sure my bot role is above the Verified role.",
                ephemeral=True
            )
            return

    await interaction.followup.send(
        f"✅ You are now verified as Roblox user "
        f"**{user['name']}**!\n\n"
        f"⚠️ **Important:** this basic version verifies that the "
        f"Roblox username exists. It does not yet prove that you own "
        f"the Roblox account.",
        ephemeral=True
    )


# ============================================================
# TICKET
# ============================================================

@bot.tree.command(
    name="ticket",
    description="Create a support ticket."
)
async def ticket(interaction: discord.Interaction):

    guild = interaction.guild

    existing = discord.utils.get(
        guild.text_channels,
        name=f"ticket-{interaction.user.name.lower()}"
    )

    if existing:
        await interaction.response.send_message(
            f"❌ You already have a ticket: {existing.mention}",
            ephemeral=True
        )
        return

    category = discord.utils.get(
        guild.categories,
        name=TICKET_CATEGORY_NAME
    )

    if category is None:
        category = await guild.create_category(
            TICKET_CATEGORY_NAME
        )

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        ),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            read_message_history=True
        )
    }

    channel = await guild.create_text_channel(
        name=f"ticket-{interaction.user.name}",
        category=category,
        overwrites=overwrites,
        reason="Ticket created"
    )

    embed = discord.Embed(
        title="🎫 Support Ticket",
        description=(
            f"Welcome {interaction.user.mention}!\n\n"
            "Please explain your issue and a staff member will help you."
        ),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text="Use /close when your issue is resolved."
    )

    await channel.send(
        content=interaction.user.mention,
        embed=embed
    )

    await interaction.response.send_message(
        f"✅ Your ticket has been created: {channel.mention}",
        ephemeral=True
    )


# ============================================================
# CLOSE TICKET
# ============================================================

@bot.tree.command(
    name="close",
    description="Close the current ticket."
)
async def close(
    interaction: discord.Interaction
):

    channel = interaction.channel

    if not channel.name.startswith("ticket-"):
        await interaction.response.send_message(
            "❌ This isn't a ticket channel.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        "🔒 Closing this ticket in 5 seconds..."
    )

    await asyncio.sleep(5)

    await channel.delete(
        reason=f"Ticket closed by {interaction.user}"
    )


# ============================================================
# TICKET CLAIM
# ============================================================

@bot.tree.command(
    name="claim",
    description="Claim the current ticket."
)
async def claim(
    interaction: discord.Interaction
):

    if not is_moderator(interaction):
        await interaction.response.send_message(
            "❌ You need moderation permissions to claim tickets.",
            ephemeral=True
        )
        return

    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message(
            "❌ This isn't a ticket channel.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"🎫 This ticket has been claimed by {interaction.user.mention}."
    )


# ============================================================
# TICKET ADD
# ============================================================

@bot.tree.command(
    name="add",
    description="Add a user to the current ticket."
)
@app_commands.describe(user="User to add.")
async def add(
    interaction: discord.Interaction,
    user: discord.Member
):

    if not is_moderator(interaction):
        await interaction.response.send_message(
            "❌ You need moderation permissions.",
            ephemeral=True
        )
        return

    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message(
            "❌ This isn't a ticket channel.",
            ephemeral=True
        )
        return

    await interaction.channel.set_permissions(
        user,
        view_channel=True,
        send_messages=True,
        read_message_history=True
    )

    await interaction.response.send_message(
        f"✅ Added {user.mention} to the ticket."
    )


# ============================================================
# TICKET REMOVE
# ============================================================

@bot.tree.command(
    name="remove",
    description="Remove a user from the current ticket."
)
@app_commands.describe(user="User to remove.")
async def remove(
    interaction: discord.Interaction,
    user: discord.Member
):

    if not is_moderator(interaction):
        await interaction.response.send_message(
            "❌ You need moderation permissions.",
            ephemeral=True
        )
        return

    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message(
            "❌ This isn't a ticket channel.",
            ephemeral=True
        )
        return

    await interaction.channel.set_permissions(
        user,
        overwrite=None
    )

    await interaction.response.send_message(
        f"✅ Removed {user.mention} from the ticket."
    )


# ============================================================
# ANNOUNCE
# ============================================================

@bot.tree.command(
    name="announce",
    description="Send an announcement."
)
@app_commands.describe(
    message="Announcement message."
)
@app_commands.checks.has_permissions(manage_guild=True)
async def announce(
    interaction: discord.Interaction,
    message: str
):

    embed = discord.Embed(
        title="📢 Announcement",
        description=message,
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow()
    )

    embed.set_footer(
        text=f"Announcement by {interaction.user}"
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# POLL
# ============================================================

@bot.tree.command(
    name="poll",
    description="Create a poll."
)
@app_commands.describe(question="The poll question.")
async def poll(
    interaction: discord.Interaction,
    question: str
):

    embed = discord.Embed(
        title="📊 Poll",
        description=question,
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text=f"Poll created by {interaction.user}"
    )

    message = await interaction.channel.send(
        embed=embed
    )

    await message.add_reaction("👍")
    await message.add_reaction("👎")

    await interaction.response.send_message(
        "✅ Poll created.",
        ephemeral=True
    )


# ============================================================
# AI
# ============================================================

@bot.tree.command(
    name="ai",
    description="Ask the AI a question."
)
@app_commands.describe(
    question="What do you want to ask?"
)
async def ai(
    interaction: discord.Interaction,
    question: str
):
    if gemini_client is None:
        await interaction.response.send_message(
            "❌ Gemini AI is not configured.",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    try:
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=question
        )

        answer = response.text or "❌ Gemini returned no response."

        # Discord message limit
        for i in range(0, len(answer), 2000):
            await interaction.followup.send(
                answer[i:i + 2000]
            )

    except Exception as e:
        print(f"Gemini error: {e}")

        await interaction.followup.send(
            "❌ Gemini encountered an error."
        )
# ============================================================
# ERROR HANDLER
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        message = (
            "❌ You don't have permission to use this command."
        )

    elif isinstance(
        error,
        app_commands.errors.BotMissingPermissions
    ):

        message = (
            "❌ I don't have the permissions required "
            "to perform that action."
        )

    elif isinstance(
        error,
        app_commands.errors.CommandOnCooldown
    ):

        message = (
            "⏳ This command is on cooldown. "
            "Please try again later."
        )

    else:

        print(f"Command error: {error}")

        message = (
            "❌ Something went wrong while running that command."
        )

    try:

        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True
            )

    except Exception as e:
        print(f"Error handler failed: {e}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
