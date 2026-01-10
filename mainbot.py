import json
import os
from datetime import datetime
import schedule
import discord
from discord import app_commands
import asyncio
import tracemalloc
import resource
import aiohttp
import logging
import logging.handlers
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import Column, Integer, select
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# Start tracing Python memory allocations
tracemalloc.start()

discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.DEBUG)
logging.getLogger('discord.http').setLevel(logging.INFO)

discord_handler = logging.handlers.RotatingFileHandler(
    filename='discord.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,
    backupCount=5
)

dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
discord_handler.setFormatter(formatter)
discord_logger.addHandler(discord_handler)

# Bot operations logger setup
bot_logger = logging.getLogger('bot_operations')
bot_logger.setLevel(logging.DEBUG)

bot_handler = logging.handlers.RotatingFileHandler(
    filename='bot_operations.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32MB
    backupCount=5
)
bot_handler.setLevel(logging.DEBUG)

bot_formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
bot_handler.setFormatter(bot_formatter)
bot_logger.addHandler(bot_handler)

# Also add console handler for bot info and errors
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(bot_formatter)
bot_logger.addHandler(console_handler)

# Constants
JSON_URL_1 = 'https://raw.githubusercontent.com/vanshb03/Summer2026-Internships/refs/heads/dev/.github/scripts/listings.json'
JSON_URL_2 = 'https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/refs/heads/dev/.github/scripts/listings.json'
PREVIOUS_DATA_FILE = 'previous_data.json'
PREVIOUS_DATA_FILE_2 = 'previous_data_simplify.json'

DISCORD_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_FILE = 'bot_config.db'
DATABASE_URL = f'sqlite+aiosqlite:///{DATABASE_FILE}'
MAX_RETRIES = 3

BIG_TECH_COMPANIES = [
    "openai", "anthropic", "google", "nvidia", "bloomberg", "snap",
    "meta", "apple", "amazon", "microsoft", "netflix", "tesla", "databricks", "figma", "roblox",
    "square", "block", "stripe", "airbnb", "uber", "lyft", "doordash", "instacart", "palantir",
    "snowflake", "salesforce", "oracle", "sap", "adobe", "intel", "amd",
    "qualcomm", "texas instruments", "cisco", "dell", "hp", "atlassian", "zoom",
    "workday", "servicenow", "twilio", "shopify", "spotify", "pinterest", "twitter",
    "linkedin", "github", "robinhood", "coinbase", "jane street", "hudson river trading",
    "citadel", "two sigma", "jump trading", "drw", "akamai", "cloudflare", "mongodb",
    "splunk", "reddit", "discord", "tiktok", "bytedance", "waymo", "heygen", "waabi", "mistral", "perplexity",
    "point72", "optiver", "imc trading", "tencent", "samsung", "tower research", "rippling", "cohere", "talos trading",
    "kalshi", "neo", "capital one", "datadog", "ramp", "notion", "five rings", "epic games", "riot games",
    "sony",
    "x", # will be .startswith()
]

# Emojis
EMOJI_NEW = "✨"
EMOJI_DEACTIVATED = "📉"
EMOJI_REACTIVATED = "📈"
EMOJI_SUMMER = "☀️"
EMOJI_WINTER = "❄️"
EMOJI_FALL = "🍂"
EMOJI_UNKNOWN_TERM = "❓"

# Initialize Discord client and command tree
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Global tracking for failed channels (in-memory for current session)
failed_channels = set()
channel_failure_counts = {}

# Global flag to track if scheduled task is running
is_task_running = False

seen_urls = set()

# Global storage for Discord message IDs to enable message editing
# Structure: role_message_map[guild_id][channel_id][role_id] = message_id
role_message_map = {}

def initialize_url_cache():
    """Initialize the URL cache with existing URLs from previous data files"""
    global seen_urls

    data_files = [PREVIOUS_DATA_FILE, PREVIOUS_DATA_FILE_2]
    urls_added = 0

    for data_file in data_files:
        if os.path.exists(data_file):
            try:
                with open(data_file, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    for role in data:
                        url = role.get('url', '')
                        if url:
                            seen_urls.add(url)
                            urls_added += 1
                bot_logger.debug(f"Loaded {urls_added} URLs from {data_file} into cache")
            except (FileNotFoundError, json.JSONDecodeError) as e:
                bot_logger.warning(f"Error loading URLs from {data_file}: {e}")
        else:
            bot_logger.debug(f"Data file {data_file} not found, skipping cache initialization")

    bot_logger.info(f"URL cache initialized with {len(seen_urls)} total URLs")

def store_message_id(guild_id: int, channel_id: int, role_id: str, message_id: int):
    """Store Discord message ID for a specific role in a guild/channel"""
    if guild_id not in role_message_map:
        role_message_map[guild_id] = {}
    if channel_id not in role_message_map[guild_id]:
        role_message_map[guild_id][channel_id] = {}
    role_message_map[guild_id][channel_id][role_id] = message_id
    bot_logger.debug(f"Stored message ID {message_id} for role {role_id} in guild {guild_id}, channel {channel_id}")

def get_message_id(guild_id: int, channel_id: int, role_id: str) -> int | None:
    """Retrieve Discord message ID for a specific role in a guild/channel"""
    try:
        return role_message_map[guild_id][channel_id][role_id]
    except KeyError:
        return None

def remove_message_id(guild_id: int, channel_id: int, role_id: str):
    """Remove Discord message ID for a specific role in a guild/channel"""
    try:
        del role_message_map[guild_id][channel_id][role_id]
        bot_logger.debug(f"Removed message ID for role {role_id} in guild {guild_id}, channel {channel_id}")
    except KeyError:
        pass

async def edit_discord_message(message_id: int, new_content: str, guild_id: int, channel_id: int):
    """Edit an existing Discord message with new content"""
    global failed_channels, channel_failure_counts

    channel_key = f"{guild_id}:{channel_id}"

    if channel_key in failed_channels:
        bot_logger.debug(f"Skipping previously failed channel {channel_id} in guild {guild_id}")
        return False

    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            bot_logger.debug(f"Channel {channel_id} not in cache, attempting to fetch...")
            channel = await client.fetch_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            bot_logger.error(f"Error: Channel ID {channel_id} is not a text channel. Skipping.")
            failed_channels.add(channel_key)
            return False

        message = await channel.fetch_message(message_id)
        await message.edit(content=new_content)
        bot_logger.debug(f"Successfully edited message {message_id} in channel {channel_id}, guild {guild_id}")

        if channel_key in channel_failure_counts:
            del channel_failure_counts[channel_key]
        if channel_key in failed_channels:
            failed_channels.remove(channel_key)

        return True

    except discord.NotFound:
        bot_logger.warning(f"Message {message_id} not found in channel {channel_id}, guild {guild_id}. Will send new message.")
        remove_message_id(guild_id, channel_id, "")
        return False
    except discord.Forbidden:
        bot_logger.warning(f"No permission to edit message in channel {channel_id}, guild {guild_id}.")
        failed_channels.add(channel_key)
        return False
    except Exception as e:
        bot_logger.error(f"Error editing message {message_id} in channel {channel_id}, guild {guild_id}: {e}")
        channel_failure_counts[channel_key] = channel_failure_counts.get(channel_key, 0) + 1

        if channel_failure_counts.get(channel_key, 0) >= MAX_RETRIES:
            bot_logger.warning(f"Channel {channel_id} in guild {guild_id} has failed {MAX_RETRIES} times, adding to failed channels.")
            failed_channels.add(channel_key)

        return False

# --- SQLAlchemy Setup ---
Base = declarative_base()

class GuildConfig(Base):
    __tablename__ = 'guild_configs'
    
    guild_id = Column(Integer, primary_key=True)
    channel_id = Column(Integer, nullable=True)
    ping_role_id = Column(Integer, nullable=True)

# Create async engine and session factory
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

# --- Database Setup and Helper Functions ---
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def set_guild_channel(guild_id: int, channel_id: int | None):
    async with async_session() as session:
        # Check if record exists
        result = await session.execute(
            select(GuildConfig).where(GuildConfig.guild_id == guild_id)
        )
        guild_config = result.scalar_one_or_none()
        
        if guild_config:
            # Update existing record
            guild_config.channel_id = channel_id
        else:
            # Create new record
            guild_config = GuildConfig(
                guild_id=guild_id,
                channel_id=channel_id,
                ping_role_id=None
            )
            session.add(guild_config)
        
        await session.commit()

async def get_all_channels_from_db() -> list[tuple[int, int]]:
    """Returns list of (guild_id, channel_id) tuples for all configured channels"""
    async with async_session() as session:
        result = await session.execute(
            select(GuildConfig.guild_id, GuildConfig.channel_id)
            .where(GuildConfig.channel_id.is_not(None))
        )
        return [(row.guild_id, row.channel_id) for row in result]

async def get_guild_channel(guild_id: int) -> int | None:
    """Get the configured channel for a specific guild"""
    async with async_session() as session:
        result = await session.execute(
            select(GuildConfig.channel_id)
            .where(GuildConfig.guild_id == guild_id)
        )
        row = result.first()
        return row.channel_id if row and row.channel_id else None

async def set_guild_ping_role(guild_id: int, role_id: int | None):
    async with async_session() as session:
        # Check if record exists
        result = await session.execute(
            select(GuildConfig).where(GuildConfig.guild_id == guild_id)
        )
        guild_config = result.scalar_one_or_none()
        
        if guild_config:
            # Update existing record
            guild_config.ping_role_id = role_id
        else:
            # Create new record
            guild_config = GuildConfig(
                guild_id=guild_id,
                channel_id=None,
                ping_role_id=role_id
            )
            session.add(guild_config)
        
        await session.commit()

async def get_guild_ping_role(guild_id: int) -> int | None:
    async with async_session() as session:
        result = await session.execute(
            select(GuildConfig.ping_role_id)
            .where(GuildConfig.guild_id == guild_id)
        )
        row = result.first()
        if row and row.ping_role_id:
            try:
                return int(row.ping_role_id)
            except (ValueError, TypeError):
                bot_logger.warning(f"Warning: Invalid ping_role_id '{row.ping_role_id}' for guild {guild_id}. Treating as None.")
                return None
        return None

async def get_all_guild_ping_roles() -> dict[int, int]:
    """Returns dict of {guild_id: ping_role_id} for all guilds with ping roles configured"""
    async with async_session() as session:
        result = await session.execute(
            select(GuildConfig.guild_id, GuildConfig.ping_role_id)
            .where(GuildConfig.ping_role_id.is_not(None))
        )
        guild_roles = {}
        for row in result:
            try:
                guild_roles[row.guild_id] = int(row.ping_role_id)
            except (ValueError, TypeError):
                bot_logger.warning(f"Warning: Invalid ping_role_id '{row.ping_role_id}' for guild {row.guild_id}. Skipping.")
        return guild_roles

# --- Repository and JSON Handling ---
async def fetch_json_from_url(url: str) -> list:
    """Fetch JSON data directly from URL"""
    bot_logger.debug(f"Fetching JSON data from {url}...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    text_data = await response.text()
                    data = json.loads(text_data)
                    bot_logger.debug(f"Successfully fetched {len(data)} items from {url}")
                    return data
                else:
                    bot_logger.error(f"Error fetching {url}: HTTP {response.status}")
                    return []
    except json.JSONDecodeError as e:
        bot_logger.error(f"Error parsing JSON from {url}: {e}")
        return []
    except Exception as e:
        bot_logger.error(f"Error fetching JSON from {url}: {e}")
        return []

def read_json(json_file_path):
    bot_logger.info(f"Reading JSON file from {json_file_path}...")
    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
        bot_logger.info(f"JSON file read successfully from {json_file_path}, {len(data)} items loaded.")
        return data
    except FileNotFoundError:
        bot_logger.error(f"Error: JSON file not found at {json_file_path}.")
        return []
    except json.JSONDecodeError:
        bot_logger.error(f"Error: Could not decode JSON from {json_file_path}.")
        return []

def _is_value_truthy(value):
    if isinstance(value, str):
        return value.lower() == 'true'
    return bool(value)

# --- Message Formatting ---
def get_term_emoji_and_string(role_data):
    raw_terms = role_data.get('terms')
    raw_season = role_data.get('season')
    season_str = "Unknown" # Default
    collected_emojis = []

    # Prioritize 'season' if available, then 'terms'
    if raw_season:
        if isinstance(raw_season, list) and raw_season:
            season_str = ', '.join(raw_season)
        elif isinstance(raw_season, str) and raw_season.strip():
            season_str = raw_season
    elif raw_terms:
        if isinstance(raw_terms, list) and raw_terms:
            season_str = ', '.join(raw_terms)
        elif isinstance(raw_terms, str) and raw_terms.strip():
            season_str = raw_terms
    
    if not season_str or season_str == "Unknown": # If no valid season/term found
         return EMOJI_UNKNOWN_TERM, "Unknown"

    season_lower = season_str.lower()

    if "summer" in season_lower or "spring" in season_lower:
        collected_emojis.append(EMOJI_SUMMER)
    if "winter" in season_lower:
        collected_emojis.append(EMOJI_WINTER)
    if "fall" in season_lower or "autumn" in season_lower:
        collected_emojis.append(EMOJI_FALL)
    
    if not collected_emojis: # If no specific terms found, but season_str has a value
        final_emoji_str = EMOJI_UNKNOWN_TERM
    else:
        final_emoji_str = "".join(collected_emojis) # Join emojis like "❄️🍂"

    # Return the processed season string (could be "Unknown", "Summer 2025", "Fall 2025, Winter 2025", etc.)
    # and the collected/defaulted emoji string.
    return final_emoji_str, season_str


def format_message(role, guild_id: int, guild_ping_roles: dict[int, int]):
    company_name_str = role.get('company_name', 'N/A Company')
    title_str = role.get('title', 'N/A Title')
    url_str = role.get('url', '#')
    location_str = ', '.join(role.get('locations', [])) if role.get('locations') else 'Not specified'
    sponsorship_str = role.get('sponsorship', 'Not specified')
    term_emoji, term_str = get_term_emoji_and_string(role)

    if term_str == "Unknown" and url_str != '#': # Special formatting for unknown term but existing URL
        return (f"{EMOJI_NEW} **{company_name_str}** - {title_str}\n"
                f"Term: {term_emoji} {term_str}. Review details: <{url_str}>")
    elif term_str == "Unknown": # Fallback if URL is also not present for an unknown term
        return (f"{EMOJI_NEW} **{company_name_str}** - {title_str}\n"
                f"Term: {term_emoji} {term_str}. More details unavailable.")

    ping_str = ""
    ping_role_id = guild_ping_roles.get(guild_id)
    if ping_role_id:
        should_ping = False

        for company in BIG_TECH_COMPANIES:
            if company.lower() == "x":
                if company_name_str.lower().startswith("x"):
                    should_ping = True
                    break
            elif company.lower() in company_name_str.lower():
                should_ping = True
                break
        
        if should_ping:
            ping_str = f"<@&{ping_role_id}> "

    return (f"{EMOJI_NEW} **{company_name_str}** just posted a new internship! {ping_str}\n"
            f"[{title_str}]({url_str})\n"
            f"**Location(s):** {location_str}\n"
            f"**Term:** {term_emoji} {term_str}\n"
            f"**Sponsorship:** `{sponsorship_str}`\n"
            f"**Posted:** {datetime.now().strftime('%b %d')}")

def format_deactivation_message(role):
    company_name_str = role.get('company_name', 'N/A Company')
    title_str = role.get('title', 'N/A Title')
    url_str = role.get('url', '#') # Keep URL for reference
    term_emoji, term_str = get_term_emoji_and_string(role)

    return (f"{EMOJI_DEACTIVATED} **{company_name_str}** internship is no longer active.\n"
            f"[{title_str}]({url_str}) - Term: {term_emoji} {term_str}\n"
            f"Deactivated: {datetime.now().strftime('%b %d')}")

def format_reactivation_message(role, guild_id: int, guild_ping_roles: dict[int, int]):
    company_name_str = role.get('company_name', 'N/A Company')
    title_str = role.get('title', 'N/A Title')
    url_str = role.get('url', '#')
    term_emoji, term_str = get_term_emoji_and_string(role)

    ping_str = ""
    ping_role_id = guild_ping_roles.get(guild_id)
    if ping_role_id:
        should_ping = False
        if "winter 2026" in term_str.lower(): # Consistent ping logic
             should_ping = True
        for company in BIG_TECH_COMPANIES:
            if company.lower() == "x":
                if company_name_str.lower().startswith("x"):
                    should_ping = True
                    break
            elif company.lower() in company_name_str.lower():
                should_ping = True
                break
        if should_ping:
            ping_str = f"<@&{ping_role_id}> "

    return (f"{EMOJI_REACTIVATED} {ping_str}**{company_name_str}** internship is active again!\n"
            f"[{title_str}]({url_str}) - Term: {term_emoji} {term_str}\n"
            f"Reactivated: {datetime.now().strftime('%b %d')}")

def format_deactivated_embed_message(role):
    """Format a message for editing an existing active message to show it's deactivated"""
    company_name_str = role.get('company_name', 'N/A Company')
    title_str = role.get('title', 'N/A Title')
    url_str = role.get('url', '#')
    location_str = ', '.join(role.get('locations', [])) if role.get('locations') else 'Not specified'
    sponsorship_str = role.get('sponsorship', 'Not specified')
    term_emoji, term_str = get_term_emoji_and_string(role)

    return (f"{EMOJI_DEACTIVATED} **~~{company_name_str}~~** - ~~{title_str}~~\n"
            f"~~[{title_str}]({url_str})~~\n"
            f"**Location(s):** ~~{location_str}~~\n"
            f"**Term:** {term_emoji} ~~{term_str}~~\n"
            f"**Sponsorship:** ~~`{sponsorship_str}`~~\n"
            f"**Posted:** ~~{datetime.now().strftime('%b %d')}~~\n"
            f"❌ **This internship is no longer active**")

def format_reactivated_embed_message(role, guild_id: int, guild_ping_roles: dict[int, int]):
    """Format a message for editing an existing deactivated message to show it's reactivated"""
    company_name_str = role.get('company_name', 'N/A Company')
    title_str = role.get('title', 'N/A Title')
    url_str = role.get('url', '#')
    location_str = ', '.join(role.get('locations', [])) if role.get('locations') else 'Not specified'
    sponsorship_str = role.get('sponsorship', 'Not specified')
    term_emoji, term_str = get_term_emoji_and_string(role)

    ping_str = ""
    ping_role_id = guild_ping_roles.get(guild_id)
    if ping_role_id:
        should_ping = False

        for company in BIG_TECH_COMPANIES:
            if company.lower() == "x":
                if company_name_str.lower().startswith("x"):
                    should_ping = True
                    break
            elif company.lower() in company_name_str.lower():
                should_ping = True
                break

        if should_ping:
            ping_str = f"<@&{ping_role_id}> "

    return (f"{EMOJI_REACTIVATED} **{company_name_str}** just posted a new internship! {ping_str}\n"
            f"[{title_str}]({url_str})\n"
            f"**Location(s):** {location_str}\n"
            f"**Term:** {term_emoji} {term_str}\n"
            f"**Sponsorship:** `{sponsorship_str}`\n"
            f"**Posted:** {datetime.now().strftime('%b %d')}\n"
            f"🔄 **This internship is active again!**")


# --- Discord Interaction ---
async def send_discord_message(message_content: str, guild_id: int, channel_id: int, role_id: str = None):
    global failed_channels, channel_failure_counts

    channel_key = f"{guild_id}:{channel_id}"

    if channel_key in failed_channels:
        bot_logger.debug(f"Skipping previously failed channel ID {channel_id} in guild {guild_id}")
        return None

    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            bot_logger.debug(f"Channel {channel_id} not in cache, attempting to fetch...")
            channel = await client.fetch_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            bot_logger.error(f"Error: Channel ID {channel_id} is not a text channel. Skipping.")
            failed_channels.add(channel_key)
            return None

        message = await channel.send(message_content)

        if role_id:
            store_message_id(guild_id, channel_id, role_id, message.id)

        bot_logger.debug(f"Successfully sent message to channel {channel_id} in guild {guild_id}")

        if channel_key in channel_failure_counts:
            del channel_failure_counts[channel_key]
        if channel_key in failed_channels:
            failed_channels.remove(channel_key)

        await asyncio.sleep(1)
        return message.id

    except discord.NotFound:
        bot_logger.warning(f"Channel {channel_id} not found in guild {guild_id}.")
        channel_failure_counts[channel_key] = channel_failure_counts.get(channel_key, 0) + 1
    except discord.Forbidden:
        bot_logger.warning(f"No permission for channel {channel_id} in guild {guild_id}.")
        failed_channels.add(channel_key)
    except Exception as e:
        bot_logger.error(f"Error sending message to channel {channel_id} in guild {guild_id}: {e}")
        channel_failure_counts[channel_key] = channel_failure_counts.get(channel_key, 0) + 1
    finally:
        if channel_failure_counts.get(channel_key, 0) >= MAX_RETRIES:
            bot_logger.warning(f"Channel {channel_id} in guild {guild_id} has failed {MAX_RETRIES} times, adding to failed channels.")
            failed_channels.add(channel_key)

    return None


async def send_messages_to_all_configured_channels(message_content: str, guild_ping_roles: dict[int, int] = None):
    channel_configs = await get_all_channels_from_db()
    if not channel_configs:
        bot_logger.info("No channels configured in the database to send messages to.")
        return

    if guild_ping_roles is None:
        guild_ping_roles = await get_all_guild_ping_roles()

    # Filter out failed channels
    active_channel_configs = [
        (guild_id, channel_id) for guild_id, channel_id in channel_configs
        if f"{guild_id}:{channel_id}" not in failed_channels
    ]

    tasks = [send_discord_message(message_content, guild_id, channel_id) for guild_id, channel_id in active_channel_configs]
    if tasks:
        await asyncio.gather(*tasks)

async def edit_or_send_message(role, guild_id: int, channel_id: int, guild_ping_roles: dict[int, int], is_deactivation: bool):
    """Edit existing message if possible, otherwise send new message"""
    role_id = role.get('id', '')

    if not role_id:
        if is_deactivation:
            message = format_deactivation_message(role)
        else:
            message = format_reactivation_message(role, guild_id, guild_ping_roles)
        await send_discord_message(message, guild_id, channel_id)
        return

    existing_message_id = get_message_id(guild_id, channel_id, role_id)

    if existing_message_id:
        if is_deactivation:
            new_content = format_deactivated_embed_message(role)
        else:
            new_content = format_reactivated_embed_message(role, guild_id, guild_ping_roles)

        success = await edit_discord_message(existing_message_id, new_content, guild_id, channel_id)

        if success:
            bot_logger.debug(f"Successfully edited message for role {role_id} in guild {guild_id}")
            return

    bot_logger.debug(f"Could not edit message for role {role_id}, sending new message")
    if is_deactivation:
        message = format_deactivation_message(role)
    else:
        message = format_reactivation_message(role, guild_id, guild_ping_roles)
    await send_discord_message(message, guild_id, channel_id)

# --- Scheduled Tasks ---
async def combined_scheduled_task():
    """Combined scheduled task that processes both repos sequentially"""
    global is_task_running
    
    if is_task_running:
        bot_logger.debug("Previous task still running, skipping this execution")
        return
    
    is_task_running = True
    bot_logger.debug(f"Running scheduled check for both repos at {datetime.now()}")
    try:
        new_data = await fetch_json_from_url(JSON_URL_1)
        if new_data != []:
            if os.path.exists(PREVIOUS_DATA_FILE):
                try:
                    with open(PREVIOUS_DATA_FILE, 'r', encoding='utf-8') as file:
                        old_data = json.load(file)
                    bot_logger.debug(f"Previous data loaded from {PREVIOUS_DATA_FILE}.")
                except (FileNotFoundError, json.JSONDecodeError) as e:
                    bot_logger.error(f"Error reading or decoding previous data file {PREVIOUS_DATA_FILE}: {e}. Starting fresh.")
                    old_data = []
            else:
                old_data = []
                bot_logger.info(f"No previous data found at {PREVIOUS_DATA_FILE}. Initializing.")

            await process_repo_updates(new_data, old_data, PREVIOUS_DATA_FILE, JSON_URL_1, is_second_repo=False)
        
        new_data_2 = await fetch_json_from_url(JSON_URL_2)
        if new_data_2 != []:
            if os.path.exists(PREVIOUS_DATA_FILE_2):
                try:
                    with open(PREVIOUS_DATA_FILE_2, 'r', encoding='utf-8') as file:
                        old_data_2 = json.load(file)
                    bot_logger.debug(f"Previous data loaded from {PREVIOUS_DATA_FILE_2}.")
                except (FileNotFoundError, json.JSONDecodeError) as e:
                    bot_logger.error(f"Error reading or decoding previous data file {PREVIOUS_DATA_FILE_2}: {e}. Starting fresh.")
                    old_data_2 = []
            else:
                old_data_2 = []
                bot_logger.info(f"No previous data found at {PREVIOUS_DATA_FILE_2}. Initializing.")

            await process_repo_updates(new_data_2, old_data_2, PREVIOUS_DATA_FILE_2, JSON_URL_2, is_second_repo=True)
        
    except Exception as e:
        is_task_running = False
        bot_logger.error(f"Error during combined scheduled task: {e}")
    finally:
        is_task_running = False
        bot_logger.debug(f"Scheduled task completed at {datetime.now()}")

def try_start_scheduled_task():
    """Function to start scheduled task only if not already running"""
    if not is_task_running:
        asyncio.create_task(combined_scheduled_task())
    else:
        bot_logger.debug("Scheduled task already running, skipping")

async def process_repo_updates(new_data, old_data, previous_data_file, repo_url, is_second_repo=False):
    """Process updates for a single repo"""
    new_roles = []
    deactivated_roles = []
    reactivated_roles = [] 

    old_roles_dict = {role['id']: role for role in old_data if 'id' in role and role['id'] is not None}
    guild_ping_roles = await get_all_guild_ping_roles()

    for new_role in new_data:
        if 'id' not in new_role or new_role['id'] is None:
            continue

        old_role = old_roles_dict.get(new_role['id'])
        new_role_is_active = _is_value_truthy(new_role.get('active', True))
        new_role_is_visible = _is_value_truthy(new_role.get('is_visible', True))

        if old_role:
            old_role_is_active = _is_value_truthy(old_role.get('active', True))

            if old_role_is_active and not new_role_is_active:
                deactivated_roles.append(new_role)
            elif is_second_repo and not old_role_is_active and new_role_is_active and new_role_is_visible:
                reactivated_roles.append(new_role)
        elif new_role_is_visible and new_role_is_active: 
            new_roles.append(new_role)

    loop = client.loop if client and client.loop.is_running() else asyncio.get_event_loop()

    for role in new_roles:
        role_url = role.get('url', '')
        if role_url and role_url not in seen_urls:
            channel_configs = await get_all_channels_from_db()
            for guild_id, channel_id in channel_configs:
                if f"{guild_id}:{channel_id}" not in failed_channels:
                    message = format_message(role, guild_id, guild_ping_roles)
                    role_id = role.get('id', '')
                    loop.create_task(send_discord_message(message, guild_id, channel_id, role_id))
            seen_urls.add(role_url)
            bot_logger.debug(f"Added URL to cache: {role_url}")
        elif role_url in seen_urls:
            bot_logger.debug(f"Skipping duplicate URL: {role_url}")
        else:
            bot_logger.debug(f"Role missing URL, skipping cache check: {role.get('company_name', 'Unknown')} - {role.get('title', 'Unknown')}")

    # Deactivation messages disabled - too noisy
    # for role in deactivated_roles:
    #     channel_configs = await get_all_channels_from_db()
    #     for guild_id, channel_id in channel_configs:
    #         if f"{guild_id}:{channel_id}" not in failed_channels:
    #             loop.create_task(edit_or_send_message(role, guild_id, channel_id, guild_ping_roles, is_deactivation=True))

    if is_second_repo:
        for role in reactivated_roles:
            role_url = role.get('url', '')
            if role_url and role_url not in seen_urls:
                channel_configs = await get_all_channels_from_db()
                for guild_id, channel_id in channel_configs:
                    if f"{guild_id}:{channel_id}" not in failed_channels:
                        loop.create_task(edit_or_send_message(role, guild_id, channel_id, guild_ping_roles, is_deactivation=False))
                seen_urls.add(role_url)
                bot_logger.debug(f"Added reactivated URL to cache: {role_url}")
            elif role_url in seen_urls:
                bot_logger.debug(f"Skipping duplicate reactivated URL: {role_url}")
            else:
                bot_logger.debug(f"Reactivated role missing URL, skipping cache check: {role.get('company_name', 'Unknown')} - {role.get('title', 'Unknown')}")

    try:
        with open(previous_data_file, 'w', encoding='utf-8') as file:
            json.dump(new_data, file, indent=2) 
        bot_logger.debug(f"Updated previous data with new data for {previous_data_file}.")
    except IOError as e:
        bot_logger.error(f"Error writing previous data file {previous_data_file}: {e}")

    if not new_roles and not deactivated_roles and (not is_second_repo or not reactivated_roles):
        bot_logger.debug(f"No updates found for {repo_url}.")


async def background_scheduler():
    # Single scheduled job that handles both repos
    schedule.every(1).minutes.do(try_start_scheduled_task)
    
    memory_check_counter = 0
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

        memory_check_counter += 1
        if memory_check_counter % 300 == 0: # Approx every 5 minutes (300 seconds)
            current_mem, peak_mem = tracemalloc.get_traced_memory()
            peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak_rss_display = peak_rss_kb / 1024 if os.uname().sysname == 'Darwin' else peak_rss_kb
            
            bot_logger.debug(f"--- Memory Usage ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
            bot_logger.debug(f"Current Python memory (tracemalloc): {current_mem / 1024:.2f} KB")
            bot_logger.debug(f"Peak Python memory (tracemalloc):    {peak_mem / 1024:.2f} KB")
            bot_logger.debug(f"Peak RSS (OS):                       {peak_rss_display:.2f} KB")
            bot_logger.debug("---------------------------------------------------")

# --- Slash Commands ---
@tree.command(name="set_channel", description="Sets the notification channel for this guild (Admin only).")
@app_commands.describe(channel="The text channel to receive notifications. Leave empty to remove the current channel.")
async def set_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel | None = None):
    try:
        if channel:
            await set_guild_channel(interaction.guild.id, channel.id)
            bot_logger.debug(f"Guild {interaction.guild.id} set notification channel to {channel.id}")
            await interaction.response.send_message(f"Notification channel set to {channel.mention}.", ephemeral=True)
        else:
            current_channel_id = await get_guild_channel(interaction.guild.id)
            if current_channel_id is None:
                await interaction.response.send_message("No notification channel is currently configured for this guild.", ephemeral=True)
                return
                
            await set_guild_channel(interaction.guild.id, None)
            bot_logger.debug(f"Guild {interaction.guild.id} removed notification channel.")
            await interaction.response.send_message("Notification channel removed for this guild.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error setting channel: {e}", ephemeral=True)

@tree.command(name="get_channel", description="Shows the notification channel for this guild (Admin only).")
async def list_channels_cmd(interaction: discord.Interaction):
    try:
        channel_id = await get_guild_channel(interaction.guild.id)
        if not channel_id:
            await interaction.response.send_message("No notification channel is currently configured for this guild.", ephemeral=True)
            return
        
        channel = client.get_channel(channel_id)
        if channel:
            await interaction.response.send_message(f"Notification channel for this guild: {channel.mention} (`{channel_id}`)", ephemeral=True)
        else:
            await interaction.response.send_message(f"Notification channel ID: `{channel_id}` (Channel not currently accessible or may be invalid)", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error getting notification channel: {e}", ephemeral=True)

@tree.command(name="set_ping_role", description="Sets the role to ping for important updates (Admin only).")
@app_commands.describe(role="The role to ping. Leave empty to clear.")
async def set_ping_role_cmd(interaction: discord.Interaction, role: discord.Role | None = None):
    try:
        if role:
            await set_guild_ping_role(interaction.guild.id, role.id)
            bot_logger.debug(f"Guild {interaction.guild.id} set ping role to {role.id}")
            await interaction.response.send_message(f"Ping role set to {role.mention}.", ephemeral=True)
        else:
            await set_guild_ping_role(interaction.guild.id, None) # Explicitly pass None
            bot_logger.debug(f"Guild {interaction.guild.id} cleared ping role.")
            await interaction.response.send_message("Ping role cleared.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error setting ping role: {e}", ephemeral=True)

@tree.command(name="get_ping_role", description="Shows the currently configured ping role (Admin only).")
async def get_ping_role_cmd(interaction: discord.Interaction):
    try:
        role_id = await get_guild_ping_role(interaction.guild.id)
        if role_id:
            # Ensure guild is available from interaction before trying to get role
            guild = interaction.guild
            if guild:
                role_obj = guild.get_role(role_id)
                if role_obj:
                    await interaction.response.send_message(f"Current ping role: {role_obj.mention} (`{role_id}`)", ephemeral=True)
                else:
                    await interaction.response.send_message(f"Current ping role ID: `{role_id}` (Role not found in this server).", ephemeral=True)
            else: # Should not happen for guild commands, but good practice
                 await interaction.response.send_message(f"Current ping role ID: `{role_id}` (Could not verify role in this context).", ephemeral=True)
        else:
            await interaction.response.send_message("No ping role is currently configured.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error getting ping role: {e}", ephemeral=True)

@tree.command(name="url_cache_stats", description="Shows URL cache statistics (Admin only).")
async def url_cache_stats_cmd(interaction: discord.Interaction):
    try:
        cache_size = len(seen_urls)
        await interaction.response.send_message(f"URL Cache Statistics:\n• Total cached URLs: {cache_size}\n• Cache is active and preventing duplicate messages.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error getting cache stats: {e}", ephemeral=True)

@tree.command(name="clear_url_cache", description="Clears the URL cache - use with caution as it may allow duplicate messages (Admin only).")
async def clear_url_cache_cmd(interaction: discord.Interaction):
    try:
        global seen_urls
        cache_size_before = len(seen_urls)
        seen_urls.clear()
        initialize_url_cache()
        cache_size_after = len(seen_urls)
        await interaction.response.send_message(f"URL cache cleared and reinitialized.\n• Cleared {cache_size_before} URLs\n• Cache now contains {cache_size_after} URLs from existing data files.", ephemeral=True)
        bot_logger.info(f"URL cache cleared by {interaction.user} (ID: {interaction.user.id}) in guild {interaction.guild.id}")
    except Exception as e:
        await interaction.response.send_message(f"Error clearing cache: {e}", ephemeral=True)

@tree.command(name="message_cache_stats", description="Shows message ID cache statistics (Admin only).")
async def message_cache_stats_cmd(interaction: discord.Interaction):
    try:
        total_guilds = len(role_message_map)
        total_messages = sum(
            len(channels) for guild in role_message_map.values()
            for channels in guild.values()
        )
        await interaction.response.send_message(f"Message ID Cache Statistics:\n• Total guilds with cached messages: {total_guilds}\n• Total cached message IDs: {total_messages}\n• Cache enables message editing for status changes.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error getting cache stats: {e}", ephemeral=True)

@tree.command(name="clear_message_cache", description="Clears the message ID cache - will prevent message editing until new messages are sent (Admin only).")
async def clear_message_cache_cmd(interaction: discord.Interaction):
    try:
        global role_message_map
        cache_size_before = sum(
            len(channels) for guild in role_message_map.values()
            for channels in guild.values()
        )
        role_message_map.clear()
        await interaction.response.send_message(f"Message ID cache cleared.\n• Cleared {cache_size_before} message IDs\n• Future status changes will send new messages instead of editing.", ephemeral=True)
        bot_logger.info(f"Message ID cache cleared by {interaction.user} (ID: {interaction.user.id}) in guild {interaction.guild.id}")
    except Exception as e:
        await interaction.response.send_message(f"Error clearing message cache: {e}", ephemeral=True)

# --- Bot Event Handlers ---
async def cleanup_db():
    """Properly close the database engine"""
    await engine.dispose()
    bot_logger.info("Database engine disposed.")

@client.event
async def on_ready():
    bot_logger.info(f"Preparing bot: {client.user}...")
    await init_db() # Initialize DB on startup

    initialize_url_cache()

    # Sync slash commands. This can be done globally or per-guild.
    # For simplicity, global sync. For faster updates during dev, sync to a specific guild.
    # await tree.sync(guild=discord.Object(id=DISCORD_GUILD_ID)) # Example for guild-specific sync
    await tree.sync()

    bot_logger.info(f"Logged in as {client.user} (ID: {client.user.id})")
    bot_logger.info("Command tree synced.")

    # List guilds the bot is in
    guild_names = [guild.name for guild in client.guilds]
    if guild_names:
        bot_logger.info(f"Currently in guilds: {', '.join(guild_names)}")
    else:
        bot_logger.info("Currently in no guilds.")

    bot_logger.info("Bot is ready and listening for commands and scheduled tasks.")

    # Start the background scheduler task
    if not hasattr(client, '_scheduler_task_started'): # Ensure it only starts once
        client._scheduler_task_started = True
        client.loop.create_task(background_scheduler())
        bot_logger.info("Background scheduler started.")

@client.event
async def on_disconnect():
    """Clean up when bot disconnects"""
    bot_logger.info("Bot disconnected. Cleaning up database connections...")
    await cleanup_db()

# --- Error Handling for Slash Commands ---
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You don't have the required permissions to use this command.", ephemeral=True)
    elif isinstance(error, app_commands.CommandNotFound):
        # This error should ideally not occur if commands are synced properly.
        await interaction.response.send_message("Sorry, I couldn't find that command. It might be an issue with command syncing.", ephemeral=True)
    elif isinstance(error, app_commands.CheckFailure): # More general check failure
        await interaction.response.send_message("You do not meet the requirements to use this command.", ephemeral=True)
    else:
        # Log the error for debugging
        bot_logger.error(f"Unhandled slash command error: {type(error).__name__} - {error}")
        # Inform the user generically
        if interaction.response.is_done():
            await interaction.followup.send("An unexpected error occurred while processing your command. Please try again later.", ephemeral=True)
        else:
            await interaction.response.send_message("An unexpected error occurred. Please try again later.", ephemeral=True)

# Run the bot
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        bot_logger.critical("CRITICAL ERROR: BOT_TOKEN is not set in the environment variables or .env file.")
        bot_logger.critical("Please ensure your Discord Bot Token is correctly configured.")
    else:
        bot_logger.info("Starting bot...")
        try:
            client.run(DISCORD_TOKEN, log_handler=None)
        except discord.LoginFailure:
            bot_logger.critical("CRITICAL ERROR: Login Failure. The provided Discord Bot Token is invalid.")
            bot_logger.critical("Please verify your BOT_TOKEN.")
        except discord.PrivilegedIntentsRequired:
            bot_logger.critical("CRITICAL ERROR: Privileged Intents Required. Ensure your bot has the necessary intents enabled in the Discord Developer Portal.")
        except Exception as e:
            bot_logger.critical(f"An unexpected critical error occurred while trying to run the bot: {type(e).__name__} - {e}")
