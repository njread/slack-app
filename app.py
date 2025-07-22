import os
import json
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
import slack_sdk
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest
from threading import Thread, Timer
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def validate_config():
    """Validate required configuration"""
    required_vars = {
        'SLACK_APP_TOKEN': 'xapp-',
        'SLACK_BOT_TOKEN': 'xoxb-',
        'BOX_API_URL': None,
        'BOX_DEV_TOKEN': None,
        'BOX_HUB_ID': None
    }
    
    missing_vars = []
    invalid_vars = []
    
    for var, prefix in required_vars.items():
        value = os.environ.get(var)
        if not value:
            missing_vars.append(var)
        elif prefix and not value.startswith(prefix):
            invalid_vars.append(f"{var} (should start with {prefix})")
        else:
            logger.debug(f"✓ {var} is properly configured")
    
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
    if invalid_vars:
        raise ValueError(f"Invalid token format for: {', '.join(invalid_vars)}")

app = Flask(__name__)

# Configuration
BOX_API_URL = os.environ.get("BOX_API_URL")
BOX_DEV_TOKEN = os.environ.get("BOX_DEV_TOKEN")
BOX_HUB_ID = os.environ.get("BOX_HUB_ID")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")

# Initialize Slack clients
web_client = WebClient(token=SLACK_BOT_TOKEN)
socket_mode_client = SocketModeClient(
    app_token=SLACK_APP_TOKEN,
    web_client=web_client
)

def get_last_week_timestamp():
    """Get timestamp for one week ago"""
    one_week_ago = datetime.now() - timedelta(days=7)
    return one_week_ago.timestamp()

def fetch_messages_from_last_week(channel_id, limit=100):
    """Fetch messages from the last week for a specific channel"""
    try:
        oldest_timestamp = get_last_week_timestamp()
        
        logger.info(f"Fetching messages from channel {channel_id} since {datetime.fromtimestamp(oldest_timestamp)}")
        
        messages = []
        cursor = None
        
        while True:
            # Fetch conversation history
            response = web_client.conversations_history(
                channel=channel_id,
                oldest=str(oldest_timestamp),
                limit=limit,
                cursor=cursor
            )
            
            batch_messages = response.get("messages", [])
            messages.extend(batch_messages)
            
            logger.debug(f"Fetched {len(batch_messages)} messages in this batch")
            
            # Check if there are more messages
            if not response.get("has_more", False):
                break
                
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        
        # Filter out bot messages and messages with subtypes (like file uploads, etc.)
        filtered_messages = [
            msg for msg in messages 
            if not msg.get("bot_id") and not msg.get("subtype") and msg.get("text")
        ]
        
        logger.info(f"Found {len(filtered_messages)} user messages from the last week")
        return filtered_messages
        
    except Exception as e:
        logger.error(f"Error fetching messages from last week: {str(e)}")
        return []

def get_all_channels():
    """Get list of all channels the bot has access to"""
    try:
        channels = []
        cursor = None
        
        while True:
            response = web_client.conversations_list(
                types="public_channel,private_channel",
                cursor=cursor,
                limit=100
            )
            
            batch_channels = response.get("channels", [])
            # Filter for channels the bot is a member of
            member_channels = [ch for ch in batch_channels if ch.get("is_member", False)]
            channels.extend(member_channels)
            
            if not response.get("has_more", False):
                break
                
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        
        logger.info(f"Found {len(channels)} channels where bot is a member")
        return channels
        
    except Exception as e:
        logger.error(f"Error fetching channels: {str(e)}")
        return []

def process_weekly_messages():
    """Process all messages from the last week across all channels"""
    try:
        logger.info("Starting weekly message processing...")
        
        channels = get_all_channels()
        all_messages = []
        
        for channel in channels:
            channel_id = channel["id"]
            channel_name = channel["name"]
            
            logger.info(f"Processing channel: #{channel_name} ({channel_id})")
            messages = fetch_messages_from_last_week(channel_id)
            
            # Add channel context to messages
            for msg in messages:
                msg["channel_name"] = channel_name
                msg["channel_id"] = channel_id
            
            all_messages.extend(messages)
        
        logger.info(f"Total messages collected from last week: {len(all_messages)}")
        
        # Process messages with Box AI (you can customize this logic)
        if all_messages:
            process_messages_with_box_ai(all_messages)
        
        return all_messages
        
    except Exception as e:
        logger.error(f"Error in weekly message processing: {str(e)}")
        return []

def process_messages_with_box_ai(messages):
    """Process messages with Box AI - customize this based on your needs"""
    try:
        # Example: Create a summary of all messages
        message_texts = [f"From #{msg['channel_name']}: {msg['text']}" for msg in messages]
        combined_text = "\n".join(message_texts[:50])  # Limit to first 50 messages
        
        prompt = f"Please summarize the key topics and themes from these Slack messages from the past week:\n\n{combined_text}"
        
        logger.info("Sending weekly summary request to Box AI...")
        summary = query_box_ai(prompt)
        
        # You could post this summary to a specific channel
        # web_client.chat_postMessage(
        #     channel="C1234567890",  # Replace with your summary channel ID
        #     text=f"📊 Weekly Activity Summary:\n{summary}"
        # )
        
        logger.info("Weekly summary generated successfully")
        return summary
        
    except Exception as e:
        logger.error(f"Error processing messages with Box AI: {str(e)}")
        return None

def query_box_ai(prompt):
    """Query Box AI with the given prompt"""
    logger.debug(f"Querying Box AI with prompt: {prompt}")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BOX_DEV_TOKEN}",
    }
    
    payload = {
        "mode": "multiple_item_qa", 
        "items": [
            {
                "type": "hubs",
                "id": BOX_HUB_ID
            }
        ],
        "prompt": prompt,
        "includes_citations": "True"
    }
    
    response = requests.post(BOX_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    return response.json().get("answer")

def check_thread_replies(client, channel, thread_ts):
    """Check if thread has any replies"""
    try:
        response = client.web_client.conversations_replies(
            channel=channel,
            ts=thread_ts
        )
        return len(response["messages"]) > 1
    except Exception as e:
        logger.error(f"Error checking thread replies: {str(e)}")
        return False

def delayed_box_response(client, channel, text, thread_ts):
    """Function to execute after delay"""
    try:
        if check_thread_replies(client, channel, thread_ts):
            logger.info(f"Thread {thread_ts} already has replies, skipping Box AI response")
            return

        answer = query_box_ai(text)
        
        client.web_client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"Here's what I found:\n{answer}"
        )
    except Exception as e:
        logger.error(f"Error in delayed response: {str(e)}")

def process_slack_event(client: SocketModeClient, req: SocketModeRequest):
    """Process incoming Slack events"""
    try:
        logger.debug(f"Received event type: {req.type}")
        
        ack = SocketModeResponse(envelope_id=req.envelope_id)
        client.send_socket_mode_response(ack)
        
        if req.type == "events_api":
            event = req.payload.get("event", {})
            event_type = event.get("type")
            
            if event_type == "message":
                if event.get("bot_id") or event.get("subtype") or event.get("thread_ts"):
                    return
                
                text = event.get("text", "")
                channel = event.get("channel")
                message_ts = event.get("ts")
                
                try:
                    client.web_client.reactions_add(
                        channel=channel,
                        timestamp=message_ts,
                        name="timer_clock"
                    )
                except Exception as e:
                    logger.error(f"Error adding reaction: {str(e)}")
                
                timer = Timer(1.0, delayed_box_response, args=[client, channel, text, message_ts])
                timer.start()
                
        elif req.type == "slash_commands":
            command = req.payload.get("command")
            if command == "/askboxhub":
                handle_slash_command(client, req)
            elif command == "/weekly_summary":
                handle_weekly_summary_command(client, req)
            
    except Exception as e:
        logger.error(f"Error processing event: {str(e)}", exc_info=True)

def handle_slash_command(client, req):
    """Handle the /askboxhub slash command"""
    try:
        prompt = req.payload.get("text", "").strip()
        channel_id = req.payload.get("channel_id")
        user_id = req.payload.get("user_id")
        
        if not prompt:
            client.web_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Please provide a prompt with your command."
            )
            return

        answer = query_box_ai(prompt)
        
        client.web_client.chat_postMessage(
            channel=channel_id,
            text=f"*Question:* {prompt}\n*Answer:* {answer}"
        )
        
    except Exception as e:
        logger.error(f"Error processing slash command: {str(e)}", exc_info=True)

def handle_weekly_summary_command(client, req):
    """Handle the /weekly_summary slash command"""
    try:
        channel_id = req.payload.get("channel_id")
        user_id = req.payload.get("user_id")
        
        # Send immediate response
        client.web_client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="⏳ Generating weekly summary... This may take a moment."
        )
        
        # Process weekly messages in background
        def process_in_background():
            try:
                summary = process_weekly_messages()
                if summary:
                    client.web_client.chat_postMessage(
                        channel=channel_id,
                        text=f"📊 *Weekly Activity Summary*\n{summary}"
                    )
                else:
                    client.web_client.chat_postMessage(
                        channel=channel_id,
                        text="❌ Unable to generate weekly summary. Please check the logs."
                    )
            except Exception as e:
                logger.error(f"Error in background processing: {str(e)}")
        
        # Start background processing
        thread = Thread(target=process_in_background)
        thread.daemon = True
        thread.start()
        
    except Exception as e:
        logger.error(f"Error processing weekly summary command: {str(e)}", exc_info=True)

# Register the event handler
socket_mode_client.socket_mode_request_listeners.append(process_slack_event)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/weekly_summary', methods=['POST'])
def weekly_summary_endpoint():
    """HTTP endpoint to trigger weekly summary"""
    try:
        messages = process_weekly_messages()
        return jsonify({
            "status": "success",
            "message_count": len(messages),
            "summary": "Weekly processing completed"
        })
    except Exception as e:
        logger.error(f"Error in weekly summary endpoint: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

def run_flask():
    port = int(os.environ.get("PORT", 3000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    try:
        logger.info("Starting Box AI Assistant...")
        
        validate_config()
        logger.debug("Configuration validated successfully")
        
        auth_test = web_client.auth_test()
        logger.debug(f"Connected to Slack as: {auth_test['bot_id']}")
        
        flask_thread = Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()
        
        logger.info("Connecting to Slack...")
        socket_mode_client.connect()
        
        while True:
            time.sleep(1)
            
    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
        exit(1)
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Error in main thread: {str(e)}", exc_info=True)
    finally:
        logger.info("Application stopped")