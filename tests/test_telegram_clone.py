"""Unit tests for Telegram video cloning with interactive poster & review."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, Video, Document, PhotoSize, Chat, User
from app.bot.handlers import (
    handle_video_message,
    handle_photo_message,
    skip_command,
    cancel_command,
    handle_message,
    _start_telegram_clone_flow,
)


@pytest.fixture
def dummy_update():
    update = MagicMock(spec=Update)
    chat = MagicMock(spec=Chat)
    chat.id = 123456
    user = MagicMock(spec=User)
    user.id = 999
    msg = MagicMock(spec=Message)
    msg.chat_id = 123456
    msg.message_id = 42
    msg.reply_text = AsyncMock()
    msg.effective_chat = chat
    msg.effective_user = user
    update.effective_chat = chat
    update.effective_user = user
    update.effective_message = msg
    update.message = msg
    return update


@pytest.fixture
def dummy_context():
    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "pipeline": MagicMock(has_active_job=MagicMock(return_value=False)),
        "config": MagicMock(telegram_channel_id="-1004385468716"),
        "scraper": MagicMock(),
    }
    context.bot = MagicMock()
    context.bot.send_photo = AsyncMock()
    context.bot.send_message = AsyncMock()
    context.bot.copy_message = AsyncMock(return_value=MagicMock(message_id=777))
    return context


@pytest.mark.asyncio
async def test_handle_video_message_starts_clone_flow(dummy_update, dummy_context):
    video = MagicMock(spec=Video)
    video.file_size = 104857600
    video.file_name = "Inception.2010.1080p.mkv"
    dummy_update.message.video = video
    dummy_update.message.document = None
    dummy_update.message.caption = None

    await handle_video_message(dummy_update, dummy_context)

    assert dummy_context.user_data.get("tg_clone_state") == "AWAITING_POSTER"
    clone_data = dummy_context.user_data.get("tg_clone_data", {})
    assert clone_data["title"] == "Inception 2010 1080p"
    assert clone_data["file_size"] == 104857600
    assert dummy_update.message.reply_text.called
    call_args = dummy_update.message.reply_text.call_args[0][0]
    assert "Step 1 of 2: Poster Image" in call_args


@pytest.mark.asyncio
async def test_handle_video_document_starts_clone_flow(dummy_update, dummy_context):
    doc = MagicMock(spec=Document)
    doc.file_size = 52428800
    doc.file_name = "Avatar.2.mp4"
    doc.mime_type = "video/mp4"
    dummy_update.message.video = None
    dummy_update.message.document = doc
    dummy_update.message.caption = "Awesome movie"

    await handle_video_message(dummy_update, dummy_context)

    assert dummy_context.user_data.get("tg_clone_state") == "AWAITING_POSTER"
    clone_data = dummy_context.user_data.get("tg_clone_data", {})
    assert "Avatar 2" in clone_data["title"]


@pytest.mark.asyncio
async def test_non_video_document_ignored(dummy_update, dummy_context):
    doc = MagicMock(spec=Document)
    doc.file_size = 1024
    doc.file_name = "notes.txt"
    doc.mime_type = "text/plain"
    dummy_update.message.video = None
    dummy_update.message.document = doc
    dummy_update.message.caption = None

    await handle_video_message(dummy_update, dummy_context)

    assert dummy_context.user_data.get("tg_clone_state") is None
    assert not dummy_update.message.reply_text.called


@pytest.mark.asyncio
async def test_handle_photo_message_advances_to_review(dummy_update, dummy_context):
    dummy_context.user_data["tg_clone_state"] = "AWAITING_POSTER"
    dummy_context.user_data["tg_clone_data"] = {
        "clone_id": "test1234",
        "title": "Gladiator",
    }

    photo = MagicMock(spec=PhotoSize)
    photo.file_id = "photo_file_abc"
    dummy_update.message.photo = [photo]
    dummy_update.message.document = None
    dummy_update.message.caption = None

    mock_file = MagicMock()
    mock_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"image_content_xyz"))
    photo.get_file = AsyncMock(return_value=mock_file)

    with patch("app.store.database.save_poster", new=AsyncMock()) as mock_save:
        await handle_photo_message(dummy_update, dummy_context)
        assert mock_save.called
        assert mock_save.call_args[0][0] == "test1234"
        assert mock_save.call_args[0][1] == b"image_content_xyz"

    assert dummy_context.user_data.get("tg_clone_state") == "AWAITING_REVIEW"
    call_args = dummy_update.message.reply_text.call_args[0][0]
    assert "Step 2 of 2: Review & Storyline" in call_args


@pytest.mark.asyncio
async def test_skip_command_flow(dummy_update, dummy_context):
    # Step 1: Skip poster
    dummy_context.user_data["tg_clone_state"] = "AWAITING_POSTER"
    dummy_context.user_data["tg_clone_data"] = {
        "clone_id": "skip123",
        "title": "Movie Without Poster",
        "video_msg_id": 101,
        "from_chat_id": 123456,
    }

    await skip_command(dummy_update, dummy_context)
    assert dummy_context.user_data.get("tg_clone_state") == "AWAITING_REVIEW"

    # Step 2: Skip review and execute clone
    with patch("app.bot.handlers._execute_telegram_clone", new=AsyncMock()) as mock_exec:
        await skip_command(dummy_update, dummy_context)
        assert mock_exec.called


@pytest.mark.asyncio
async def test_cancel_command_clears_state(dummy_update, dummy_context):
    dummy_context.user_data["tg_clone_state"] = "AWAITING_POSTER"
    dummy_context.user_data["tg_clone_data"] = {"title": "Cancelled Movie"}

    await cancel_command(dummy_update, dummy_context)

    assert dummy_context.user_data.get("tg_clone_state") is None
    assert dummy_context.user_data.get("tg_clone_data") is None
    assert "cancelled" in dummy_update.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_handle_message_receives_review_and_executes(dummy_update, dummy_context):
    dummy_context.user_data["tg_clone_state"] = "AWAITING_REVIEW"
    dummy_context.user_data["tg_clone_data"] = {
        "clone_id": "rev123",
        "title": "Test Movie",
        "video_msg_id": 202,
        "from_chat_id": 123456,
    }
    dummy_update.message.text = "ဒီကားကတော့ အရမ်းကောင်းတဲ့ စစ်ကားဖြစ်ပါတယ်..."

    with patch("app.bot.handlers._execute_telegram_clone", new=AsyncMock()) as mock_exec:
        await handle_message(dummy_update, dummy_context)
        assert mock_exec.called
        assert dummy_context.user_data["tg_clone_data"]["review_text"] == "ဒီကားကတော့ အရမ်းကောင်းတဲ့ စစ်ကားဖြစ်ပါတယ်..."
