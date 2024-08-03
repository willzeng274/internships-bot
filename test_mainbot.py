import pytest
import asyncio
import json
import os
import sqlite3
import io # Required for mocking file operations
import builtins # Required for mocking open
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock, mock_open, MagicMock
import shutil
import tracemalloc
import discord
from discord.ext import commands
import git

# Assuming mainbot.py is in the same directory or accessible via PYTHONPATH
import mainbot

# Helper to reset mainbot's global state if necessary between tests
def reset_mainbot_globals():
    mainbot.failed_channels = set()
    mainbot.channel_failure_counts = {}

# Sample data for roles
SAMPLE_ROLE_SUMMER = {
    'id': '1',
    'company_name': 'Test Summer Inc',
    'title': 'Summer Engineering Intern',
    'url': 'https://example.com/summer',
    'locations': ['New York, NY', 'Remote'],
    'terms': ['Summer 2025'],
    'sponsorship': 'Available',
    'active': True,
    'is_visible': True
}

SAMPLE_ROLE_WINTER_BIGTECH = {
    'id': '2',
    'company_name': 'Google', # A "big tech" company
    'title': 'Winter Software Intern',
    'url': 'https://example.com/winter',
    'locations': ['Mountain View, CA'],
    'season': 'Winter 2026', # Uses 'season' key
    'sponsorship': 'Not Available',
    'active': True,
    'is_visible': True
}

SAMPLE_ROLE_FALL_UNKNOWN_SPONSOR = {
    'id': '3',
    'company_name': 'Autumnal Co',
    'title': 'Fall Data Analyst Intern',
    'url': 'https://example.com/fall',
    'locations': ['Chicago, IL'],
    'terms': ['Fall 2025'],
    # Missing sponsorship, should default
    'active': True,
    'is_visible': True
}

SAMPLE_ROLE_UNKNOWN_TERM = {
    'id': '4',
    'company_name': 'Mystery Terms LLC',
    'title': 'Intern of Unknown Term',
    'url': 'https://example.com/unknown_term',
    'locations': ['Remote'],
    # No terms or season
    'sponsorship': 'Yes',
    'active': True,
    'is_visible': True
}

SAMPLE_ROLE_NO_URL_UNKNOWN_TERM = {
    'id': '5',
    'company_name': 'No URL Corp',
    'title': 'Intern No URL Unknown Term',
    # No URL
    'locations': ['Classified'],
    'active': True,
    'is_visible': True
}

@pytest.fixture
def temp_workspace(tmp_path):
    """Creates a temporary workspace with mock repo paths and data files."""
    original_paths = {
        k: getattr(mainbot, k) for k in [
            'LOCAL_REPO_PATH', 'LOCAL_REPO_PATH_2', 'JSON_FILE_PATH',
            'JSON_FILE_PATH_2', 'PREVIOUS_DATA_FILE', 'PREVIOUS_DATA_FILE_2', 'DATABASE_FILE'
        ]
    }
    mainbot.LOCAL_REPO_PATH = str(tmp_path / "Summer2025-Internships")
    mainbot.LOCAL_REPO_PATH_2 = str(tmp_path / "Summer2025-Internships_Simplify")
    os.makedirs(mainbot.LOCAL_REPO_PATH, exist_ok=True)
    os.makedirs(mainbot.LOCAL_REPO_PATH_2, exist_ok=True)
    mainbot.JSON_FILE_PATH = os.path.join(mainbot.LOCAL_REPO_PATH, ".github", "scripts", "listings.json")
    mainbot.JSON_FILE_PATH_2 = os.path.join(mainbot.LOCAL_REPO_PATH_2, ".github", "scripts", "listings.json")
    os.makedirs(os.path.dirname(mainbot.JSON_FILE_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(mainbot.JSON_FILE_PATH_2), exist_ok=True)
    mainbot.PREVIOUS_DATA_FILE = str(tmp_path / "previous_data.json")
    mainbot.PREVIOUS_DATA_FILE_2 = str(tmp_path / "previous_data_simplify.json")
    mainbot.DATABASE_FILE = str(tmp_path / "test_bot_config.db")
    
    mainbot.init_db()
    
    yield tmp_path

    if os.path.exists(mainbot.DATABASE_FILE):
        os.remove(mainbot.DATABASE_FILE)
    
    # Restore original paths
    for key, value in original_paths.items():
        setattr(mainbot, key, value)

@pytest.fixture
def mock_git_repo():
    with patch('git.Repo') as MockRepo:
        mock_repo_instance = Mock()
        mock_repo_instance.remotes.origin.pull = Mock()
        MockRepo.clone_from = Mock()
        MockRepo.return_value = mock_repo_instance
        yield MockRepo

@pytest.fixture
def mock_discord_client():
    with patch('mainbot.client', new_callable=AsyncMock) as mock_client:
        mock_client.loop = asyncio.get_event_loop()
        mock_client.get_channel = Mock(return_value=AsyncMock(spec=discord.TextChannel))
        mock_client.fetch_channel = AsyncMock(return_value=AsyncMock(spec=discord.TextChannel))
        # Ensure tree is attached to the mock client for slash command tests
        mock_client.tree = mainbot.tree 
        yield mock_client

@pytest.fixture(autouse=True)
def reset_globals_fixture():
    reset_mainbot_globals()

class TestDatabaseOperations:
    def test_init_db(self, temp_workspace):
        conn = sqlite3.connect(mainbot.DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='channels'")
        assert cursor.fetchone() is not None
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='config'")
        assert cursor.fetchone() is not None
        cursor.execute("SELECT key, value FROM config WHERE key='ping_role_id'")
        assert cursor.fetchone() == ('ping_role_id', None)
        conn.close()

    def test_channel_management(self, temp_workspace):
        mainbot.add_channel_to_db(12345)
        assert mainbot.get_all_channels_from_db() == [12345]
        mainbot.add_channel_to_db(67890)
        assert sorted(mainbot.get_all_channels_from_db()) == [12345, 67890]
        mainbot.remove_channel_from_db(12345)
        assert mainbot.get_all_channels_from_db() == [67890]
        mainbot.remove_channel_from_db(12345)
        assert mainbot.get_all_channels_from_db() == [67890]

    def test_ping_role_management(self, temp_workspace):
        assert mainbot.get_ping_role_from_db() is None
        mainbot.set_ping_role_in_db(98765)
        assert mainbot.get_ping_role_from_db() == 98765
        mainbot.set_ping_role_in_db(None)
        assert mainbot.get_ping_role_from_db() is None
        conn = sqlite3.connect(mainbot.DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE config SET value = 'invalid_role' WHERE key = 'ping_role_id'")
        conn.commit()
        conn.close()
        assert mainbot.get_ping_role_from_db() is None

class TestRepositoryAndJsonHandling:
    def test_clone_or_update_repo_clone(self, mock_git_repo, temp_workspace):
        # Ensure the target directory is removed if it exists to test cloning properly
        if os.path.exists(mainbot.LOCAL_REPO_PATH):
            shutil.rmtree(mainbot.LOCAL_REPO_PATH) 
        mainbot.clone_or_update_repo("some_url", mainbot.LOCAL_REPO_PATH)
        mock_git_repo.clone_from.assert_called_with("some_url", mainbot.LOCAL_REPO_PATH)

    def test_clone_or_update_repo_pull(self, mock_git_repo, temp_workspace):
        mainbot.clone_or_update_repo("some_url", mainbot.LOCAL_REPO_PATH)
        mock_git_repo.return_value.remotes.origin.pull.assert_called_once()

    def test_read_json_success(self, temp_workspace):
        data = [SAMPLE_ROLE_SUMMER]
        json_path = os.path.join(temp_workspace, "test_listings.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        read_data = mainbot.read_json(json_path)
        assert read_data == data

    def test_read_json_not_found(self):
        read_data = mainbot.read_json("non_existent_file.json")
        assert read_data == []

    def test_read_json_decode_error(self, temp_workspace):
        json_path = os.path.join(temp_workspace, "invalid_listings.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            f.write("this is not json")
        read_data = mainbot.read_json(json_path)
        assert read_data == []

class TestMessageFormatting:
    @pytest.mark.parametrize("role_data, expected_emoji, expected_term_contains", [
        (SAMPLE_ROLE_SUMMER, mainbot.EMOJI_SUMMER, "Summer 2025"),
        (SAMPLE_ROLE_WINTER_BIGTECH, mainbot.EMOJI_WINTER, "Winter 2026"),
        (SAMPLE_ROLE_FALL_UNKNOWN_SPONSOR, mainbot.EMOJI_FALL, "Fall 2025"),
        (SAMPLE_ROLE_UNKNOWN_TERM, mainbot.EMOJI_UNKNOWN_TERM, "Unknown"),
        ({'season': 'Spring 2025'}, mainbot.EMOJI_UNKNOWN_TERM, "Spring 2025"),
    ])
    def test_get_term_emoji_and_string(self, role_data, expected_emoji, expected_term_contains):
        emoji, term_str = mainbot.get_term_emoji_and_string(role_data)
        assert emoji == expected_emoji
        assert expected_term_contains in term_str

    def test_format_message_new_internship(self):
        msg = mainbot.format_message(SAMPLE_ROLE_SUMMER, ping_role_id=None)
        assert mainbot.EMOJI_NEW in msg
        assert SAMPLE_ROLE_SUMMER['company_name'] in msg
        assert "**Sponsorship:** `Available`" in msg # Corrected assertion
        assert "Posted:" in msg

    def test_format_message_new_internship_with_ping(self):
        ping_role = 111222333
        msg = mainbot.format_message(SAMPLE_ROLE_WINTER_BIGTECH, ping_role_id=ping_role)
        assert f"<@&{ping_role}>" in msg
        assert "Google" in msg
        assert mainbot.EMOJI_WINTER in msg

    def test_format_message_unknown_term_with_url(self):
        msg = mainbot.format_message(SAMPLE_ROLE_UNKNOWN_TERM, ping_role_id=None)
        assert mainbot.EMOJI_NEW in msg
        assert "Term: ❓ Unknown. Review details: <https://example.com/unknown_term>" in msg
    
    def test_format_message_unknown_term_no_url(self):
        msg = mainbot.format_message(SAMPLE_ROLE_NO_URL_UNKNOWN_TERM, ping_role_id=None)
        assert mainbot.EMOJI_NEW in msg
        assert "Term: ❓ Unknown. More details unavailable." in msg

    def test_format_deactivation_message(self):
        msg = mainbot.format_deactivation_message(SAMPLE_ROLE_SUMMER)
        assert mainbot.EMOJI_DEACTIVATED in msg
        assert SAMPLE_ROLE_SUMMER['company_name'] in msg
        assert f"Term: {mainbot.EMOJI_SUMMER} Summer 2025" in msg
        assert "Deactivated:" in msg

    def test_format_reactivation_message(self):
        msg = mainbot.format_reactivation_message(SAMPLE_ROLE_WINTER_BIGTECH, ping_role_id=None)
        assert mainbot.EMOJI_REACTIVATED in msg
        assert "Google" in msg
        assert f"Term: {mainbot.EMOJI_WINTER} Winter 2026" in msg
        assert "Reactivated:" in msg
    
    def test_format_reactivation_message_with_ping(self):
        ping_role = 111222333
        msg = mainbot.format_reactivation_message(SAMPLE_ROLE_WINTER_BIGTECH, ping_role_id=ping_role)
        assert f"<@&{ping_role}>" in msg

@pytest.mark.asyncio
class TestDiscordInteractions:
    async def test_send_discord_message_success(self, mock_discord_client, temp_workspace):
        channel_id = 123
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_discord_client.get_channel.return_value = mock_channel
        await mainbot.send_discord_message("Test content", channel_id)
        mock_channel.send.assert_called_once_with("Test content")
        assert channel_id not in mainbot.failed_channels

    async def test_send_discord_message_not_found_then_fail(self, mock_discord_client, temp_workspace):
        channel_id = 456
        mock_discord_client.get_channel.return_value = None
        mock_discord_client.fetch_channel.side_effect = discord.NotFound(Mock(), "not found")
        for _ in range(mainbot.MAX_RETRIES):
            await mainbot.send_discord_message("Test content", channel_id)
        assert channel_id in mainbot.failed_channels
        assert mainbot.channel_failure_counts[channel_id] == mainbot.MAX_RETRIES

    async def test_send_discord_message_forbidden(self, mock_discord_client, temp_workspace):
        channel_id = 789
        mock_discord_client.get_channel.side_effect = discord.Forbidden(Mock(), "forbidden")
        await mainbot.send_discord_message("Test content", channel_id)
        assert channel_id in mainbot.failed_channels

    async def test_send_messages_to_all_configured_channels(self, mock_discord_client, temp_workspace):
        mainbot.add_channel_to_db(111)
        mainbot.add_channel_to_db(222)
        mainbot.failed_channels.add(333)
        mainbot.add_channel_to_db(333)
        with patch('mainbot.send_discord_message', new_callable=AsyncMock) as mock_send_single:
            await mainbot.send_messages_to_all_configured_channels("Hello all")
            assert mock_send_single.call_count == 2
            mock_send_single.assert_any_call("Hello all", 111)
            mock_send_single.assert_any_call("Hello all", 222)

@pytest.mark.asyncio
class TestCoreUpdateLogic:
    @pytest.fixture
    def mock_file_io(self, temp_workspace):
        fs_content = {}
        def _mocked_open(file_path, mode='r', encoding=None):
            file_path_str = str(file_path)
            if mode.startswith('r'):
                if file_path_str not in fs_content:
                    raise FileNotFoundError(f"[Mock IO] File not found: {file_path_str}")
                # Return a new mock_open instance each time for reading
                return mock_open(read_data=fs_content[file_path_str])(file_path_str, mode, encoding=encoding)
            elif mode.startswith('w'):
                # For writing, json.dump will call write on this object.
                # We need a file-like object whose 'name' can be accessed by json.dump mock,
                # and whose 'write' method can be used by the actual json.dump.
                mock_file = MagicMock(spec=io.StringIO) # Use StringIO for text mode
                mock_file.name = file_path_str # So json.dump mock knows which file it is
                
                # This inner write will capture what json.dump tries to write if we weren't mocking json.dump
                # However, since we *are* mocking json.dump, this specific write isn't critical
                # but the file object needs to be acceptable to the real json.dump if it were called.
                def side_effect_write(data_written):
                    # This would normally write to an in-memory buffer for the mock file
                    # For our purpose, json.dump mock handles the fs_content update.
                    pass 
                mock_file.write = MagicMock(side_effect=side_effect_write) 
                return mock_file
            else:
                # Fallback for other modes, though not expected for json load/dump
                return mock_open()(file_path_str, mode, encoding=encoding)

        def _mocked_json_dump(data, file_obj, **kwargs):
            # file_obj here is the MagicMock returned by _mocked_open for write mode
            # Its .name attribute was set to the original file_path_str
            fs_content[file_obj.name] = json.dumps(data, **kwargs) # Store serialized string

        with patch('builtins.open', _mocked_open) as patched_open, \
             patch('json.dump', _mocked_json_dump) as patched_json_dump:
            yield fs_content # Provide the fs_content for tests to inspect/setup

    @pytest.fixture
    def mock_check_deps(self, mock_git_repo, mock_discord_client, mock_file_io):
        fs_content_from_mock_file_io = mock_file_io
        with patch('mainbot.clone_or_update_repo') as mock_clone, \
             patch('mainbot.read_json') as mock_read_json, \
             patch('mainbot.send_messages_to_all_configured_channels', new_callable=AsyncMock) as mock_send_all:
            
            mainbot.set_ping_role_in_db(None)
            yield {"clone": mock_clone, "read_json": mock_read_json, 
                   "send_all": mock_send_all, "fs_content": fs_content_from_mock_file_io}

    async def test_check_for_updates_new_roles(self, mock_check_deps):
        mock_check_deps['read_json'].return_value = [SAMPLE_ROLE_SUMMER, SAMPLE_ROLE_WINTER_BIGTECH]
        # fs_content starts empty, so previous_data.json won't be found by mocked open

        mainbot.check_for_updates("url1", mainbot.LOCAL_REPO_PATH, mainbot.JSON_FILE_PATH, mainbot.PREVIOUS_DATA_FILE)
        await asyncio.sleep(0.01) 

        assert mock_check_deps['clone'].call_count == 1
        assert mock_check_deps['read_json'].call_count == 1
        assert mock_check_deps['send_all'].call_count == 2
        mock_check_deps['json_dump'].assert_called_once()
        # Verify content of mocked PREVIOUS_DATA_FILE
        assert mainbot.PREVIOUS_DATA_FILE in mock_check_deps['fs_content']
        written_data = json.loads(mock_check_deps['fs_content'][mainbot.PREVIOUS_DATA_FILE])
        assert len(written_data) == 2

    async def test_check_for_updates_deactivated_role(self, mock_check_deps):
        old_data = [{**SAMPLE_ROLE_SUMMER, 'id': '10', 'active': True}]
        mock_check_deps['fs_content'][mainbot.PREVIOUS_DATA_FILE] = json.dumps(old_data)
        new_data = [{**SAMPLE_ROLE_SUMMER, 'id': '10', 'active': False}]
        mock_check_deps['read_json'].return_value = new_data

        mainbot.check_for_updates("url1", mainbot.LOCAL_REPO_PATH, mainbot.JSON_FILE_PATH, mainbot.PREVIOUS_DATA_FILE)
        await asyncio.sleep(0.01)

        assert mock_check_deps['send_all'].call_count == 1
        sent_message = mock_check_deps['send_all'].call_args[0][0]
        assert mainbot.EMOJI_DEACTIVATED in sent_message

    async def test_check_for_updates_reactivated_role_second_repo(self, mock_check_deps):
        old_data = [{**SAMPLE_ROLE_WINTER_BIGTECH, 'id': '20', 'active': False, 'is_visible': True}]
        mock_check_deps['fs_content'][mainbot.PREVIOUS_DATA_FILE_2] = json.dumps(old_data)
        new_data = [{**SAMPLE_ROLE_WINTER_BIGTECH, 'id': '20', 'active': True, 'is_visible': True}]
        mock_check_deps['read_json'].return_value = new_data
        mainbot.set_ping_role_in_db(777888999)

        mainbot.check_for_updates("url2", mainbot.LOCAL_REPO_PATH_2, mainbot.JSON_FILE_PATH_2, mainbot.PREVIOUS_DATA_FILE_2, is_second_repo=True)
        await asyncio.sleep(0.01)

        assert mock_check_deps['send_all'].call_count == 1
        sent_message = mock_check_deps['send_all'].call_args[0][0]
        assert mainbot.EMOJI_REACTIVATED in sent_message 
        assert "<@&777888999>" in sent_message

    async def test_check_for_updates_no_changes(self, mock_check_deps):
        data = [SAMPLE_ROLE_SUMMER]
        mock_check_deps['fs_content'][mainbot.PREVIOUS_DATA_FILE] = json.dumps(data)
        mock_check_deps['read_json'].return_value = data

        mainbot.check_for_updates("url1", mainbot.LOCAL_REPO_PATH, mainbot.JSON_FILE_PATH, mainbot.PREVIOUS_DATA_FILE)
        await asyncio.sleep(0.01)
        mock_check_deps['send_all'].assert_not_called()

@pytest.mark.asyncio
class TestSlashCommands:
    @pytest.fixture
    def mock_interaction(self):
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock(spec=discord.InteractionResponse)
        interaction.guild = AsyncMock(spec=discord.Guild)
        # Mock the client on the interaction if slash commands need it (e.g., for tree access, not common)
        interaction.client = AsyncMock(spec=mainbot.discord.Client) 
        return interaction

    async def test_add_channel_cmd(self, mock_interaction, temp_workspace, mock_discord_client):
        channel_mock = MagicMock(spec=discord.TextChannel)
        channel_mock.id = 123456789
        channel_mock.mention = "<#123456789>"
        # Ensure mainbot.tree.add_channel_cmd is the command object
        await mainbot.add_channel_cmd.callback(mainbot.tree, mock_interaction, channel=channel_mock)
        mock_interaction.response.send_message.assert_called_once_with(
            f"Channel {channel_mock.mention} will now receive notifications.", ephemeral=True
        )
        assert mainbot.get_all_channels_from_db() == [channel_mock.id]

    async def test_remove_channel_cmd(self, mock_interaction, temp_workspace, mock_discord_client):
        channel_id_to_remove = 987654321
        mainbot.add_channel_to_db(channel_id_to_remove)
        channel_mock = MagicMock(spec=discord.TextChannel)
        channel_mock.id = channel_id_to_remove
        channel_mock.mention = "<#987654321>"
        await mainbot.remove_channel_cmd.callback(mainbot.tree, mock_interaction, channel=channel_mock)
        mock_interaction.response.send_message.assert_called_once_with(
            f"Channel {channel_mock.mention} will no longer receive notifications.", ephemeral=True
        )
        assert mainbot.get_all_channels_from_db() == []

    async def test_list_channels_cmd(self, mock_interaction, temp_workspace, mock_discord_client):
        mainbot.add_channel_to_db(111)
        mainbot.add_channel_to_db(222)
        def side_effect_get_channel(ch_id):
            if ch_id == 111: m = MagicMock(spec=discord.TextChannel); m.id=111; m.mention="<#111>"; return m
            if ch_id == 222: m = MagicMock(spec=discord.TextChannel); m.id=222; m.mention="<#222>"; return m
            return None
        mock_discord_client.get_channel.side_effect = side_effect_get_channel
        with patch('mainbot.client', mock_discord_client):
             await mainbot.list_channels_cmd.callback(mainbot.tree, mock_interaction)
        expected_message = "Channels receiving notifications:\n- <#111> (`111`)\n- <#222> (`222`)"
        mock_interaction.response.send_message.assert_called_once_with(expected_message, ephemeral=True)

    async def test_set_ping_role_cmd(self, mock_interaction, temp_workspace, mock_discord_client):
        role_mock = MagicMock(spec=discord.Role)
        role_mock.id = 777
        role_mock.mention = "<@&777>"
        await mainbot.set_ping_role_cmd.callback(mainbot.tree, mock_interaction, role=role_mock)
        mock_interaction.response.send_message.assert_called_once_with(
            f"Ping role set to {role_mock.mention}.", ephemeral=True
        )
        assert mainbot.get_ping_role_from_db() == 777
        mock_interaction.reset_mock() # Reset for next call
        await mainbot.set_ping_role_cmd.callback(mainbot.tree, mock_interaction, role=None)
        mock_interaction.response.send_message.assert_called_with(
            "Ping role cleared.", ephemeral=True
        )
        assert mainbot.get_ping_role_from_db() is None

    async def test_get_ping_role_cmd(self, mock_interaction, temp_workspace, mock_discord_client):
        await mainbot.get_ping_role_cmd.callback(mainbot.tree, mock_interaction)
        mock_interaction.response.send_message.assert_called_once_with(
            "No ping role is currently configured.", ephemeral=True
        )
        mock_interaction.reset_mock()
        role_id = 888
        mainbot.set_ping_role_in_db(role_id)
        role_mock = MagicMock(spec=discord.Role); role_mock.id = role_id; role_mock.mention = "<@&888>"
        mock_interaction.guild.get_role.return_value = role_mock
        await mainbot.get_ping_role_cmd.callback(mainbot.tree, mock_interaction)
        mock_interaction.response.send_message.assert_called_with(
            f"Current ping role: {role_mock.mention} (`{role_id}`)", ephemeral=True
        )

class TestScheduledTasksAndLifecycle:
    @pytest.mark.asyncio
    async def test_on_ready(self, mock_discord_client, temp_workspace):
        with patch('mainbot.init_db') as mock_init_db, \
             patch('mainbot.tree.sync', new_callable=AsyncMock) as mock_tree_sync, \
             patch.object(mock_discord_client.loop, 'create_task') as mock_create_task:
            mock_discord_client.user = MagicMock(spec=discord.ClientUser); mock_discord_client.user.name = "TestBot"; mock_discord_client.user.id = "12345"
            await mainbot.on_ready()
            mock_init_db.assert_called_once()
            mock_tree_sync.assert_called_once()
            assert any(mainbot.background_scheduler.__name__ in str(call_args[0][0]) for call_args in mock_create_task.call_args_list)

    def test_scheduled_task_wrapper(self, temp_workspace):
        with patch('mainbot.check_for_updates') as mock_check:
            mainbot.scheduled_task_wrapper("url", "local_path", "json_path", "prev_data_file", False)
            mock_check.assert_called_once_with("url", "local_path", "json_path", "prev_data_file", is_second_repo=False)

    @pytest.mark.asyncio
    async def test_background_scheduler_runs_pending(self, mock_discord_client):
         with patch('schedule.run_pending') as mock_run_pending, \
              patch('schedule.every') as mock_schedule_every:
            mock_schedule_every.return_value.minutes.return_value.do.return_value = None
            scheduler_task = asyncio.create_task(mainbot.background_scheduler())
            await asyncio.sleep(0.02)
            scheduler_task.cancel()
            try: await scheduler_task
            except asyncio.CancelledError: pass
            assert mock_run_pending.call_count > 0
            assert mock_schedule_every.call_count == 2

if not tracemalloc.is_tracing():
    tracemalloc.start()