import json
import os
from datetime import datetime
import git
import schedule
import discord
from discord import app_commands
import asyncio
import shutil
import tracemalloc
import resource
import concurrent.futures
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import Column, Integer, select
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# Start tracing Python memory allocations
tracemalloc.start()

# Constants
REPO_URL = 'https://github.com/vanshb03/Summer2025-Internships'
LOCAL_REPO_PATH = 'Summer2025-Internships'
JSON_FILE_PATH = os.path.join(LOCAL_REPO_PATH, '.github', 'scripts', 'listings.json')
PREVIOUS_DATA_FILE = 'previous_data.json'

REPO_URL_2 = 'https://github.com/SimplifyJobs/Summer2025-Internships'
LOCAL_REPO_PATH_2 = 'Summer2025-Internships_Simplify'
JSON_FILE_PATH_2 = os.path.join(LOCAL_REPO_PATH_2, '.github', 'scripts', 'listings.json')
PREVIOUS_DATA_FILE_2 = 'previous_data_simplify.json'

DISCORD_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_FILE = 'bot_config.db'
DATABASE_URL = f'sqlite+aiosqlite:///{DATABASE_FILE}'
MAX_RETRIES = 3

BIG_TECH_COMPANIES = [
    "openai", "anthropic", "google", "nvidia", "bloomberg", "snap",
    "meta", "apple", "amazon", "microsoft", "netflix", "tesla", "databricks", "figma", "roblox",
    "square", "block", "stripe", "airbnb", "uber", "lyft", "doordash", "instacart", "palantir",
    "snowflake", "salesforce", "oracle", "sap", "adobe", "vmware", "ibm", "intel", "amd",
    "qualcomm", "broadcom", "texas instruments", "cisco", "dell", "hp", "atlassian", "zoom",
    "workday", "servicenow", "twilio", "shopify", "spotify", "pinterest", "twitter", "x",
    "linkedin", "github", "robinhood", "coinbase", "jane street", "hudson river trading",
    "citadel", "two sigma", "jump trading", "drw", "akamai", "cloudflare", "mongodb",
    "splunk", "reddit", "discord", "tiktok", "bytedance", "cruise", "waymo", "rivian", "lucid"
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
                print(f"Warning: Invalid ping_role_id '{row.ping_role_id}' for guild {guild_id}. Treating as None.")
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
                print(f"Warning: Invalid ping_role_id '{row.ping_role_id}' for guild {row.guild_id}. Skipping.")
        return guild_roles

# --- Repository and JSON Handling ---
async def update_both_repos():
    """Update both repositories sequentially in one threadpool executor"""
    def _sync_update_both():
        print(f"Cloning or updating repository from {REPO_URL} into {LOCAL_REPO_PATH}...")
        if os.path.exists(LOCAL_REPO_PATH):
            try:
                repo = git.Repo(LOCAL_REPO_PATH)
                repo.remotes.origin.pull()
                print(f"Repository {LOCAL_REPO_PATH} updated.")
            except git.exc.InvalidGitRepositoryError:
                print(f"Invalid git repository at {LOCAL_REPO_PATH}. Removing and re-cloning.")
                shutil.rmtree(LOCAL_REPO_PATH, ignore_errors=True)
                git.Repo.clone_from(REPO_URL, LOCAL_REPO_PATH)
                print(f"Repository {LOCAL_REPO_PATH} cloned fresh.")
            except Exception as e:
                print(f"Error updating repo {LOCAL_REPO_PATH}: {e}. Attempting re-clone.")
                shutil.rmtree(LOCAL_REPO_PATH, ignore_errors=True)
                git.Repo.clone_from(REPO_URL, LOCAL_REPO_PATH)
                print(f"Repository {LOCAL_REPO_PATH} cloned fresh after error.")
        else:
            git.Repo.clone_from(REPO_URL, LOCAL_REPO_PATH)
            print(f"Repository {LOCAL_REPO_PATH} cloned fresh.")

        print(f"Cloning or updating repository from {REPO_URL_2} into {LOCAL_REPO_PATH_2}...")
        if os.path.exists(LOCAL_REPO_PATH_2):
            try:
                repo = git.Repo(LOCAL_REPO_PATH_2)
                repo.remotes.origin.pull()
                print(f"Repository {LOCAL_REPO_PATH_2} updated.")
            except git.exc.InvalidGitRepositoryError:
                print(f"Invalid git repository at {LOCAL_REPO_PATH_2}. Removing and re-cloning.")
                shutil.rmtree(LOCAL_REPO_PATH_2, ignore_errors=True)
                git.Repo.clone_from(REPO_URL_2, LOCAL_REPO_PATH_2)
                print(f"Repository {LOCAL_REPO_PATH_2} cloned fresh.")
            except Exception as e:
                print(f"Error updating repo {LOCAL_REPO_PATH_2}: {e}. Attempting re-clone.")
                shutil.rmtree(LOCAL_REPO_PATH_2, ignore_errors=True)
                git.Repo.clone_from(REPO_URL_2, LOCAL_REPO_PATH_2)
                print(f"Repository {LOCAL_REPO_PATH_2} cloned fresh after error.")
        else:
            git.Repo.clone_from(REPO_URL_2, LOCAL_REPO_PATH_2)
            print(f"Repository {LOCAL_REPO_PATH_2} cloned fresh.")
    
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        await loop.run_in_executor(executor, _sync_update_both)

def read_json(json_file_path):
    print(f"Reading JSON file from {json_file_path}...")
    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
        print(f"JSON file read successfully from {json_file_path}, {len(data)} items loaded.")
        return data
    except FileNotFoundError:
        print(f"Error: JSON file not found at {json_file_path}.")
        return []
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {json_file_path}.")
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
        # Example: Ping for specific terms like "Winter 2026"
        # You might want to make this list/logic configurable or more dynamic
        if any(company.lower() in company_name_str.lower() for company in BIG_TECH_COMPANIES):
            should_ping = True
        
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
        if any(company.lower() in company_name_str.lower() for company in BIG_TECH_COMPANIES):
            should_ping = True
        if should_ping:
            ping_str = f"<@&{ping_role_id}> "

    return (f"{EMOJI_REACTIVATED} {ping_str}**{company_name_str}** internship is active again!\n"
            f"[{title_str}]({url_str}) - Term: {term_emoji} {term_str}\n"
            f"Reactivated: {datetime.now().strftime('%b %d')}")


# --- Discord Interaction ---
async def send_discord_message(message_content: str, guild_id: int, channel_id: int):
    global failed_channels, channel_failure_counts # Ensure we're modifying the global sets/dicts
    
    # Use a composite key for failed channels since we now track by guild+channel
    channel_key = f"{guild_id}:{channel_id}"
    
    if channel_key in failed_channels:
        print(f"Skipping previously failed channel ID {channel_id} in guild {guild_id}")
        return

    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            print(f"Channel {channel_id} not in cache, attempting to fetch...")
            channel = await client.fetch_channel(channel_id)

        if not isinstance(channel, discord.TextChannel): # Check if it's a text channel
            print(f"Error: Channel ID {channel_id} is not a text channel. Skipping.")
            # Optionally, add to failed_channels if this is a persistent issue type
            channel_failure_counts[channel_key] = channel_failure_counts.get(channel_key, 0) + MAX_RETRIES # Mark as failed
            failed_channels.add(channel_key)
            return

        await channel.send(message_content)
        print(f"Successfully sent message to channel {channel_id} in guild {guild_id}")
        if channel_key in channel_failure_counts: # Reset on success
            del channel_failure_counts[channel_key]
        if channel_key in failed_channels: # Also remove from perm failed if successful now
             failed_channels.remove(channel_key)
        await asyncio.sleep(1)  # Rate limiting

    except discord.NotFound:
        print(f"Channel {channel_id} not found in guild {guild_id}.")
        channel_failure_counts[channel_key] = channel_failure_counts.get(channel_key, 0) + 1
    except discord.Forbidden:
        print(f"No permission for channel {channel_id} in guild {guild_id}.")
        failed_channels.add(channel_key) # Add to permanent failures for permission issues
    except Exception as e:
        print(f"Error sending message to channel {channel_id} in guild {guild_id}: {e}")
        channel_failure_counts[channel_key] = channel_failure_counts.get(channel_key, 0) + 1
    finally:
        # Add to failed_channels if retries exceeded
        if channel_failure_counts.get(channel_key, 0) >= MAX_RETRIES:
            print(f"Channel {channel_id} in guild {guild_id} has failed {MAX_RETRIES} times, adding to failed channels for this session.")
            failed_channels.add(channel_key)


async def send_messages_to_all_configured_channels(message_content: str, guild_ping_roles: dict[int, int] = None):
    channel_configs = await get_all_channels_from_db()
    if not channel_configs:
        print("No channels configured in the database to send messages to.")
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

# --- Scheduled Tasks ---
async def combined_scheduled_task():
    """Combined scheduled task that processes both repos sequentially"""
    global is_task_running
    
    if is_task_running:
        print("Previous task still running, skipping this execution")
        return
    
    is_task_running = True
    print(f"Running scheduled check for both repos at {datetime.now()}")
    try:
        await update_both_repos()

        new_data = read_json(JSON_FILE_PATH)
        if os.path.exists(PREVIOUS_DATA_FILE):
            try:
                with open(PREVIOUS_DATA_FILE, 'r', encoding='utf-8') as file:
                    old_data = json.load(file)
                print(f"Previous data loaded from {PREVIOUS_DATA_FILE}.")
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"Error reading or decoding previous data file {PREVIOUS_DATA_FILE}: {e}. Starting fresh.")
                old_data = []
        else:
            old_data = []
            print(f"No previous data found at {PREVIOUS_DATA_FILE}. Initializing.")

        await process_repo_updates(new_data, old_data, PREVIOUS_DATA_FILE, LOCAL_REPO_PATH, is_second_repo=False)
        
        new_data_2 = read_json(JSON_FILE_PATH_2)
        if os.path.exists(PREVIOUS_DATA_FILE_2):
            try:
                with open(PREVIOUS_DATA_FILE_2, 'r', encoding='utf-8') as file:
                    old_data_2 = json.load(file)
                print(f"Previous data loaded from {PREVIOUS_DATA_FILE_2}.")
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"Error reading or decoding previous data file {PREVIOUS_DATA_FILE_2}: {e}. Starting fresh.")
                old_data_2 = []
        else:
            old_data_2 = []
            print(f"No previous data found at {PREVIOUS_DATA_FILE_2}. Initializing.")

        await process_repo_updates(new_data_2, old_data_2, PREVIOUS_DATA_FILE_2, LOCAL_REPO_PATH_2, is_second_repo=True)
        
    except Exception as e:
        is_task_running = False
        print(f"Error during combined scheduled task: {e}")
    finally:
        is_task_running = False
        print(f"Scheduled task completed at {datetime.now()}")

def try_start_scheduled_task():
    """Function to start scheduled task only if not already running"""
    if not is_task_running:
        asyncio.create_task(combined_scheduled_task())
    else:
        print("Scheduled task already running, skipping")

async def process_repo_updates(new_data, old_data, previous_data_file, local_repo_path, is_second_repo=False):
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
        channel_configs = await get_all_channels_from_db()
        for guild_id, channel_id in channel_configs:
            if f"{guild_id}:{channel_id}" not in failed_channels:
                message = format_message(role, guild_id, guild_ping_roles)
                loop.create_task(send_discord_message(message, guild_id, channel_id))

    for role in deactivated_roles:
        message = format_deactivation_message(role)
        loop.create_task(send_messages_to_all_configured_channels(message, guild_ping_roles))

    if is_second_repo:
        for role in reactivated_roles:
            channel_configs = await get_all_channels_from_db()
            for guild_id, channel_id in channel_configs:
                if f"{guild_id}:{channel_id}" not in failed_channels:
                    message = format_reactivation_message(role, guild_id, guild_ping_roles)
                    loop.create_task(send_discord_message(message, guild_id, channel_id))

    try:
        with open(previous_data_file, 'w', encoding='utf-8') as file:
            json.dump(new_data, file, indent=2) 
        print(f"Updated previous data with new data for {previous_data_file}.")
    except IOError as e:
        print(f"Error writing previous data file {previous_data_file}: {e}")

    if not new_roles and not deactivated_roles and (not is_second_repo or not reactivated_roles):
        print(f"No updates found for {local_repo_path}.")


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
            
            print(f"--- Memory Usage ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
            print(f"Current Python memory (tracemalloc): {current_mem / 1024:.2f} KB")
            print(f"Peak Python memory (tracemalloc):    {peak_mem / 1024:.2f} KB")
            print(f"Peak RSS (OS):                       {peak_rss_display:.2f} KB")
            print("---------------------------------------------------")

# --- Slash Commands ---
@tree.command(name="set_channel", description="Sets the notification channel for this guild (Admin only).")
@app_commands.describe(channel="The text channel to receive notifications. Leave empty to remove the current channel.")
async def set_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel | None = None):
    try:
        if channel:
            await set_guild_channel(interaction.guild.id, channel.id)
            await interaction.response.send_message(f"Notification channel set to {channel.mention}.", ephemeral=True)
        else:
            current_channel_id = await get_guild_channel(interaction.guild.id)
            if current_channel_id is None:
                await interaction.response.send_message("No notification channel is currently configured for this guild.", ephemeral=True)
                return
                
            await set_guild_channel(interaction.guild.id, None)
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
            await interaction.response.send_message(f"Ping role set to {role.mention}.", ephemeral=True)
        else:
            await set_guild_ping_role(interaction.guild.id, None) # Explicitly pass None
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

# --- Bot Event Handlers ---
async def cleanup_db():
    """Properly close the database engine"""
    await engine.dispose()
    print("Database engine disposed.")

@client.event
async def on_ready():
    print(f"Preparing bot: {client.user}...")
    await init_db() # Initialize DB on startup
    
    # Sync slash commands. This can be done globally or per-guild.
    # For simplicity, global sync. For faster updates during dev, sync to a specific guild.
    # await tree.sync(guild=discord.Object(id=DISCORD_GUILD_ID)) # Example for guild-specific sync
    await tree.sync() 
    
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print("Command tree synced.")
    
    # List guilds the bot is in
    guild_names = [guild.name for guild in client.guilds]
    if guild_names:
        print(f"Currently in guilds: {', '.join(guild_names)}")
    else:
        print("Currently in no guilds.")
        
    print("Bot is ready and listening for commands and scheduled tasks.")
    
    # Start the background scheduler task
    if not hasattr(client, '_scheduler_task_started'): # Ensure it only starts once
        client._scheduler_task_started = True
        client.loop.create_task(background_scheduler())
        print("Background scheduler started.")

@client.event
async def on_disconnect():
    """Clean up when bot disconnects"""
    print("Bot disconnected. Cleaning up database connections...")
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
        print(f"Unhandled slash command error: {type(error).__name__} - {error}")
        # Inform the user generically
        if interaction.response.is_done():
            await interaction.followup.send("An unexpected error occurred while processing your command. Please try again later.", ephemeral=True)
        else:
            await interaction.response.send_message("An unexpected error occurred. Please try again later.", ephemeral=True)

# Run the bot
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("CRITICAL ERROR: BOT_TOKEN is not set in the environment variables or .env file.")
        print("Please ensure your Discord Bot Token is correctly configured.")
    else:
        print("Starting bot...")
        try:
            client.run(DISCORD_TOKEN)
        except discord.LoginFailure:
            print("CRITICAL ERROR: Login Failure. The provided Discord Bot Token is invalid.")
            print("Please verify your BOT_TOKEN.")
        except discord.PrivilegedIntentsRequired:
            print("CRITICAL ERROR: Privileged Intents Required. Ensure your bot has the necessary intents enabled in the Discord Developer Portal.")
        except Exception as e:
            print(f"An unexpected critical error occurred while trying to run the bot: {type(e).__name__} - {e}")
