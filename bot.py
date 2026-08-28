import os
import random
import asyncio
import aiohttp
from datetime import datetime, timedelta
import time
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import io
import json


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




# ============================================================
# OPENROUTER AI
# ============================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openrouter/free"
)

# ============================================================
# DATABASE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "pebble.db")


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    conn = get_db()
    cursor = conn.cursor()

    # Warnings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Server settings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_settings (
            guild_id INTEGER PRIMARY KEY,
            suggestions_channel_id INTEGER,
            logs_channel_id INTEGER,
            tickets_category_id INTEGER
        )
    """)

    # Roblox verification
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS roblox_verifications (
            guild_id INTEGER NOT NULL,
            discord_id INTEGER NOT NULL,
            roblox_id INTEGER NOT NULL,
            roblox_username TEXT,
            verified_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, discord_id)
        )
    """)

        # Moderation / server logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_logs (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER
        )
    """)
    
    conn.commit()
    conn.close()

    print("✅ SQLite database initialized.")


init_database()

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
# LEVEL XP
# ============================================================

level_cooldowns = {}


@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    if message.guild is None:
        await bot.process_commands(message)
        return

    user_id = message.author.id
    guild_id = message.guild.id

    cooldown_key = (guild_id, user_id)

    now = datetime.utcnow()

    last_message = level_cooldowns.get(cooldown_key)

    # 60 second XP cooldown
    if (
        last_message is None
        or (now - last_message).total_seconds() >= 60
    ):

        level_cooldowns[cooldown_key] = now

        user_data = get_user_level_data(
            guild_id,
            user_id
        )

        old_level = user_data["level"]

        # Random XP between 10 and 20
        gained_xp = random.randint(10, 20)

        user_data["xp"] += gained_xp

        new_level = calculate_level(
            user_data["xp"]
        )

        user_data["level"] = new_level

        save_levels()

        # ----------------------------------------------------
        # LEVEL UP
        # ----------------------------------------------------

        if new_level > old_level:

            try:
                await message.channel.send(
                    f"🎉 {message.author.mention} "
                    f"leveled up to **Level {new_level}**!"
                )
            except discord.Forbidden:
                pass

    await bot.process_commands(message)


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

    try:
        if TEST_GUILD_ID:
            guild = discord.Object(id=int(TEST_GUILD_ID))

            # Remove old commands from the test guild
            bot.tree.clear_commands(guild=guild)

            # Copy the commands currently defined in this file
            bot.tree.copy_global_to(guild=guild)

            # Sync only the current commands
            synced = await bot.tree.sync(guild=guild)

            # Sync only the current commands
            synced = await bot.tree.sync(guild=guild)

            print(f"Synced {len(synced)} commands to test guild.")

            bot.add_view(TicketPanelView())
            bot.add_view(TicketControlView())

            print("Commands:")
            for command in synced:
                print(f"  /{command.name}")

        else:
            synced = await bot.tree.sync()

            print(f"Synced {len(synced)} global commands.")

            
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
# SERVER CONFIGURATION
# ============================================================

CONFIG_FILE = "server_config.json"


def load_server_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


server_config = load_server_config()


def save_server_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(server_config, f, indent=4)


def get_guild_config(guild_id: int):
    guild_id = str(guild_id)

    if guild_id not in server_config:
        server_config[guild_id] = {}

    return server_config[guild_id]

# ============================================================
# LEVELING SYSTEM
# ============================================================

LEVELS_FILE = "levels.json"

try:
    with open(LEVELS_FILE, "r", encoding="utf-8") as f:
        levels_data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    levels_data = {}


def save_levels():
    with open(LEVELS_FILE, "w", encoding="utf-8") as f:
        json.dump(levels_data, f, indent=4)


def get_user_level_data(guild_id: int, user_id: int):
    guild_id = str(guild_id)
    user_id = str(user_id)

    if guild_id not in levels_data:
        levels_data[guild_id] = {}

    if user_id not in levels_data[guild_id]:
        levels_data[guild_id][user_id] = {
            "xp": 0,
            "level": 0
        }

    return levels_data[guild_id][user_id]


def xp_for_next_level(level: int) -> int:
    return 100 + (level * 50)


def calculate_level(xp: int) -> int:
    level = 0

    while xp >= xp_for_next_level(level):
        xp -= xp_for_next_level(level)
        level += 1

    return level

    
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
# PERSISTENT WARNING SYSTEM
# ============================================================

WARNINGS_FILE = "warnings.json"

try:
    with open(WARNINGS_FILE, "r", encoding="utf-8") as f:
        warnings_data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    warnings_data = {}


def save_warnings():
    with open(WARNINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(warnings_data, f, indent=4)


def get_user_warnings(guild_id: int, user_id: int):
    guild_id = str(guild_id)
    user_id = str(user_id)

    if guild_id not in warnings_data:
        warnings_data[guild_id] = {}

    if user_id not in warnings_data[guild_id]:
        warnings_data[guild_id][user_id] = []

    return warnings_data[guild_id][user_id]


async def send_punishment_dm(
    user: discord.Member,
    guild: discord.Guild,
    punishment: str,
    reason: str,
    warning_count: int
):
    try:
        embed = discord.Embed(
            title="⚠️ Moderation Action",
            description=(
                f"You have received a moderation action "
                f"in **{guild.name}**."
            ),
            color=discord.Color.red()
        )

        embed.add_field(
            name="📋 Punishment",
            value=punishment,
            inline=False
        )

        embed.add_field(
            name="⚠️ Warnings",
            value=f"**{warning_count}**",
            inline=True
        )

        embed.add_field(
            name="📝 Reason",
            value=reason,
            inline=True
        )

        embed.set_footer(
            text="Pebble Moderation"
        )

        await user.send(embed=embed)

    except discord.Forbidden:
        print(
            f"⚠️ Could not DM {user}."
        )

    except Exception as e:
        print(
            f"❌ Punishment DM error: {e}"
        )


async def send_moderation_log(
    guild: discord.Guild,
    title: str,
    description: str,
    color=discord.Color.red()
):

    config_data = get_guild_config(guild.id)

    log_channel_id = config_data.get(
        "log_channel"
    )

    if not log_channel_id:
        return

    channel = guild.get_channel(
        log_channel_id
    )

    if channel is None:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.utcnow()
    )

    try:
        await channel.send(embed=embed)

    except discord.Forbidden:
        print(
            f"❌ Cannot send moderation log in #{channel.name}."
        )

    except Exception as e:
        print(
            f"❌ Moderation log error: {e}"
        )


async def temporary_unban(
    guild_id: int,
    user_id: int,
    seconds: int
):

    await asyncio.sleep(seconds)

    guild = bot.get_guild(guild_id)

    if guild is None:
        return

    try:
        user = await bot.fetch_user(user_id)

        await guild.unban(
            user,
            reason="Temporary punishment expired"
        )

        await send_moderation_log(
            guild,
            "🔓 Temporary Ban Expired",
            f"**{user}** has been automatically unbanned.",
            discord.Color.green()
        )

    except discord.NotFound:
        pass

    except discord.Forbidden:
        print(
            f"❌ Cannot unban user {user_id}."
        )

    except Exception as e:
        print(
            f"❌ Automatic unban error: {e}"
        )


# ============================================================
# /WARN
# ============================================================

@bot.tree.command(
    name="warn",
    description="Warn a member."
)
@app_commands.describe(
    user="Member to warn.",
    reason="Reason for the warning."
)
@app_commands.checks.has_permissions(
    moderate_members=True
)
async def warn(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "No reason provided"
):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    if user.bot:
        await interaction.response.send_message(
            "❌ You cannot warn a bot.",
            ephemeral=True
        )
        return

    if user == interaction.user:
        await interaction.response.send_message(
            "❌ You cannot warn yourself.",
            ephemeral=True
        )
        return

    if user.top_role >= interaction.user.top_role:
        await interaction.response.send_message(
            "❌ You cannot warn someone with an equal or higher role.",
            ephemeral=True
        )
        return

    # --------------------------------------------------------
    # SAVE WARNING
    # --------------------------------------------------------

    user_warnings = get_user_warnings(
        guild.id,
        user.id
    )

    user_warnings.append({
        "reason": reason,
        "moderator_id": interaction.user.id,
        "moderator_name": str(interaction.user),
        "timestamp": datetime.utcnow().isoformat()
    })

    save_warnings()

    warning_count = len(user_warnings)

    # --------------------------------------------------------
    # DETERMINE PUNISHMENT
    # --------------------------------------------------------

    punishment = "Warning"
    punishment_text = "⚠️ You have received a warning."

    if warning_count == 3:

        punishment = "Timeout for 1 hour"
        punishment_text = "🔇 You have been timed out for 1 hour."

    elif warning_count == 4:

        punishment = "Kick"
        punishment_text = "👢 You have been kicked from the server."

    elif warning_count == 5:

        punishment = "Temporary ban for 1 day"
        punishment_text = "🔨 You have been temporarily banned for 1 day."

    elif warning_count >= 6:

        punishment = "Ban for 1 year"
        punishment_text = "🔨 You have been banned for 1 year."

    # --------------------------------------------------------
    # SEND DM BEFORE PUNISHMENT
    # --------------------------------------------------------

    await send_punishment_dm(
        user=user,
        guild=guild,
        punishment=punishment,
        reason=reason,
        warning_count=warning_count
    )

    # --------------------------------------------------------
    # APPLY PUNISHMENT
    # --------------------------------------------------------

    punishment_success = True

    try:

        if warning_count == 3:

            await user.timeout(
                discord.utils.utcnow() + timedelta(hours=1),
                reason=f"Reached 3 warnings: {reason}"
            )

        elif warning_count == 4:

            await user.kick(
                reason=f"Reached 4 warnings: {reason}"
            )

        elif warning_count == 5:

            await user.ban(
                reason=f"Reached 5 warnings: {reason}"
            )

            asyncio.create_task(
                temporary_unban(
                    guild.id,
                    user.id,
                    86400
                )
            )

        elif warning_count >= 6:

            await user.ban(
                reason=f"Reached 6+ warnings: {reason}"
            )

            asyncio.create_task(
                temporary_unban(
                    guild.id,
                    user.id,
                    31536000
                )
            )

    except discord.Forbidden:

        punishment_success = False

        print(
            f"❌ Bot cannot punish {user}."
        )

    except Exception as e:

        punishment_success = False

        print(
            f"❌ Punishment error: {e}"
        )

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    await send_moderation_log(
        guild,
        "⚠️ Warning / Punishment",
        (
            f"**User:** {user.mention} (`{user.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Warnings:** `{warning_count}`\n"
            f"**Punishment:** `{punishment}`\n"
            f"**Reason:** {reason}\n"
            f"**Punishment successful:** "
            f"`{'Yes' if punishment_success else 'No'}`"
        )
    )

    # --------------------------------------------------------
    # RESPONSE
    # --------------------------------------------------------

    if warning_count < 3:

        await interaction.response.send_message(
            f"⚠️ **{user}** has been warned.\n"
            f"**Warnings:** `{warning_count}`\n"
            f"**Reason:** {reason}\n"
            f"📩 The user was sent a DM."
        )

    else:

        await interaction.response.send_message(
            f"⚠️ **{user}** received warning `{warning_count}`.\n"
            f"**Punishment:** {punishment}\n"
            f"**Reason:** {reason}\n"
            f"📩 The user was sent a DM."
        )


# ============================================================
# /WARNINGS
# ============================================================

@bot.tree.command(
    name="warnings",
    description="View a member's warnings."
)
@app_commands.describe(
    user="Member whose warnings you want to view."
)
@app_commands.checks.has_permissions(
    moderate_members=True
)
async def warnings_command(
    interaction: discord.Interaction,
    user: discord.Member
):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    user_warnings = get_user_warnings(
        guild.id,
        user.id
    )

    if not user_warnings:

        await interaction.response.send_message(
            f"✅ **{user}** has no warnings.",
            ephemeral=True
        )
        return

    lines = []

    for index, warning in enumerate(
        user_warnings,
        start=1
    ):

        timestamp = warning.get(
            "timestamp",
            "Unknown"
        )

        try:
            dt = datetime.fromisoformat(
                timestamp
            )

            time_text = format_dt(dt)

        except Exception:
            time_text = "Unknown"

        lines.append(
            f"**#{index}** — {warning.get('reason', 'No reason')}\n"
            f"Moderator: `{warning.get('moderator_name', 'Unknown')}`\n"
            f"Time: {time_text}"
        )

    embed = discord.Embed(
        title=f"⚠️ Warnings — {user}",
        description="\n\n".join(lines[:10]),
        color=discord.Color.orange()
    )

    embed.set_thumbnail(
        url=user.display_avatar.url
    )

    embed.set_footer(
        text=f"Total warnings: {len(user_warnings)}"
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# /CLEARWARNS
# ============================================================

@bot.tree.command(
    name="clearwarns",
    description="Clear all warnings for a member."
)
@app_commands.describe(
    user="Member whose warnings should be cleared."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def clearwarns(
    interaction: discord.Interaction,
    user: discord.Member
):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    guild_id = str(guild.id)
    user_id = str(user.id)

    if (
        guild_id not in warnings_data
        or user_id not in warnings_data[guild_id]
        or not warnings_data[guild_id][user_id]
    ):

        await interaction.response.send_message(
            f"✅ **{user}** has no warnings.",
            ephemeral=True
        )
        return

    old_count = len(
        warnings_data[guild_id][user_id]
    )

    warnings_data[guild_id][user_id] = []

    save_warnings()

    await send_moderation_log(
        guild,
        "🧹 Warnings Cleared",
        (
            f"**User:** {user.mention}\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Warnings removed:** `{old_count}`"
        ),
        discord.Color.green()
    )

    await interaction.response.send_message(
        f"✅ Cleared **{old_count}** warnings from {user.mention}.",
        ephemeral=True
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
# UPGRADED TICKET SYSTEM
# ============================================================

TICKET_CATEGORY_NAME = os.getenv(
    "TICKET_CATEGORY_NAME",
    "Tickets"
)

TICKET_LOG_CHANNEL_NAME = "ticket-transcripts"


# ============================================================
# FIND TICKET CATEGORY
# ============================================================

async def get_ticket_category(guild: discord.Guild):

    category = discord.utils.get(
        guild.categories,
        name=TICKET_CATEGORY_NAME
    )

    if category is None:

        try:
            category = await guild.create_category(
                TICKET_CATEGORY_NAME,
                reason="Ticket system"
            )
        except discord.Forbidden:
            return None

    return category


# ============================================================
# FIND TICKET LOG CHANNEL
# ============================================================

async def get_ticket_log_channel(
    guild: discord.Guild
):

    # First try the configured server log channel
    channel = await get_log_channel(guild)

    if channel:
        return channel

    # Otherwise use/create ticket-transcripts
    channel = discord.utils.get(
        guild.text_channels,
        name=TICKET_LOG_CHANNEL_NAME
    )

    if channel is None:

        try:
            channel = await guild.create_text_channel(
                TICKET_LOG_CHANNEL_NAME,
                reason="Ticket transcript channel"
            )
        except discord.Forbidden:
            return None

    return channel


# ============================================================
# CHECK IF USER ALREADY HAS A TICKET
# ============================================================

def get_user_ticket(
    guild: discord.Guild,
    user: discord.Member
):

    for channel in guild.text_channels:

        if not channel.name.startswith("ticket-"):
            continue

        if channel.topic == f"ticket_owner:{user.id}":
            return channel

    return None


# ============================================================
# CREATE TICKET
# ============================================================

async def create_ticket(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ Tickets can only be created in a server.",
            ephemeral=True
        )
        return

    existing = get_user_ticket(
        guild,
        interaction.user
    )

    if existing:

        await interaction.response.send_message(
            f"❌ You already have a ticket: {existing.mention}",
            ephemeral=True
        )
        return

    category = await get_ticket_category(
        guild
    )

    if category is None:

        await interaction.response.send_message(
            "❌ I couldn't create the ticket category.",
            ephemeral=True
        )
        return

    # Find bot member
    bot_member = guild.me

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        ),

        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True
        )
    }

    # Give staff/moderators access
    for role in guild.roles:

        if (
            role.permissions.manage_messages
            or role.permissions.moderate_members
            or role.permissions.administrator
        ):

            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True
            )

    if bot_member:

        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
            attach_files=True
        )

    # Create channel
    channel = await guild.create_text_channel(
        name=f"ticket-{interaction.user.name}",
        category=category,
        overwrites=overwrites,
        topic=f"ticket_owner:{interaction.user.id}",
        reason=f"Ticket created by {interaction.user}"
    )

    embed = discord.Embed(
        title="🎫 Support Ticket",
        description=(
            f"Welcome {interaction.user.mention}!\n\n"
            "Please describe your issue and a staff member "
            "will assist you.\n\n"
            "Use the buttons below to manage this ticket."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="👤 Ticket Owner",
        value=interaction.user.mention,
        inline=True
    )

    embed.add_field(
        name="📌 Status",
        value="🟢 Open",
        inline=True
    )

    embed.set_footer(
        text="A transcript will be created when this ticket is closed."
    )

    await channel.send(
        content=interaction.user.mention,
        embed=embed,
        view=TicketControlView()
    )

    await interaction.response.send_message(
        f"✅ Your ticket has been created: {channel.mention}",
        ephemeral=True
    )


# ============================================================
# TICKET PANEL VIEW
# ============================================================

class TicketPanelView(
    discord.ui.View
):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="Create Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="pebble_create_ticket"
    )
    async def create_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await create_ticket(
            interaction
        )


# ============================================================
# TICKET CONTROL VIEW
# ============================================================

class TicketControlView(
    discord.ui.View
):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="Claim",
        emoji="🙋",
        style=discord.ButtonStyle.success,
        custom_id="pebble_ticket_claim"
    )
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not is_moderator(interaction):

            await interaction.response.send_message(
                "❌ You need moderation permissions to claim tickets.",
                ephemeral=True
            )
            return

        if not interaction.channel.name.startswith(
            "ticket-"
        ):

            await interaction.response.send_message(
                "❌ This isn't a ticket channel.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎫 Ticket Claimed",
            description=(
                f"This ticket has been claimed by "
                f"{interaction.user.mention}."
            ),
            color=discord.Color.green()
        )

        await interaction.response.send_message(
            embed=embed
        )


    @discord.ui.button(
        label="Close",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="pebble_ticket_close"
    )
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not interaction.channel.name.startswith(
            "ticket-"
        ):

            await interaction.response.send_message(
                "❌ This isn't a ticket channel.",
                ephemeral=True
            )
            return

        if not is_moderator(interaction):

            # Allow ticket owner to close their own ticket
            owner_id = None

            if interaction.channel.topic:
                if interaction.channel.topic.startswith(
                    "ticket_owner:"
                ):

                    try:
                        owner_id = int(
                            interaction.channel.topic.split(
                                ":"
                            )[1]
                        )
                    except (ValueError, IndexError):
                        owner_id = None

            if owner_id != interaction.user.id:

                await interaction.response.send_message(
                    "❌ You can only close your own ticket.",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            "🔒 Creating transcript and closing ticket..."
        )

        await asyncio.sleep(2)

        await close_ticket(
            interaction.channel,
            interaction.user
        )


# ============================================================
# CREATE TICKET PANEL
# ============================================================

@bot.tree.command(
    name="ticket-panel",
    description="Send the ticket creation panel."
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def ticket_panel(
    interaction: discord.Interaction
):

    embed = discord.Embed(
        title="🎫 Support Center",
        description=(
            "Need help?\n\n"
            "Click **Create Ticket** below to open a private "
            "support ticket.\n\n"
            "A ticket will only be visible to you and staff."
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="📋 Ticket Rules",
        value=(
            "• Don't spam tickets\n"
            "• Explain your issue clearly\n"
            "• Be respectful to staff\n"
            "• Close your ticket when finished"
        ),
        inline=False
    )

    embed.set_footer(
        text="Pebble Support"
    )

    await interaction.response.send_message(
        embed=embed,
        view=TicketPanelView()
    )


# ============================================================
# OLD /ticket COMMAND
# ============================================================

@bot.tree.command(
    name="ticket",
    description="Create a support ticket."
)
async def ticket(
    interaction: discord.Interaction
):

    await create_ticket(
        interaction
    )


# ============================================================
# CREATE TRANSCRIPT
# ============================================================

async def create_ticket_transcript(
    channel: discord.TextChannel
):

    messages = []

    try:

        async for message in channel.history(
            limit=None,
            oldest_first=True
        ):

            timestamp = message.created_at.strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )

            author = (
                f"{message.author} "
                f"({message.author.id})"
            )

            content = message.content or ""

            line = (
                f"[{timestamp}] "
                f"{author}: "
                f"{content}"
            )

            if message.attachments:

                attachments = ", ".join(
                    attachment.url
                    for attachment in message.attachments
                )

                line += (
                    f" | Attachments: {attachments}"
                )

            messages.append(line)

    except discord.Forbidden:

        return None

    transcript_text = (
        f"PEBBLE TICKET TRANSCRIPT\n"
        f"=========================\n\n"
        f"Server: {channel.guild.name}\n"
        f"Server ID: {channel.guild.id}\n"
        f"Channel: #{channel.name}\n"
        f"Channel ID: {channel.id}\n"
        f"Created: {channel.created_at}\n\n"
        f"=========================\n\n"
    )

    transcript_text += "\n".join(
        messages
    )

    return transcript_text


# ============================================================
# CLOSE TICKET
# ============================================================

async def close_ticket(
    channel: discord.TextChannel,
    closed_by: discord.Member
):

    transcript = await create_ticket_transcript(
        channel
    )

    log_channel = await get_ticket_log_channel(
        channel.guild
    )

    if transcript is not None and log_channel:

        transcript_file = discord.File(
            io.BytesIO(
                transcript.encode(
                    "utf-8"
                )
            ),
            filename=f"{channel.name}-transcript.txt"
        )

        embed = discord.Embed(
            title="📄 Ticket Transcript",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )

        embed.add_field(
            name="Ticket",
            value=f"`{channel.name}`",
            inline=True
        )

        embed.add_field(
            name="Closed By",
            value=closed_by.mention,
            inline=True
        )

        embed.add_field(
            name="Channel ID",
            value=f"`{channel.id}`",
            inline=True
        )

        await log_channel.send(
            embed=embed,
            file=transcript_file
        )

    elif log_channel:

        await log_channel.send(
            f"⚠️ Could not create a transcript for "
            f"`{channel.name}`."
        )

    # Delete ticket
    try:

        await channel.delete(
            reason=f"Ticket closed by {closed_by}"
        )

    except discord.Forbidden:

        print(
            f"❌ Cannot delete ticket channel "
            f"{channel.name}"
        )

    except discord.HTTPException as e:

        print(
            f"❌ Error deleting ticket: {e}"
        )


# ============================================================
# /CLOSE
# ============================================================

@bot.tree.command(
    name="close",
    description="Close the current ticket and save a transcript."
)
async def close(
    interaction: discord.Interaction
):

    channel = interaction.channel

    if not channel.name.startswith(
        "ticket-"
    ):

        await interaction.response.send_message(
            "❌ This isn't a ticket channel.",
            ephemeral=True
        )
        return

    if not is_moderator(interaction):

        owner_id = None

        if channel.topic:

            if channel.topic.startswith(
                "ticket_owner:"
            ):

                try:

                    owner_id = int(
                        channel.topic.split(":")[1]
                    )

                except (ValueError, IndexError):

                    owner_id = None

        if owner_id != interaction.user.id:

            await interaction.response.send_message(
                "❌ You can only close your own ticket.",
                ephemeral=True
            )
            return

    await interaction.response.send_message(
        "🔒 Creating transcript and closing ticket..."
    )

    await asyncio.sleep(2)

    await close_ticket(
        channel,
        interaction.user
    )


# ============================================================
# /CLAIM
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

    if not interaction.channel.name.startswith(
        "ticket-"
    ):

        await interaction.response.send_message(
            "❌ This isn't a ticket channel.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"🎫 This ticket has been claimed by "
        f"{interaction.user.mention}."
    )


# ============================================================
# /ADD
# ============================================================

@bot.tree.command(
    name="add",
    description="Add a user to the current ticket."
)
@app_commands.describe(
    user="User to add to the ticket."
)
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

    if not interaction.channel.name.startswith(
        "ticket-"
    ):

        await interaction.response.send_message(
            "❌ This isn't a ticket channel.",
            ephemeral=True
        )
        return

    await interaction.channel.set_permissions(
        user,
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True
    )

    await interaction.response.send_message(
        f"✅ Added {user.mention} to the ticket."
    )


# ============================================================
# /REMOVE
# ============================================================

@bot.tree.command(
    name="remove",
    description="Remove a user from the current ticket."
)
@app_commands.describe(
    user="User to remove from the ticket."
)
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

    if not interaction.channel.name.startswith(
        "ticket-"
    ):

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

# ============================================================
# AI - OPENROUTER
# ============================================================

@bot.tree.command(
    name="ai",
    description="Ask Pebble AI a question."
)
@app_commands.describe(
    question="What do you want to ask?"
)
async def ai(
    interaction: discord.Interaction,
    question: str
):

    if not OPENROUTER_API_KEY:
        await interaction.response.send_message(
            "❌ OpenRouter is not configured.",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    try:
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Pebble AI, the helpful AI assistant "
                        "for a Discord bot called Pebble. "
                        "Be friendly, concise, and helpful."
                    )
                },
                {
                    "role": "user",
                    "content": question
                }
            ],
            "max_tokens": 1000
        }

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://discord.com/",
            "X-Title": "Pebble Discord Bot"
        }

        timeout = aiohttp.ClientTimeout(total=90)

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload
            ) as response:

                data = await response.json()

                if response.status != 200:
                    print(
                        f"OpenRouter HTTP {response.status}: "
                        f"{data}"
                    )

                    error_message = (
                        data.get("error", {})
                        .get("message", "Unknown error")
                    )

                    await interaction.followup.send(
                        f"❌ AI error: {error_message}"
                    )
                    return

        answer = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )

        if not answer:
            await interaction.followup.send(
                "❌ The AI returned an empty response."
            )
            return

        # Discord messages are limited to 2000 characters.
        if len(answer) <= 2000:

            await interaction.followup.send(answer)

        else:

            for i in range(0, len(answer), 2000):
                await interaction.followup.send(
                    answer[i:i + 2000]
                )

    except asyncio.TimeoutError:

        await interaction.followup.send(
            "⏳ The AI took too long to respond."
        )

    except Exception as e:

        print(f"OpenRouter error: {e}")

        await interaction.followup.send(
            "❌ Something went wrong while contacting the AI."
        )

# ============================================================
# GIVEAWAYS
# ============================================================

giveaways = {}


def parse_duration(duration: str):
    """
    Convert durations like:
    10s
    5m
    2h
    1d
    """

    duration = duration.lower().strip()

    units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400
    }

    if len(duration) < 2:
        return None

    unit = duration[-1]

    if unit not in units:
        return None

    try:
        amount = int(duration[:-1])
    except ValueError:
        return None

    if amount <= 0:
        return None

    return amount * units[unit]


class GiveawayView(discord.ui.View):

    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(
        label="🎉 Enter Giveaway",
        style=discord.ButtonStyle.success,
        custom_id="giveaway_enter"
    )
    async def enter(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        giveaway = giveaways.get(self.message_id)

        if giveaway is None:
            await interaction.response.send_message(
                "❌ This giveaway no longer exists.",
                ephemeral=True
            )
            return

        if time.time() >= giveaway["ends_at"]:
            await interaction.response.send_message(
                "❌ This giveaway has already ended.",
                ephemeral=True
            )
            return

        if interaction.user.id in giveaway["entries"]:

            giveaway["entries"].remove(
                interaction.user.id
            )

            await interaction.response.send_message(
                "❌ You left the giveaway.",
                ephemeral=True
            )

        else:

            giveaway["entries"].append(
                interaction.user.id
            )

            await interaction.response.send_message(
                "🎉 You entered the giveaway!",
                ephemeral=True
            )


async def finish_giveaway(message_id: int):

    giveaway = giveaways.get(message_id)

    if giveaway is None:
        return

    channel = bot.get_channel(
        giveaway["channel_id"]
    )

    if channel is None:
        return

    try:
        message = await channel.fetch_message(
            message_id
        )
    except discord.NotFound:
        giveaways.pop(message_id, None)
        return
    except discord.HTTPException:
        return

    entries = giveaway["entries"]

    winners_count = min(
        giveaway["winners"],
        len(entries)
    )

    if winners_count == 0:

        embed = discord.Embed(
            title="🎉 Giveaway Ended",
            description=(
                f"**Prize:** {giveaway['prize']}\n\n"
                "😢 Nobody entered the giveaway."
            ),
            color=discord.Color.red()
        )

        await message.edit(
            embed=embed,
            view=None
        )

        giveaways.pop(message_id, None)
        return

    winner_ids = random.sample(
        entries,
        winners_count
    )

    mentions = []

    for user_id in winner_ids:
        mentions.append(f"<@{user_id}>")

    winner_text = ", ".join(mentions)

    embed = discord.Embed(
        title="🎉 Giveaway Ended!",
        description=(
            f"**Prize:** {giveaway['prize']}\n\n"
            f"🏆 **Winner(s):** {winner_text}\n\n"
            f"👥 Entries: **{len(entries)}**"
        ),
        color=discord.Color.gold()
    )

    await message.edit(
        embed=embed,
        view=None
    )

    await channel.send(
        f"🎉 Congratulations {winner_text}! "
        f"You won **{giveaway['prize']}**!"
    )

    giveaways.pop(message_id, None)


@bot.tree.command(
    name="giveaway",
    description="Start a giveaway."
)
@app_commands.describe(
    duration="Duration, e.g. 10s, 5m, 2h, or 1d.",
    winners="Number of winners.",
    prize="What are you giving away?"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def giveaway(
    interaction: discord.Interaction,
    duration: str,
    winners: app_commands.Range[int, 1, 20],
    prize: str
):

    seconds = parse_duration(duration)

    if seconds is None:

        await interaction.response.send_message(
            "❌ Invalid duration.\n\n"
            "Use formats such as:\n"
            "`10s` = 10 seconds\n"
            "`5m` = 5 minutes\n"
            "`2h` = 2 hours\n"
            "`1d` = 1 day",
            ephemeral=True
        )

        return

    if seconds > 30 * 86400:

        await interaction.response.send_message(
            "❌ The maximum giveaway duration is 30 days.",
            ephemeral=True
        )

        return

    end_time = time.time() + seconds

    embed = discord.Embed(
        title="🎉 GIVEAWAY",
        description=(
            f"🎁 **Prize:** {prize}\n\n"
            f"🏆 **Winners:** {winners}\n"
            f"⏰ **Ends:** <t:{int(end_time)}:R>\n\n"
            "Click the button below to enter!"
        ),
        color=discord.Color.gold()
    )

    embed.set_footer(
        text=f"Hosted by {interaction.user}"
    )

    await interaction.response.send_message(
        embed=embed
    )

    message = await interaction.original_response()

    giveaways[message.id] = {
        "channel_id": interaction.channel.id,
        "prize": prize,
        "winners": winners,
        "entries": [],
        "ends_at": end_time,
        "host": interaction.user.id
    }

    await message.edit(
        view=GiveawayView(message.id)
    )

    await asyncio.sleep(seconds)

    await finish_giveaway(message.id)


# ============================================================
# GIVEAWAY END
# ============================================================

@bot.tree.command(
    name="giveaway-end",
    description="End a giveaway early."
)
@app_commands.describe(
    message_id="The message ID of the giveaway."
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def giveaway_end(
    interaction: discord.Interaction,
    message_id: str
):

    try:
        giveaway_id = int(message_id)
    except ValueError:

        await interaction.response.send_message(
            "❌ Invalid message ID.",
            ephemeral=True
        )

        return

    if giveaway_id not in giveaways:

        await interaction.response.send_message(
            "❌ Giveaway not found or already ended.",
            ephemeral=True
        )

        return

    await interaction.response.send_message(
        "🏁 Ending giveaway...",
        ephemeral=True
    )

    await finish_giveaway(giveaway_id)


# ============================================================
# GIVEAWAY REROLL
# ============================================================

@bot.tree.command(
    name="giveaway-reroll",
    description="Reroll a giveaway winner."
)
@app_commands.describe(
    message_id="The message ID of the giveaway."
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def giveaway_reroll(
    interaction: discord.Interaction,
    message_id: str
):

    try:
        giveaway_id = int(message_id)
    except ValueError:

        await interaction.response.send_message(
            "❌ Invalid message ID.",
            ephemeral=True
        )

        return

    # Reroll requires us to keep the completed giveaway.
    # This version only works while the giveaway is active.

    giveaway_data = giveaways.get(giveaway_id)

    if giveaway_data is None:

        await interaction.response.send_message(
            "❌ Giveaway not found.",
            ephemeral=True
        )

        return

    if not giveaway_data["entries"]:

        await interaction.response.send_message(
            "❌ There are no entries to reroll.",
            ephemeral=True
        )

        return

    winner = random.choice(
        giveaway_data["entries"]
    )

    await interaction.response.send_message(
        f"🔄 New winner: <@{winner}>!\n"
        f"🎁 Prize: **{giveaway_data['prize']}**"
    )
# ============================================================
# SUGGESTIONS
# ============================================================

SUGGESTIONS_CHANNEL_NAME = "suggestions"


class SuggestionView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.upvotes = set()
        self.downvotes = set()

    @discord.ui.button(
        label="0",
        emoji="👍",
        style=discord.ButtonStyle.success,
        custom_id="suggestion_upvote"
    )
    async def upvote(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        user_id = interaction.user.id

        # Remove downvote if they already downvoted
        self.downvotes.discard(user_id)

        if user_id in self.upvotes:
            self.upvotes.remove(user_id)
        else:
            self.upvotes.add(user_id)

        button.label = str(len(self.upvotes))

        # Update downvote button
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "suggestion_downvote":
                    child.label = str(len(self.downvotes))

        await interaction.response.edit_message(view=self)

    @discord.ui.button(
        label="0",
        emoji="👎",
        style=discord.ButtonStyle.danger,
        custom_id="suggestion_downvote"
    )
    async def downvote(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        user_id = interaction.user.id

        # Remove upvote if they already upvoted
        self.upvotes.discard(user_id)

        if user_id in self.downvotes:
            self.downvotes.remove(user_id)
        else:
            self.downvotes.add(user_id)

        button.label = str(len(self.downvotes))

        # Update upvote button
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "suggestion_upvote":
                    child.label = str(len(self.upvotes))

        await interaction.response.edit_message(view=self)

    @discord.ui.button(
        label="Approve",
        emoji="✅",
        style=discord.ButtonStyle.primary,
        custom_id="suggestion_approve"
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not is_moderator(interaction):
            await interaction.response.send_message(
                "❌ You need moderation permissions to approve suggestions.",
                ephemeral=True
            )
            return

        embed = interaction.message.embeds[0]

        embed.color = discord.Color.green()

        embed.add_field(
            name="Status",
            value=f"✅ Approved by {interaction.user.mention}",
            inline=False
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            embed=embed,
            view=self
        )

    @discord.ui.button(
        label="Deny",
        emoji="❌",
        style=discord.ButtonStyle.secondary,
        custom_id="suggestion_deny"
    )
    async def deny(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not is_moderator(interaction):
            await interaction.response.send_message(
                "❌ You need moderation permissions to deny suggestions.",
                ephemeral=True
            )
            return

        embed = interaction.message.embeds[0]

        embed.color = discord.Color.red()

        embed.add_field(
            name="Status",
            value=f"❌ Denied by {interaction.user.mention}",
            inline=False
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            embed=embed,
            view=self
        )


@bot.tree.command(
    name="suggest",
    description="Submit a suggestion for the server."
)
@app_commands.describe(
    suggestion="What would you like to suggest?"
)
async def suggest(
    interaction: discord.Interaction,
    suggestion: str
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Find the suggestions channel
    channel = discord.utils.get(
        interaction.guild.text_channels,
        name=SUGGESTIONS_CHANNEL_NAME
    )

    # Create it if it doesn't exist
    if channel is None:
        try:
            channel = await interaction.guild.create_text_channel(
                SUGGESTIONS_CHANNEL_NAME,
                reason="Create suggestions channel"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to create the suggestions channel.",
                ephemeral=True
            )
            return

    # Create suggestion embed
    embed = discord.Embed(
        title="💡 New Suggestion",
        description=suggestion,
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="Submitted by",
        value=interaction.user.mention,
        inline=True
    )

    embed.add_field(
        name="Status",
        value="🟡 Pending",
        inline=True
    )

    embed.set_thumbnail(
        url=interaction.user.display_avatar.url
    )

    embed.set_footer(
        text=f"User ID: {interaction.user.id}"
    )

    # Create buttons
    view = SuggestionView(interaction.user.id)

    # Send suggestion
    await channel.send(
        embed=embed,
        view=view
    )

    await interaction.followup.send(
        f"✅ Your suggestion has been submitted in {channel.mention}!",
        ephemeral=True
    )
# ============================================================
# SERVER LOGGING SYSTEM
# ============================================================

async def get_log_channel(guild: discord.Guild):
    """
    Get the configured logging channel for a server.
    Returns None if logging is disabled or the channel no longer exists.
    """

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT channel_id
        FROM server_logs
        WHERE guild_id = ?
    """, (guild.id,))

    row = cursor.fetchone()

    conn.close()

    if not row:
        return None

    channel_id = row["channel_id"]

    channel = guild.get_channel(channel_id)

    return channel


async def send_log(
    guild: discord.Guild,
    embed: discord.Embed
):
    """
    Send an embed to the configured server log channel.
    """

    channel = await get_log_channel(guild)

    if channel is None:
        return

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        print(
            f"❌ Cannot send logs in #{channel.name} "
            f"({guild.name})"
        )
    except discord.HTTPException as e:
        print(f"❌ Failed to send log: {e}")


# ============================================================
# /LOGS SETUP
# ============================================================

@bot.tree.command(
    name="logs-setup",
    description="Set the current channel as the server log channel."
)
@app_commands.checks.has_permissions(administrator=True)
async def logs_setup(
    interaction: discord.Interaction
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    channel = interaction.channel

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO server_logs (
            guild_id,
            channel_id
        )
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET channel_id = excluded.channel_id
    """, (
        interaction.guild.id,
        channel.id
    ))

    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="✅ Logging Enabled",
        description=(
            f"Server logs will now be sent to {channel.mention}."
        ),
        color=discord.Color.green()
    )

    embed.add_field(
        name="Events",
        value=(
            "👋 Member joins/leaves\n"
            "🔨 Bans\n"
            "👢 Kicks\n"
            "⏳ Timeouts\n"
            "⚠️ Warnings\n"
            "🗑️ Deleted messages\n"
            "✏️ Edited messages"
        ),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )

    # Test log
    test_embed = discord.Embed(
        title="🧪 Logging Test",
        description=(
            f"Logging has been configured by "
            f"{interaction.user.mention}."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    await channel.send(embed=test_embed)


# ============================================================
# /LOGS DISABLE
# ============================================================

@bot.tree.command(
    name="logs-disable",
    description="Disable server logging."
)
@app_commands.checks.has_permissions(administrator=True)
async def logs_disable(
    interaction: discord.Interaction
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM server_logs
        WHERE guild_id = ?
    """, (
        interaction.guild.id,
    ))

    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    if deleted == 0:
        await interaction.response.send_message(
            "❌ Logging is not currently enabled.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        "🔕 Server logging has been disabled.",
        ephemeral=True
    )


# ============================================================
# /LOGS TEST
# ============================================================

@bot.tree.command(
    name="logs-test",
    description="Test the server logging system."
)
@app_commands.checks.has_permissions(administrator=True)
async def logs_test(
    interaction: discord.Interaction
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    channel = await get_log_channel(
        interaction.guild
    )

    if channel is None:
        await interaction.response.send_message(
            "❌ Logging is not configured.\n"
            "Run `/logs-setup` in the channel you want to use.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🧪 Logging Test",
        description=(
            "The logging system is working correctly."
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="Server",
        value=interaction.guild.name,
        inline=True
    )

    embed.add_field(
        name="Tested By",
        value=interaction.user.mention,
        inline=True
    )

    await channel.send(
        embed=embed
    )

    await interaction.response.send_message(
        f"✅ Test log sent to {channel.mention}.",
        ephemeral=True
    )


# ============================================================
# MEMBER JOIN
# ============================================================

@bot.event
async def on_member_join(member: discord.Member):

    embed = discord.Embed(
        title="👋 Member Joined",
        description=(
            f"{member.mention} joined the server."
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.add_field(
        name="User",
        value=f"{member} (`{member.id}`)",
        inline=False
    )

    embed.add_field(
        name="Account Created",
        value=format_dt(member.created_at),
        inline=True
    )

    embed.add_field(
        name="Member Count",
        value=str(member.guild.member_count),
        inline=True
    )

    await send_log(
        member.guild,
        embed
    )


# ============================================================
# MEMBER LEAVE
# ============================================================

@bot.event
async def on_member_remove(member: discord.Member):

    embed = discord.Embed(
        title="👋 Member Left",
        description=(
            f"**{member}** left the server."
        ),
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.add_field(
        name="User",
        value=f"{member} (`{member.id}`)",
        inline=False
    )

    await send_log(
        member.guild,
        embed
    )


# ============================================================
# MESSAGE DELETE
# ============================================================

@bot.event
async def on_message_delete(message: discord.Message):

    if message.guild is None:
        return

    # Ignore bot messages
    if message.author.bot:
        return

    content = message.content

    if not content:
        content = "*No text content*"

    # Prevent massive embeds
    if len(content) > 1000:
        content = content[:1000] + "..."

    embed = discord.Embed(
        title="🗑️ Message Deleted",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="Author",
        value=f"{message.author.mention} (`{message.author.id}`)",
        inline=False
    )

    embed.add_field(
        name="Channel",
        value=message.channel.mention,
        inline=True
    )

    embed.add_field(
        name="Content",
        value=content,
        inline=False
    )

    if message.attachments:
        attachments = "\n".join(
            attachment.filename
            for attachment in message.attachments
        )

        embed.add_field(
            name="Attachments",
            value=attachments[:1000],
            inline=False
        )

    await send_log(
        message.guild,
        embed
    )


# ============================================================
# MESSAGE EDIT
# ============================================================

@bot.event
async def on_message_edit(
    before: discord.Message,
    after: discord.Message
):

    if before.guild is None:
        return

    if before.author.bot:
        return

    if before.content == after.content:
        return

    old_content = before.content or "*Empty*"
    new_content = after.content or "*Empty*"

    if len(old_content) > 1000:
        old_content = old_content[:1000] + "..."

    if len(new_content) > 1000:
        new_content = new_content[:1000] + "..."

    embed = discord.Embed(
        title="✏️ Message Edited",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="Author",
        value=before.author.mention,
        inline=False
    )

    embed.add_field(
        name="Channel",
        value=before.channel.mention,
        inline=True
    )

    embed.add_field(
        name="Before",
        value=old_content,
        inline=False
    )

    embed.add_field(
        name="After",
        value=new_content,
        inline=False
    )

    await send_log(
        before.guild,
        embed
    )


# ============================================================
# MEMBER BAN
# ============================================================

@bot.event
async def on_member_ban(
    guild: discord.Guild,
    user: discord.User
):

    embed = discord.Embed(
        title="🔨 Member Banned",
        description=f"**{user}** was banned.",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )

    embed.set_thumbnail(
        url=user.display_avatar.url
    )

    embed.add_field(
        name="User",
        value=f"{user} (`{user.id}`)",
        inline=False
    )

    await send_log(
        guild,
        embed
    )


# ============================================================
# MEMBER UNBAN
# ============================================================

@bot.event
async def on_member_unban(
    guild: discord.Guild,
    user: discord.User
):

    embed = discord.Embed(
        title="🔓 Member Unbanned",
        description=f"**{user}** was unbanned.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="User",
        value=f"{user} (`{user.id}`)",
        inline=False
    )

    await send_log(
        guild,
        embed
    )


# ============================================================
# MEMBER UPDATE
# ============================================================

@bot.event
async def on_member_update(
    before: discord.Member,
    after: discord.Member
):

    # Timeout changes
    if before.communication_disabled_until != after.communication_disabled_until:

        if after.communication_disabled_until is not None:

            embed = discord.Embed(
                title="⏳ Member Timed Out",
                description=(
                    f"{after.mention} was timed out."
                ),
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )

            embed.add_field(
                name="User",
                value=f"{after} (`{after.id}`)",
                inline=False
            )

            embed.add_field(
                name="Until",
                value=format_dt(
                    after.communication_disabled_until
                ),
                inline=False
            )

            await send_log(
                after.guild,
                embed
            )

        else:

            embed = discord.Embed(
                title="✅ Timeout Removed",
                description=(
                    f"{after.mention}'s timeout was removed."
                ),
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )

            await send_log(
                after.guild,
                embed
            )

    # Role changes
    before_roles = set(before.roles)
    after_roles = set(after.roles)

    added_roles = after_roles - before_roles
    removed_roles = before_roles - after_roles

    if added_roles:

        for role in added_roles:

            if role.is_default():
                continue

            embed = discord.Embed(
                title="➕ Role Added",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )

            embed.add_field(
                name="User",
                value=after.mention,
                inline=True
            )

            embed.add_field(
                name="Role",
                value=role.mention,
                inline=True
            )

            await send_log(
                after.guild,
                embed
            )

    if removed_roles:

        for role in removed_roles:

            if role.is_default():
                continue

            embed = discord.Embed(
                title="➖ Role Removed",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )

            embed.add_field(
                name="User",
                value=after.mention,
                inline=True
            )

            embed.add_field(
                name="Role",
                value=role.mention,
                inline=True
            )

            await send_log(
                after.guild,
                embed
            )


# ============================================================
# CHANNEL CREATE
# ============================================================

@bot.event
async def on_guild_channel_create(
    channel: discord.abc.GuildChannel
):

    if channel.guild is None:
        return

    embed = discord.Embed(
        title="📁 Channel Created",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="Channel",
        value=channel.mention
        if hasattr(channel, "mention")
        else channel.name,
        inline=True
    )

    embed.add_field(
        name="Type",
        value=str(channel.type),
        inline=True
    )

    await send_log(
        channel.guild,
        embed
    )


# ============================================================
# CHANNEL DELETE
# ============================================================

@bot.event
async def on_guild_channel_delete(
    channel: discord.abc.GuildChannel
):

    if channel.guild is None:
        return

    embed = discord.Embed(
        title="🗑️ Channel Deleted",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="Channel",
        value=f"#{channel.name}",
        inline=True
    )

    embed.add_field(
        name="Channel ID",
        value=f"`{channel.id}`",
        inline=True
    )

    await send_log(
        channel.guild,
        embed
    )    

# ============================================================
# /CONFIG
# ============================================================

@bot.tree.command(
    name="config",
    description="Configure Pebble for this server."
)
@app_commands.describe(
    setting="The setting you want to change.",
    channel="Channel to use for the setting.",
    role="Role to use for the setting.",
    category="Category to use for the setting."
)
@app_commands.choices(
    setting=[
        app_commands.Choice(
            name="Welcome Channel",
            value="welcome_channel"
        ),
        app_commands.Choice(
            name="Log Channel",
            value="log_channel"
        ),
        app_commands.Choice(
            name="Ticket Log Channel",
            value="ticket_log_channel"
        ),
        app_commands.Choice(
            name="Ticket Category",
            value="ticket_category"
        ),
        app_commands.Choice(
            name="Verified Role",
            value="verified_role"
        )
    ]
)
@app_commands.checks.has_permissions(administrator=True)
async def config(
    interaction: discord.Interaction,
    setting: app_commands.Choice[str],
    channel: discord.TextChannel = None,
    role: discord.Role = None,
    category: discord.CategoryChannel = None
):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    config_data = get_guild_config(guild.id)

    setting_name = setting.value

    # --------------------------------------------------------
    # CHANNEL SETTINGS
    # --------------------------------------------------------

    if setting_name == "welcome_channel":

        if channel is None:
            await interaction.response.send_message(
                "❌ Please select a channel.",
                ephemeral=True
            )
            return

        config_data["welcome_channel"] = channel.id

        save_server_config()

        await interaction.response.send_message(
            f"✅ Welcome channel set to {channel.mention}.",
            ephemeral=True
        )
        return

    if setting_name == "log_channel":

        if channel is None:
            await interaction.response.send_message(
                "❌ Please select a channel.",
                ephemeral=True
            )
            return

        config_data["log_channel"] = channel.id

        save_server_config()

        await interaction.response.send_message(
            f"✅ Log channel set to {channel.mention}.",
            ephemeral=True
        )
        return

    if setting_name == "ticket_log_channel":

        if channel is None:
            await interaction.response.send_message(
                "❌ Please select a channel.",
                ephemeral=True
            )
            return

        config_data["ticket_log_channel"] = channel.id

        save_server_config()

        await interaction.response.send_message(
            f"✅ Ticket log channel set to {channel.mention}.",
            ephemeral=True
        )
        return

    # --------------------------------------------------------
    # TICKET CATEGORY
    # --------------------------------------------------------

    if setting_name == "ticket_category":

        if category is None:
            await interaction.response.send_message(
                "❌ Please select a category.",
                ephemeral=True
            )
            return

        config_data["ticket_category"] = category.id

        save_server_config()

        await interaction.response.send_message(
            f"✅ Ticket category set to **{category.name}**.",
            ephemeral=True
        )
        return

    # --------------------------------------------------------
    # VERIFIED ROLE
    # --------------------------------------------------------

    if setting_name == "verified_role":

        if role is None:
            await interaction.response.send_message(
                "❌ Please select a role.",
                ephemeral=True
            )
            return

        config_data["verified_role"] = role.id

        save_server_config()

        await interaction.response.send_message(
            f"✅ Verified role set to {role.mention}.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        "❌ Unknown configuration setting.",
        ephemeral=True
    )


# ============================================================
# /CONFIG-VIEW
# ============================================================

@bot.tree.command(
    name="config-view",
    description="View the current Pebble configuration."
)
@app_commands.checks.has_permissions(administrator=True)
async def config_view(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    config_data = get_guild_config(guild.id)

    def get_channel(key):
        channel_id = config_data.get(key)

        if not channel_id:
            return "Not configured"

        channel = guild.get_channel(channel_id)

        return channel.mention if channel else "Channel not found"

    def get_role(key):
        role_id = config_data.get(key)

        if not role_id:
            return "Not configured"

        role = guild.get_role(role_id)

        return role.mention if role else "Role not found"

    def get_category(key):
        category_id = config_data.get(key)

        if not category_id:
            return "Not configured"

        category = guild.get_channel(category_id)

        return category.name if category else "Category not found"

    embed = discord.Embed(
        title="⚙️ Pebble Configuration",
        description=f"Configuration for **{guild.name}**",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="👋 Welcome Channel",
        value=get_channel("welcome_channel"),
        inline=False
    )

    embed.add_field(
        name="📋 Log Channel",
        value=get_channel("log_channel"),
        inline=False
    )

    embed.add_field(
        name="🎫 Ticket Log Channel",
        value=get_channel("ticket_log_channel"),
        inline=False
    )

    embed.add_field(
        name="📁 Ticket Category",
        value=get_category("ticket_category"),
        inline=False
    )

    embed.add_field(
        name="✅ Verified Role",
        value=get_role("verified_role"),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )

# ============================================================
# WELCOME / LEAVE SYSTEM
# ============================================================

@bot.event
async def on_member_join(member: discord.Member):

    guild = member.guild
    config_data = get_guild_config(guild.id)

    # --------------------------------------------------------
    # AUTO ROLE
    # --------------------------------------------------------

    auto_role_id = config_data.get("auto_role")

    if auto_role_id:
        role = guild.get_role(auto_role_id)

        if role:
            try:
                await member.add_roles(
                    role,
                    reason="Automatic welcome role"
                )
            except discord.Forbidden:
                print(
                    f"❌ Cannot give {role.name} to {member}."
                )
            except Exception as e:
                print(
                    f"❌ Auto-role error: {e}"
                )

    # --------------------------------------------------------
    # WELCOME CHANNEL
    # --------------------------------------------------------

    welcome_channel_id = config_data.get(
        "welcome_channel"
    )

    if not welcome_channel_id:
        return

    channel = guild.get_channel(
        welcome_channel_id
    )

    if channel is None:
        return

    member_count = guild.member_count or len(guild.members)

    embed = discord.Embed(
        title="👋 Welcome!",
        description=(
            f"Welcome {member.mention} to **{guild.name}**!\n\n"
            f"We hope you enjoy your time here! 🎉"
        ),
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.add_field(
        name="👤 Member",
        value=member.mention,
        inline=True
    )

    embed.add_field(
        name="👥 Member Count",
        value=f"**{member_count:,}**",
        inline=True
    )

    embed.set_footer(
        text=f"Member ID: {member.id}"
    )

    try:
        await channel.send(
            embed=embed
        )
    except discord.Forbidden:
        print(
            f"❌ Cannot send welcome message in #{channel.name}."
        )
    except Exception as e:
        print(
            f"❌ Welcome message error: {e}"
        )


# ============================================================
# LEAVE SYSTEM
# ============================================================

@bot.event
async def on_member_remove(member: discord.Member):

    guild = member.guild
    config_data = get_guild_config(guild.id)

    # Use the welcome channel for leave messages
    # unless you later configure a separate leave channel.

    welcome_channel_id = config_data.get(
        "welcome_channel"
    )

    if not welcome_channel_id:
        return

    channel = guild.get_channel(
        welcome_channel_id
    )

    if channel is None:
        return

    member_count = guild.member_count or len(guild.members)

    embed = discord.Embed(
        title="👋 Member Left",
        description=(
            f"**{member}** has left **{guild.name}**."
        ),
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.add_field(
        name="👤 Member",
        value=str(member),
        inline=True
    )

    embed.add_field(
        name="👥 Members Remaining",
        value=f"**{member_count:,}**",
        inline=True
    )

    try:
        await channel.send(
            embed=embed
        )
    except discord.Forbidden:
        print(
            f"❌ Cannot send leave message in #{channel.name}."
        )
    except Exception as e:
        print(
            f"❌ Leave message error: {e}"
        )

# ============================================================
# /RANK
# ============================================================

@bot.tree.command(
    name="rank",
    description="View your leveling progress."
)
@app_commands.describe(
    user="User whose rank you want to view."
)
async def rank(
    interaction: discord.Interaction,
    user: discord.Member = None
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    user = user or interaction.user

    data = get_user_level_data(
        interaction.guild.id,
        user.id
    )

    level = data["level"]
    xp = data["xp"]

    current_level_xp = 0

    for i in range(level):
        current_level_xp += xp_for_next_level(i)

    next_level_xp = xp_for_next_level(level)

    progress_xp = xp - current_level_xp

    progress_xp = max(
        0,
        min(progress_xp, next_level_xp)
    )

    progress_percent = (
        progress_xp / next_level_xp
        if next_level_xp > 0
        else 0
    )

    bar_length = 15

    filled = int(
        progress_percent * bar_length
    )

    bar = (
        "█" * filled
        + "░" * (bar_length - filled)
    )

    embed = discord.Embed(
        title="🏆 Level",
        description=f"**{user.display_name}**",
        color=discord.Color.blurple()
    )

    embed.set_thumbnail(
        url=user.display_avatar.url
    )

    embed.add_field(
        name="Level",
        value=f"**{level}**",
        inline=True
    )

    embed.add_field(
        name="Total XP",
        value=f"**{xp:,}**",
        inline=True
    )

    embed.add_field(
        name="Progress",
        value=(
            f"`{bar}`\n"
            f"**{progress_xp:,} / {next_level_xp:,} XP**"
        ),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed
    )

# ============================================================
# /LEADERBOARD
# ============================================================

@bot.tree.command(
    name="leaderboard",
    description="View the server leveling leaderboard."
)
async def leaderboard(
    interaction: discord.Interaction
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    guild_data = levels_data.get(
        str(interaction.guild.id),
        {}
    )

    if not guild_data:
        await interaction.response.send_message(
            "📊 Nobody has earned XP yet!"
        )
        return

    sorted_users = sorted(
        guild_data.items(),
        key=lambda item: item[1].get("xp", 0),
        reverse=True
    )

    lines = []

    for position, (user_id, data) in enumerate(
        sorted_users[:10],
        start=1
    ):

        member = interaction.guild.get_member(
            int(user_id)
        )

        if member is None:
            continue

        level = data.get("level", 0)
        xp = data.get("xp", 0)

        if position == 1:
            medal = "🥇"
        elif position == 2:
            medal = "🥈"
        elif position == 3:
            medal = "🥉"
        else:
            medal = f"`#{position}`"

        lines.append(
            f"{medal} **{member.display_name}** "
            f"— Level **{level}** • **{xp:,} XP**"
        )

    if not lines:
        await interaction.response.send_message(
            "📊 Nobody on the leaderboard is currently in this server."
        )
        return

    embed = discord.Embed(
        title="🏆 Level Leaderboard",
        description="\n".join(lines),
        color=discord.Color.gold()
    )

    embed.set_footer(
        text=interaction.guild.name
    )

    await interaction.response.send_message(
        embed=embed
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
