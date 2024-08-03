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
import sqlite3
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

# --- Database Setup and Helper Functions ---
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id INTEGER PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # Ensure 'ping_role_id' exists, set to NULL if not present
    cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('ping_role_id', NULL)")
    conn.commit()
    conn.close()

def add_channel_to_db(channel_id: int):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO channels (channel_id) VALUES (?)", (channel_id,))
    conn.commit()
    conn.close()

def remove_channel_from_db(channel_id: int):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
    conn.commit()
    conn.close()

def get_all_channels_from_db() -> list[int]:
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id FROM channels")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def set_ping_role_in_db(role_id: int | None):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    if role_id is None:
        cursor.execute("UPDATE config SET value = NULL WHERE key = 'ping_role_id'")
    else:
        cursor.execute("UPDATE config SET value = ? WHERE key = 'ping_role_id'", (str(role_id),))
    conn.commit()
    conn.close()

def get_ping_role_from_db() -> int | None:
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = 'ping_role_id'")
    row = cursor.fetchone()
    conn.close()
    if row and row[0] and row[0] != 'NULL': # Check for 'NULL' string
        try:
            return int(row[0])
        except ValueError:
            print(f"Warning: Invalid ping_role_id '{row[0]}' in database. Treating as None.")
            return None
    return None

# --- Repository and JSON Handling ---
def clone_or_update_repo(repo_url, local_repo_path):
    print(f"Cloning or updating repository from {repo_url} into {local_repo_path}...")
    if os.path.exists(local_repo_path):
        try:
            repo = git.Repo(local_repo_path)
            repo.remotes.origin.pull()
            print(f"Repository {local_repo_path} updated.")
        except git.exc.InvalidGitRepositoryError:
            print(f"Invalid git repository at {local_repo_path}. Removing and re-cloning.")
            shutil.rmtree(local_repo_path, ignore_errors=True)
            git.Repo.clone_from(repo_url, local_repo_path)
            print(f"Repository {local_repo_path} cloned fresh.")
        except Exception as e: # Catch other potential git errors
            print(f"Error updating repo {local_repo_path}: {e}. Attempting re-clone.")
            shutil.rmtree(local_repo_path, ignore_errors=True)
            git.Repo.clone_from(repo_url, local_repo_path)
            print(f"Repository {local_repo_path} cloned fresh after error.")
    else:
        git.Repo.clone_from(repo_url, local_repo_path)
        print(f"Repository {local_repo_path} cloned fresh.")

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


def format_message(role, ping_role_id: int | None):
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
    if ping_role_id:
        should_ping = False
        # Example: Ping for specific terms like "Winter 2026"
        # You might want to make this list/logic configurable or more dynamic
        if "winter 2026" in term_str.lower(): 
            should_ping = True
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

def format_reactivation_message(role, ping_role_id: int | None):
    company_name_str = role.get('company_name', 'N/A Company')
    title_str = role.get('title', 'N/A Title')
    url_str = role.get('url', '#')
    term_emoji, term_str = get_term_emoji_and_string(role)

    ping_str = ""
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
async def send_discord_message(message_content: str, channel_id: int):
    global failed_channels, channel_failure_counts # Ensure we're modifying the global sets/dicts
    if channel_id in failed_channels:
        print(f"Skipping previously failed channel ID {channel_id}")
        return

    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            print(f"Channel {channel_id} not in cache, attempting to fetch...")
            channel = await client.fetch_channel(channel_id)

        if not isinstance(channel, discord.TextChannel): # Check if it's a text channel
            print(f"Error: Channel ID {channel_id} is not a text channel. Skipping.")
            # Optionally, add to failed_channels if this is a persistent issue type
            channel_failure_counts[channel_id] = channel_failure_counts.get(channel_id, 0) + MAX_RETRIES # Mark as failed
            failed_channels.add(channel_id)
            return

        await channel.send(message_content)
        print(f"Successfully sent message to channel {channel_id}")
        if channel_id in channel_failure_counts: # Reset on success
            del channel_failure_counts[channel_id]
        if channel_id in failed_channels: # Also remove from perm failed if successful now
             failed_channels.remove(channel_id)
        await asyncio.sleep(1)  # Rate limiting

    except discord.NotFound:
        print(f"Channel {channel_id} not found.")
        channel_failure_counts[channel_id] = channel_failure_counts.get(channel_id, 0) + 1
    except discord.Forbidden:
        print(f"No permission for channel {channel_id}.")
        failed_channels.add(channel_id) # Add to permanent failures for permission issues
    except Exception as e:
        print(f"Error sending message to channel {channel_id}: {e}")
        channel_failure_counts[channel_id] = channel_failure_counts.get(channel_id, 0) + 1
    finally:
        # Add to failed_channels if retries exceeded
        if channel_failure_counts.get(channel_id, 0) >= MAX_RETRIES:
            print(f"Channel {channel_id} has failed {MAX_RETRIES} times, adding to failed channels for this session.")
            failed_channels.add(channel_id)


async def send_messages_to_all_configured_channels(message_content: str):
    channel_ids = get_all_channels_from_db()
    if not channel_ids:
        print("No channels configured in the database to send messages to.")
        return

    active_channel_ids = [ch_id for ch_id in channel_ids if ch_id not in failed_channels]
    
    tasks = [send_discord_message(message_content, ch_id) for ch_id in active_channel_ids]
    if tasks:
        await asyncio.gather(*tasks)

# --- Core Update Logic ---
def check_for_updates(repo_url, local_repo_path, json_file_path, previous_data_file, is_second_repo=False):
    print(f"Checking for updates in {local_repo_path}...")
    clone_or_update_repo(repo_url, local_repo_path)
    new_data = read_json(json_file_path)

    if os.path.exists(previous_data_file):
        try:
            with open(previous_data_file, 'r', encoding='utf-8') as file:
                old_data = json.load(file)
            print(f"Previous data loaded from {previous_data_file}.")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error reading or decoding previous data file {previous_data_file}: {e}. Starting fresh.")
            old_data = []
    else:
        old_data = []
        print(f"No previous data found at {previous_data_file}. Initializing.")

    new_roles = []
    deactivated_roles = []
    reactivated_roles = [] 

    old_roles_dict = {role['id']: role for role in old_data if 'id' in role and role['id'] is not None}
    ping_role_id = get_ping_role_from_db() 

    for new_role in new_data:
        if 'id' not in new_role or new_role['id'] is None:
            # print(f"Skipping new_role in {local_repo_path} due to missing or None ID: title='{new_role.get('title')}', company='{new_role.get('company_name')}'")
            continue

        old_role = old_roles_dict.get(new_role['id'])
        # Default 'active' and 'is_visible' to True if missing, as per common use cases
        new_role_is_active = _is_value_truthy(new_role.get('active', True))
        new_role_is_visible = _is_value_truthy(new_role.get('is_visible', True))

        if old_role:
            old_role_is_active = _is_value_truthy(old_role.get('active', True))

            if old_role_is_active and not new_role_is_active:
                deactivated_roles.append(new_role)
                # print(f"Role id='{new_role['id']}' ('{new_role['title']}' at '{new_role['company_name']}') in {local_repo_path} is now inactive.")
            elif is_second_repo and not old_role_is_active and new_role_is_active and new_role_is_visible:
                reactivated_roles.append(new_role)
                # print(f"Role id='{new_role['id']}' ('{new_role['title']}' at '{new_role['company_name']}') in {local_repo_path} is now re-activated.")
        elif new_role_is_visible and new_role_is_active: 
            new_roles.append(new_role)
            # print(f"New role found in {local_repo_path}: id='{new_role.get('id')}', title='{new_role['title']}' at '{new_role['company_name']}'")

    # Use client.loop for tasks if available, otherwise asyncio.get_event_loop()
    loop = client.loop if client and client.loop.is_running() else asyncio.get_event_loop()

    for role in new_roles:
        message = format_message(role, ping_role_id)
        loop.create_task(send_messages_to_all_configured_channels(message))

    for role in deactivated_roles:
        message = format_deactivation_message(role)
        loop.create_task(send_messages_to_all_configured_channels(message))

    if is_second_repo:
        for role in reactivated_roles:
            message = format_reactivation_message(role, ping_role_id)
            loop.create_task(send_messages_to_all_configured_channels(message))

    try:
        with open(previous_data_file, 'w', encoding='utf-8') as file:
            json.dump(new_data, file, indent=2) 
        print(f"Updated previous data with new data for {previous_data_file}.")
    except IOError as e:
        print(f"Error writing previous data file {previous_data_file}: {e}")


    if not new_roles and not deactivated_roles and (not is_second_repo or not reactivated_roles):
        print(f"No updates found for {local_repo_path}.")

# --- Scheduled Tasks ---
def scheduled_task_wrapper(repo_url, local_path, json_path, prev_data_file, is_second):
    # This function runs in a thread managed by 'schedule', so direct asyncio calls might be tricky.
    # For simplicity, `check_for_updates` creates tasks on the bot's event loop.
    print(f"Running scheduled check for {local_path} at {datetime.now()}")
    try:
        check_for_updates(repo_url, local_path, json_path, prev_data_file, is_second_repo=is_second)
    except Exception as e:
        print(f"Error during scheduled task for {local_path}: {e}")
        # Consider sending an alert to a Discord channel if critical errors occur
        # admin_alert_channel_id = 1234567890 # Replace with your admin channel ID
        # if client and client.loop.is_running():
        #    client.loop.create_task(send_discord_message(f"🚨 BOT ERROR in scheduled task for {local_path}: {type(e).__name__} - {e}", admin_alert_channel_id))


async def background_scheduler():
    # Schedule jobs
    schedule.every(1).minutes.do(scheduled_task_wrapper, repo_url=REPO_URL, local_path=LOCAL_REPO_PATH, json_path=JSON_FILE_PATH, prev_data_file=PREVIOUS_DATA_FILE, is_second=False)
    schedule.every(1).minutes.do(scheduled_task_wrapper, repo_url=REPO_URL_2, local_path=LOCAL_REPO_PATH_2, json_path=JSON_FILE_PATH_2, prev_data_file=PREVIOUS_DATA_FILE_2, is_second=True)
    
    memory_check_counter = 0
    while True:
        # Run all pending jobs in the 'schedule' library
        # This needs to be called from a context that doesn't block the asyncio loop for long.
        # Running schedule.run_pending() directly here is fine as it's quick if no jobs are due.
        schedule.run_pending()
        await asyncio.sleep(1) # Check schedule every second

        memory_check_counter += 1
        if memory_check_counter % 300 == 0: # Approx every 5 minutes (300 seconds)
            current_mem, peak_mem = tracemalloc.get_traced_memory()
            peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # macOS reports ru_maxrss in bytes, Linux in kilobytes. Convert if necessary.
            # For simplicity, assuming KB if not on darwin, otherwise convert.
            peak_rss_display = peak_rss_kb / 1024 if os.uname().sysname == 'Darwin' else peak_rss_kb
            
            print(f"--- Memory Usage ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
            print(f"Current Python memory (tracemalloc): {current_mem / 1024:.2f} KB")
            print(f"Peak Python memory (tracemalloc):    {peak_mem / 1024:.2f} KB")
            print(f"Peak RSS (OS):                       {peak_rss_display:.2f} KB")
            print("---------------------------------------------------")

# --- Slash Commands ---
@tree.command(name="add_channel", description="Adds a channel for bot notifications (Admin only).")
@app_commands.describe(channel="The text channel to add notifications to.")
async def add_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    try:
        add_channel_to_db(channel.id)
        await interaction.response.send_message(f"Channel {channel.mention} will now receive notifications.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error adding channel: {e}", ephemeral=True)

@tree.command(name="remove_channel", description="Removes a channel from bot notifications (Admin only).")
@app_commands.describe(channel="The text channel to remove notifications from.")
async def remove_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    try:
        remove_channel_from_db(channel.id)
        await interaction.response.send_message(f"Channel {channel.mention} will no longer receive notifications.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error removing channel: {e}", ephemeral=True)

@tree.command(name="list_channels", description="Lists all channels receiving notifications (Admin only).")
async def list_channels_cmd(interaction: discord.Interaction):
    try:
        channel_ids = get_all_channels_from_db()
        if not channel_ids:
            await interaction.response.send_message("No channels are currently configured for notifications.", ephemeral=True)
            return
        
        message_parts = ["Channels receiving notifications:"]
        for ch_id in channel_ids:
            ch = client.get_channel(ch_id) # Attempt to get channel object
            if ch:
                message_parts.append(f"- {ch.mention} (`{ch_id}`)")
            else: # If channel object not found (e.g., bot removed from server, channel deleted)
                message_parts.append(f"- Channel ID `{ch_id}` (Not currently accessible or may be invalid)")
        await interaction.response.send_message("\n".join(message_parts), ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error listing channels: {e}", ephemeral=True)

@tree.command(name="set_ping_role", description="Sets the role to ping for important updates (Admin only).")
@app_commands.describe(role="The role to ping. Leave empty to clear.")
async def set_ping_role_cmd(interaction: discord.Interaction, role: discord.Role | None = None):
    try:
        if role:
            set_ping_role_in_db(role.id)
            await interaction.response.send_message(f"Ping role set to {role.mention}.", ephemeral=True)
        else:
            set_ping_role_in_db(None) # Explicitly pass None
            await interaction.response.send_message("Ping role cleared.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error setting ping role: {e}", ephemeral=True)

@tree.command(name="get_ping_role", description="Shows the currently configured ping role (Admin only).")
async def get_ping_role_cmd(interaction: discord.Interaction):
    try:
        role_id = get_ping_role_from_db()
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
@client.event
async def on_ready():
    print(f"Preparing bot: {client.user}...")
    init_db() # Initialize DB on startup
    
    # Sync slash commands. This can be done globally or per-guild.
    # For simplicity, global sync. For faster updates during dev, sync to a specific guild.
    # await tree.sync(guild=discord.Object(id=YOUR_GUILD_ID)) # Example for guild-specific sync
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
