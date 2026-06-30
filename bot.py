import os
import io
import discord
from discord import app_commands
from discord.ext import commands
import re
import json
from datetime import timedelta, datetime
import firebase_admin
from firebase_admin import credentials, firestore
import aiohttp
import subprocess
from dotenv import load_dotenv
from lupa import LuaRuntime, LuaError
import asyncio
from collections import Counter
import base64
from urllib.parse import urlparse
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageOps
import requests
from io import BytesIO
import textwrap

load_dotenv()

cred = credentials.Certificate({
    "type": "service_account",
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace("\\n", "\n") if os.getenv("FIREBASE_PRIVATE_KEY") else None,
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "token_uri": "https://oauth2.googleapis.com/token"
})
firebase_admin.initialize_app(cred)
db = firestore.client()

class BotClient(discord.Client):
    def __init__(self):
        bot_intents = discord.Intents.default()
        bot_intents.message_content = True
        super().__init__(intents=bot_intents)
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        await self.tree.sync()
        print(f"Logged in as {self.user} | Synced commands!")

    async def on_disconnect(self):
        print("[DISCONNECT] Bot disconnected from Discord!")

    async def on_resumed(self):
        print("[RECONNECT] Bot reconnected successfully!")

    async def on_error(self, event, *args, **kwargs):
        print(f"[ERROR] An error occurred in event {event}")
        import traceback
        traceback.print_exc()

    async def on_message(self, message):
        if message.author.bot:
            return

        if message.content.strip() == "!server-list":
            app_info = await self.application_info()
            if message.author.id != app_info.owner.id:
                return
            try:
                await message.delete()
            except discord.Forbidden:
                pass

            total_servers = len(self.guilds)
            output = io.StringIO()
            output.write(f"=========================================\n")
            output.write(f"TOTAL SERVERS: {total_servers}\n")
            output.write(f"=========================================\n\n")

            for index, guild in enumerate(self.guilds, start=1):
                invite_link = "No Permission to Create Invite"
                try:
                    if guild.text_channels:
                        for channel in guild.text_channels:
                            perms = channel.permissions_for(guild.me)
                            if perms.create_instant_invite:
                                invite = await channel.create_invite(max_age=0, max_uses=0)
                                invite_link = invite.url
                                break
                except Exception:
                    pass

                icon_url = guild.icon.url if guild.icon else "No Icon"
                output.write(f"[{index}] SERVER PROFILE\n")
                output.write(f" Name: {guild.name}\n")
                output.write(f" ID: {guild.id}\n")
                output.write(f" Members: {guild.member_count}\n")
                output.write(f" Icon: {icon_url}\n")
                output.write(f" Link: {invite_link}\n")
                output.write(f"{'-'*40}\n")

            output.seek(0)
            file_to_send = discord.File(fp=io.BytesIO(output.getvalue().encode('utf-8')), filename="server_list.txt")
            try:
                await message.author.send(content="SigmaBot | Server Guilds", file=file_to_send)
            except discord.Forbidden:
                print("[WARNING] Cannot send to owner.")

        doc_ref = db.collection("sticky").document(str(message.channel.id))
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            if data.get("enabled", True):
                message_count = data.get("message_count", 0) + 1
                duration = data.get("duration", 5)
                sticky_message = data.get("message", "")
                last_message_id = data.get("last_message_id", None)

                if message_count >= duration:
                    if last_message_id:
                        try:
                            old_msg = await message.channel.fetch_message(int(last_message_id))
                            await old_msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass
                    new_msg = await message.channel.send(f"**Stickied Message:**\n\n{sticky_message}")
                    doc_ref.update({
                        "last_message_id": str(new_msg.id),
                        "message_count": 0
                    })
                else:
                    doc_ref.update({"message_count": message_count})

        trigger_ref = db.collection("triggers").document(str(message.channel.id))
        trigger_doc = trigger_ref.get()
        if trigger_doc.exists:
            trigger_data = trigger_doc.to_dict()
            stored_trigger = trigger_data.get("trigger", "")
            stored_response = trigger_data.get("response", "")
            if message.content.strip().lower() == stored_trigger.strip().lower():
                await message.channel.send(stored_response)

client = BotClient()

class UserSelectView(discord.ui.View):
    def __init__(self, role: discord.Role):
        super().__init__(timeout=180)
        self.role = role

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Select members to assign this role...", min_values=1, max_values=25)
    async def select_users(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        await interaction.response.defer()
        for member in select.values:
            if isinstance(member, discord.Member):
                try:
                    await member.add_roles(self.role)
                except discord.Forbidden:
                    await interaction.followup.send("I dont have permission to role this user.", ephemeral=True)
                    return
        await interaction.edit_original_response(content="Successfully granted selected users!", view=None)

class CreateRoleView(discord.ui.View):
    def __init__(self, role: discord.Role):
        super().__init__(timeout=180)
        self.role = role

    @discord.ui.button(label="users", style=discord.ButtonStyle.primary)
    async def users_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Select the users you want to assign the role to:", view=UserSelectView(self.role))

class CreateTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket_button")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Creating the ticket channel...", ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        display_name = member.display_name

        if display_name.endswith("s") or display_name.endswith("S"):
            channel_name = f"{display_name}' ticket"
        else:
            channel_name = f"{display_name}'s ticket"

        mod_role = None
        for role in guild.roles:
            if role.permissions.kick_members or role.permissions.moderate_members:
                mod_role = role
                break

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
                send_messages=False,
                read_message_history=False
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True
            )
        }

        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        target_category = None
        doc_ref = db.collection("ticket_panels").document(str(guild.id))
        doc = doc_ref.get()
        if doc.exists:
            category_id = doc.to_dict().get("category_id")
            if category_id:
                target_category = guild.get_channel(int(category_id))

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=target_category
            )
            ticket_embed = discord.Embed(
                description=f"{member.mention} has created a new ticket.",
                color=0xFFE135
            )
            ticket_embed.set_footer(text="Powered by SigmaBot")
            close_view = CloseTicketView(member.id)
            await ticket_channel.send(embed=ticket_embed, view=close_view)
            await interaction.edit_original_response(content=f"Ticket channel was created successfully! {ticket_channel.mention}")
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to create channels!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)

class CloseTicketView(discord.ui.View):
    def __init__(self, creator_id: int = None):
        super().__init__(timeout=None)
        self.creator_id = creator_id

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild
        is_mod = member.guild_permissions.kick_members or member.guild_permissions.moderate_members or member.guild_permissions.administrator

        creator_id = self.creator_id
        if creator_id is None:
            for overwrite_target, overwrite in interaction.channel.overwrites.items():
                if isinstance(overwrite_target, discord.Member) and overwrite_target.id != guild.me.id:
                    if overwrite.send_messages is True:
                        creator_id = overwrite_target.id
                        break

        if member.id != creator_id and not is_mod:
            await interaction.response.send_message("You don't have permission to close this ticket!", ephemeral=True)
            return

        await interaction.response.send_message("Deleting ticket...")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except discord.Forbidden:
            pass
        except Exception:
            pass

class EditTicketPanelModal(discord.ui.Modal, title="Edit Ticket Panel"):
    panel_name = discord.ui.TextInput(
        label="Name",
        placeholder="Edit the ticket panel name.",
        style=discord.TextStyle.short,
        required=True
    )
    panel_desc = discord.ui.TextInput(
        label="Description",
        placeholder="Edit the ticket panel description.",
        style=discord.TextStyle.short,
        required=True
    )

    def __init__(self, channel_id: int, message_id: int):
        super().__init__()
        self.channel_id = channel_id
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        channel = guild.get_channel(self.channel_id)
        if not channel:
            await interaction.followup.send("Could not find the ticket panel channel!", ephemeral=True)
            return

        try:
            old_message = await channel.fetch_message(self.message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        new_embed = discord.Embed(
            title=self.panel_name.value,
            description=self.panel_desc.value,
            color=0xFFE135
        )
        if guild.icon:
            new_embed.set_thumbnail(url=guild.icon.url)
        new_embed.set_footer(text="Powered by SigmaBot")

        new_message = await channel.send(embed=new_embed, view=CreateTicketView())
        doc_ref = db.collection("ticket_panels").document(str(guild.id))
        doc_ref.set({
            "channel_id": str(channel.id),
            "message_id": str(new_message.id),
            "name": self.panel_name.value,
            "desc": self.panel_desc.value
        })
        await interaction.followup.send("Ticket panel updated successfully!", ephemeral=True)

@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[ERROR] Error occurred: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("An error occurred while running this command!", ephemeral=True)
        else:
            await interaction.response.send_message("An error occurred while running this command!", ephemeral=True)
    except Exception:
        pass

@client.tree.command(name="kick", description="kick a user")
@app_commands.describe(user="Select a members to kick.", reason="Reason to kick (Optional).")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    if reason is None:
        reason = "No reason provided"
    try:
        dm_message = f"## You have been kicked from {interaction.guild.name}!\n\n**Reason: {reason}**\n**Kicked by: {interaction.user.name}**"
        await user.send(dm_message)
    except discord.Forbidden:
        pass
    try:
        await user.kick(reason=f"Kicked by {interaction.user}: {reason}")
        await interaction.response.send_message(f"Successfully kicked {user.mention}. Reason: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("I do not have permissions to kick this user!", ephemeral=True)

@kick.error
async def kick_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

def parse_duration_to_seconds(duration_str: str) -> int:
    if not duration_str:
        return 0
    match = re.match(r"^(\d+)([smhd])$", duration_str.strip().lower())
    if not match:
        return 0
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == 's':
        return amount
    elif unit == 'm':
        return amount * 60
    elif unit == 'h':
        return amount * 3600
    elif unit == 'd':
        return amount * 86400
    return 0

@client.tree.command(name="ban", description="Ban a user.")
@app_commands.describe(user="User to ban.", reason="Reason to ban (Optional).", duration="Duration to hide message activities for this user (Optional).")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = None, duration: str = None):
    if reason is None:
        reason = "No reason provided."
    display_duration = duration if duration else "No time provided"
    delete_seconds = parse_duration_to_seconds(duration) if duration else 0
    try:
        dm_message = f"## You have been banned from {interaction.guild.name}\n\n**Reason: {reason}**\n**Banned by {interaction.user.name}**\n**Duration: {display_duration}**"
        await user.send(dm_message)
    except discord.Forbidden:
        pass
    try:
        await user.ban(delete_message_seconds=delete_seconds, reason=f"Banned by {interaction.user}: {reason}")
        await interaction.response.send_message(f"Successfully banned {user.mention}. Reason: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("I do not have permission to ban this user!", ephemeral=True)

@ban.error
async def ban_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="say", description="Let bot send the message of what you say!")
@app_commands.describe(message="The message you want the bot to repeat.")
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("Message sent!", ephemeral=True)
    await interaction.channel.send(message)

@client.tree.command(name="purge", description="Clear messages in this channel.")
@app_commands.describe(
    number_of_messages="Number of messages that you want to delete.",
    filter_by_user="Delete messages sends by the user (Optional).",
    filter_by_role="Delete messages by the user with the selected role (Optional).",
    filter_by_bots="Delete messages sends by the bots (Optional)."
)
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(
    interaction: discord.Interaction,
    number_of_messages: int,
    filter_by_user: discord.Member = None,
    filter_by_role: discord.Role = None,
    filter_by_bots: bool = False
):
    await interaction.response.defer(ephemeral=True)

    def check(msg):
        if filter_by_user and msg.author != filter_by_user:
            return False
        if filter_by_role and filter_by_role not in msg.author.roles:
            return False
        if filter_by_bots and not msg.author.bot:
            return False
        return True

    try:
        deleted = await interaction.channel.purge(limit=number_of_messages, check=check)
        await interaction.followup.send(f"Successfully deleted **{len(deleted)}** message(s).", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("I do not have permission to purge messages!", ephemeral=True)

@purge.error
async def purge_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="timeout", description="Timeout a user.")
@app_commands.describe(
    user="User to timeout.",
    reason="Reason for timeout (Optional).",
    duration="Time/Duration for timeout, e.g 1d, 24h, 67m, 100s (Optional)."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = None,
    duration: str = None
):
    await interaction.response.defer(ephemeral=True)
    display_reason = reason if reason else "No reason provided."
    display_duration = duration if duration else "No time provided."
    timeout_seconds = parse_duration_to_seconds(duration) if duration else 0

    try:
        dm_embed = discord.Embed(
            description=(
                f"**Reason:** {display_reason}\n"
                f"**Duration:** {display_duration}\n"
                f"**Timed out by:** {interaction.user.name} > {interaction.user.display_name}"
            ),
            color=0xFF6B00
        )
        dm_embed.title = f"You were timed out in {interaction.guild.name}"
        if interaction.guild.icon:
            dm_embed.set_thumbnail(url=interaction.guild.icon.url)

        if timeout_seconds > 0:
            await user.timeout(timedelta(seconds=timeout_seconds), reason=f"Timed out by {interaction.user}: {display_reason}")
        else:
            await user.timeout(timedelta(minutes=5), reason=f"Timed out by {interaction.user}: {display_reason}")

        try:
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"Successfully timed out {user.mention}. Reason: {display_reason} | Duration: {display_duration}",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I do not have permission to timeout this user! Check my role and ensure my role is higher than this role!",
            ephemeral=True
        )

@timeout.error
async def timeout_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="warn", description="Warn a user.")
@app_commands.describe(user="User to warning.", reason="Reason for warning (Optional).")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    await interaction.response.defer(ephemeral=True)
    display_reason = reason if reason else "No reason provided!"
    try:
        dm_embed = discord.Embed(
            description=(
                f"**Reason:** {display_reason}\n"
                f"**Warned by:** {interaction.user.name} > {interaction.user.display_name}"
            ),
            color=0xFFCC00
        )
        dm_embed.title = f"You were warned in {interaction.guild.name}!"
        if interaction.guild.icon:
            dm_embed.set_thumbnail(url=interaction.guild.icon.url)

        try:
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        await interaction.followup.send(f"Successfully warned {user.mention}. Reason: {display_reason}", ephemeral=True)
    except Exception:
        await interaction.followup.send(
            "I do not have permission to warn this user! Check my role and ensure my role is higher than this role!",
            ephemeral=True
        )

@warn.error
async def warn_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="ping", description="Show bot latency.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(client.latency * 1000)
    guild_count = len(client.guilds)
    node = "Railway-US-West"
    await interaction.response.send_message(
        f"PONG!\n"
        f"**Cluster 436:** {latency_ms}ms\n"
        f"**Shard 6984:** {latency_ms}ms\n"
        f"**Guild:** {guild_count}\n"
        f"**Node:** {node}"
    )

@client.tree.command(name="beautify-image", description="Enhance an uploaded image using Stability AI.")
@app_commands.describe(file="Attach an image file to beautify.")
async def beautify_image(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=False)
    if not file.filename.lower().endswith((".png", ".jpg", ".jpeg")):
        await interaction.followup.send("Only image files (.png, .jpg, .jpeg) are allowed!")
        return

    img_bytes = await file.read()
    async with aiohttp.ClientSession() as session:
        form_data = aiohttp.FormData()
        form_data.add_field("image", img_bytes, filename=file.filename, content_type=file.content_type)
        form_data.add_field("output_format", "png")
        async with session.post(
            "https://api.stability.ai/v2beta/stable-image/upscale/fast",
            headers={
                "Authorization": f"Bearer {os.getenv('STABILITY_API_KEY')}",
                "Accept": "image/*"
            },
            data=form_data
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                await interaction.followup.send(f"Stability AI error: {resp.status} | {text}")
                return
            output_bytes = await resp.read()
            file_obj = discord.File(io.BytesIO(output_bytes), filename="beautified.png")
            await interaction.followup.send("Your so ugly so i beautified your image:", file=file_obj)

@client.tree.command(name="invite", description="Invite bot to your server!")
async def invite(interaction: discord.Interaction):
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Invite Bot", url="https://discord.com/oauth2/authorize?client_id=1508136617251176579&permissions=9521875909879&integration_type=0&scope=bot+applications.commands"))
    view.add_item(discord.ui.Button(label="Support", url="https://discord.gg/Zame2JAGDr"))
    await interaction.response.send_message("Invite bot to your server now! Click the buttons below!", view=view)

@client.tree.command(name="web-screenshot", description="Let the bot screenshot a website.")
@app_commands.describe(url="Url or website to take an screenshot.")
async def web_screenshot(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=False)
    parsed_url = urlparse(url)
    if not parsed_url.scheme or not parsed_url.netloc:
        await interaction.followup.send("Invalid url, please try again\n\nExample:\n`https://www.SigmaBot.com`")
        return
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.screenshotmachine.com/?key={os.getenv('SCREENSHOT_API_KEY')}&url={url}&dimension=1280x720") as resp:
                if resp.status != 200:
                    await interaction.followup.send("Failed to take an screenshot.")
                    return
                screenshot_bytes = await resp.read()
                file_obj = discord.File(io.BytesIO(screenshot_bytes), filename="screenshot.png")
                await interaction.followup.send(file=file_obj)
    except Exception:
        await interaction.followup.send("Failed to take an screenshot.")

@client.tree.command(name="roblox-profile", description="Get a roblox user profile.")
@app_commands.describe(username="Type the username or userid of user to fetch (NOT DISPLAY NAME).")
async def roblox_profile(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=False)
    
    try:
        async with aiohttp.ClientSession() as session:
            if username.isdigit():
                user_id = username
            else:
                payload = {"usernames": [username], "excludeBannedUsers": False}
                async with session.post("https://users.roblox.com/v1/usernames/users", json=payload) as resp:
                    if resp.status != 200:
                        await interaction.followup.send("An error occurred while fetching the user ID. Please try again later.")
                        return
                    data = await resp.json()
                    if not data.get("data"):
                        await interaction.followup.send("User not found. Please check the username and try again.")
                        return
                    user_id = str(data["data"][0].get("id"))
            
            async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp:
                if resp.status != 200:
                    await interaction.followup.send("User not found. Please check the username and try again.")
                    return
                user_data = await resp.json()
            
            async with session.get(f"https://friends.roblox.com/v1/users/{user_id}/friends/count") as resp:
                if resp.status == 200:
                    friends_data = await resp.json()
                    friends_count = friends_data.get("count", 0)
                else:
                    friends_count = 0
            
            async with session.get(f"https://friends.roblox.com/v1/users/{user_id}/followers/count") as resp:
                if resp.status == 200:
                    followers_data = await resp.json()
                    followers_count = followers_data.get("count", 0)
                else:
                    followers_count = 0
            
            async with session.get(f"https://friends.roblox.com/v1/users/{user_id}/following/count") as resp:
                if resp.status == 200:
                    following_data = await resp.json()
                    following_count = following_data.get("count", 0)
                else:
                    following_count = 0
            
            async with session.get(f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=720x720&format=Png") as resp:
                if resp.status == 200:
                    thumbnail_data = await resp.json()
                    if thumbnail_data.get("data") and len(thumbnail_data["data"]) > 0:
                        avatar_url = thumbnail_data["data"][0].get("imageUrl")
                    else:
                        avatar_url = None
                else:
                    avatar_url = None
            
            display_name = user_data.get("displayName", "N/A")
            username_display = user_data.get("name", "N/A")
            created_date = user_data.get("created", "N/A")
            description = user_data.get("description", "N/A")
            if not description or description == "":
                description = "N/A"
            
            if created_date != "N/A":
                created_datetime = datetime.fromisoformat(created_date.replace('Z', '+00:00'))
                join_date = created_datetime.strftime("%B %d, %Y")
            else:
                join_date = "N/A"
            
            country = user_data.get("country", "N/A")
            if not country or country == "":
                country = "N/A"
            
            profile_image = await create_roblox_profile_image(
                display_name=display_name,
                username=username_display,
                avatar_url=avatar_url,
                description=description,
                friends=friends_count,
                followers=followers_count,
                following=following_count,
                join_date=join_date,
                country=country
            )
            
            with BytesIO() as image_binary:
                profile_image.save(image_binary, 'PNG')
                image_binary.seek(0)
                file = discord.File(fp=image_binary, filename='roblox_profile.png')
                await interaction.followup.send(file=file)
            
    except Exception as e:
        print(f"[ERROR] Roblox profile error: {e}")
        await interaction.followup.send("An error occurred while fetching the profile. Please try again later.")

async def create_roblox_profile_image(display_name, username, avatar_url, description, friends, followers, following, join_date, country):
    width = 800
    height = 600
    background_color = (30, 30, 35)
    
    image = Image.new('RGB', (width, height), background_color)
    draw = ImageDraw.Draw(image)
    
    try:
        font_title = ImageFont.truetype("arialbd.ttf", 36)
        font_username = ImageFont.truetype("arial.ttf", 22)
        font_label = ImageFont.truetype("arialbd.ttf", 18)
        font_value = ImageFont.truetype("arial.ttf", 20)
        font_description = ImageFont.truetype("arial.ttf", 16)
        font_small = ImageFont.truetype("arial.ttf", 14)
    except:
        try:
            font_title = ImageFont.truetype("arial.ttf", 36)
            font_username = ImageFont.truetype("arial.ttf", 22)
            font_label = ImageFont.truetype("arial.ttf", 18)
            font_value = ImageFont.truetype("arial.ttf", 20)
            font_description = ImageFont.truetype("arial.ttf", 16)
            font_small = ImageFont.truetype("arial.ttf", 14)
        except:
            font_title = ImageFont.load_default()
            font_username = ImageFont.load_default()
            font_label = ImageFont.load_default()
            font_value = ImageFont.load_default()
            font_description = ImageFont.load_default()
            font_small = ImageFont.load_default()
    
    draw.rectangle([(0, 0), (width, height)], outline=(80, 80, 90), width=3)
    draw.rectangle([(10, 10), (width-10, height-10)], outline=(60, 60, 70), width=2)
    
    draw.line([(30, 50), (width-30, 50)], fill=(60, 60, 70), width=1)
    draw.line([(30, height-50), (width-30, height-50)], fill=(60, 60, 70), width=1)
    
    draw.rectangle([(20, 20), (35, 35)], fill=(255, 200, 50), outline=None)
    draw.rectangle([(width-35, 20), (width-20, 35)], fill=(255, 200, 50), outline=None)
    draw.rectangle([(20, height-35), (35, height-20)], fill=(255, 200, 50), outline=None)
    draw.rectangle([(width-35, height-35), (width-20, height-20)], fill=(255, 200, 50), outline=None)
    
    if avatar_url:
        try:
            response = requests.get(avatar_url)
            avatar_img = Image.open(BytesIO(response.content))
            avatar_img = avatar_img.resize((150, 150), Image.Resampling.LANCZOS)
            
            mask = Image.new('L', (150, 150), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 150, 150), fill=255)
            
            avatar_img = ImageOps.fit(avatar_img, (150, 150), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            avatar_img.putalpha(mask)
            
            image.paste(avatar_img, (50, 60), avatar_img)
            
            draw.ellipse((48, 58, 202, 212), outline=(255, 200, 50), width=3)
        except:
            draw.ellipse((50, 60, 200, 210), fill=(60, 60, 70), outline=(255, 200, 50), width=3)
            draw.text((110, 110), "?", fill=(150, 150, 150), font=font_title, anchor="mm")
    
    draw.text((230, 70), display_name, fill=(255, 255, 255), font=font_title)
    draw.text((230, 115), f"@{username}", fill=(180, 180, 180), font=font_username)
    
    wrapped_desc = textwrap.wrap(description, width=55)
    y_offset = 170
    for line in wrapped_desc:
        draw.text((50, y_offset), line, fill=(200, 200, 200), font=font_description)
        y_offset += 25
    if not wrapped_desc:
        draw.text((50, 170), "N/A", fill=(150, 150, 150), font=font_description)
        y_offset = 195
    
    y_pos = y_offset + 30
    
    info_items = [
        ("Friends", friends, 50),
        ("Followers", followers, 250),
        ("Following", following, 450)
    ]
    
    for label, value, x_pos in info_items:
        draw.text((x_pos, y_pos), str(value), fill=(255, 255, 255), font=font_value)
        draw.text((x_pos, y_pos + 30), label, fill=(180, 180, 180), font=font_label)
    
    y_pos = y_pos + 80
    
    draw.text((50, y_pos), "Joined:", fill=(180, 180, 180), font=font_label)
    draw.text((140, y_pos), join_date, fill=(255, 255, 255), font=font_value)
    
    y_pos = y_pos + 45
    
    country_emoji = get_country_emoji(country)
    draw.text((50, y_pos), "Country:", fill=(180, 180, 180), font=font_label)
    draw.text((140, y_pos), f"{country_emoji} {country}", fill=(255, 255, 255), font=font_value)
    
    draw.text((width-200, height-35), "SigmaBot", fill=(100, 100, 110), font=font_small)
    
    return image

def get_country_emoji(country):
    country_flags = {
        "United States": "🇺🇸", "US": "🇺🇸", "USA": "🇺🇸",
        "United Kingdom": "🇬🇧", "UK": "🇬🇧", "England": "🇬🇧",
        "Canada": "🇨🇦", "CA": "🇨🇦",
        "Australia": "🇦🇺", "AU": "🇦🇺",
        "Germany": "🇩🇪", "DE": "🇩🇪",
        "France": "🇫🇷", "FR": "🇫🇷",
        "Japan": "🇯🇵", "JP": "🇯🇵",
        "Brazil": "🇧🇷", "BR": "🇧🇷",
        "India": "🇮🇳", "IN": "🇮🇳",
        "China": "🇨🇳", "CN": "🇨🇳",
        "Russia": "🇷🇺", "RU": "🇷🇺",
        "Italy": "🇮🇹", "IT": "🇮🇹",
        "Spain": "🇪🇸", "ES": "🇪🇸",
        "Mexico": "🇲🇽", "MX": "🇲🇽",
        "South Korea": "🇰🇷", "KR": "🇰🇷",
        "Netherlands": "🇳🇱", "NL": "🇳🇱",
        "Sweden": "🇸🇪", "SE": "🇸🇪",
        "Norway": "🇳🇴", "NO": "🇳🇴",
        "Denmark": "🇩🇰", "DK": "🇩🇰",
        "Finland": "🇫🇮", "FI": "🇫🇮",
        "Poland": "🇵🇱", "PL": "🇵🇱",
        "Turkey": "🇹🇷", "TR": "🇹🇷",
        "Philippines": "🇵🇭", "PH": "🇵🇭",
        "Indonesia": "🇮🇩", "ID": "🇮🇩",
        "Malaysia": "🇲🇾", "MY": "🇲🇾",
        "Singapore": "🇸🇬", "SG": "🇸🇬",
        "New Zealand": "🇳🇿", "NZ": "🇳🇿"
    }
    for key in country_flags:
        if key in country or country in key:
            return country_flags[key]
    return "🌍"

def run_lua_with_loop_detection(lua_code: str, max_repeats: int = 500):
    lua = LuaRuntime(unpack_returned_tuples=True)
    output_buffer = []

    def custom_print(*args):
        output_buffer.append(" ".join(str(arg) for arg in args))
    lua.globals().print = custom_print

    try:
        result = lua.execute(lua_code)
    except LuaError as e:
        raise RuntimeError(f"Lua error: {e}")

    if result is not None and not output_buffer:
        output_buffer.append(str(result))

    output_text = "\n".join(output_buffer)
    if not output_text:
        return "Script executed successfully with no output."

    lines = output_text.splitlines()
    if lines:
        counter = Counter(lines)
        most_common = counter.most_common(1)[0]
        if most_common[1] >= max_repeats:
            raise RuntimeError(
                f"Infinite loop detected: line '{most_common[0][:50]}' repeated {most_common[1]} times."
            )
    return output_text

@client.tree.command(name="execute", description="Execute a lua script directly.")
@app_commands.describe(
    file="Attach a .lua or .txt file only!",
    code="Paste lua code by message."
)
async def execute(interaction: discord.Interaction, file: discord.Attachment = None, code: str = None):
    await interaction.response.defer(ephemeral=False)

    if (file and code) or (not file and not code):
        await interaction.followup.send("It seems you chose both options or left both blank. Please select only one to proceed! :)")
        return

    lua_code = ""
    if file:
        if not file.filename.lower().endswith((".lua", ".txt")):
            await interaction.followup.send("Only .lua or .txt files are allowed!")
            return
        try:
            file_bytes = await file.read()
            lua_code = file_bytes.decode("utf-8")
        except Exception as e:
            await interaction.followup.send(f"Error reading file:\n```lua\n{str(e)}\n```")
            return
    else:
        lua_code = code

    try:
        output_text = await asyncio.wait_for(
            asyncio.to_thread(run_lua_with_loop_detection, lua_code),
            timeout=30.0
        )
        if len(output_text) <= 2000:
            formatted = f"Successfully executed! Check the output below:\n```lua\n{output_text}\n```"
            await interaction.followup.send(formatted)
        else:
            file_obj = discord.File(io.BytesIO(output_text.encode("utf-8")), filename="Output.txt")
            await interaction.followup.send("Successfully executed! Check the output below:", file=file_obj)
    except asyncio.TimeoutError:
        await interaction.followup.send(
            "Lua execution timed out after 30 seconds possible infinite loop (e.g., `while true do end`)."
        )
    except RuntimeError as re:
        error_msg = str(re)
        if len(error_msg) <= 2000:
            await interaction.followup.send(f"{error_msg}")
        else:
            file_obj = discord.File(io.BytesIO(error_msg.encode("utf-8")), filename="Error.txt")
            await interaction.followup.send("Error occurred:", file=file_obj)
    except Exception as e:
        error_msg = str(e)
        if len(error_msg) <= 2000:
            await interaction.followup.send(f"Error:\n```lua\n{error_msg}\n```")
        else:
            file_obj = discord.File(io.BytesIO(error_msg.encode("utf-8")), filename="Error.txt")
            await interaction.followup.send("Error occurred:", file=file_obj)

@client.tree.command(name="create-role", description="Create a server role.")
@app_commands.describe(
    name="Role name (required).",
    color="Enter a valid hex color for this role (Optional)",
    permission="Select a permission for this role (Required)"
)
@app_commands.choices(permission=[
    app_commands.Choice(name="Cosmetic", value="Cosmetic"),
    app_commands.Choice(name="Member", value="Member"),
    app_commands.Choice(name="Moderator", value="Moderator"),
    app_commands.Choice(name="Manager", value="Manager")
])
@app_commands.checks.has_permissions(manage_roles=True)
async def create_role(interaction: discord.Interaction, name: str, permission: str, color: str = "#99aab5"):
    await interaction.response.defer(ephemeral=False)
    hex_match = re.match(r"^#?[0-9a-fA-F]{6}$", color.strip())
    if not hex_match:
        await interaction.followup.send("Invalid hex code, try like this example buddy: #99aab5")
        return

    cleaned_hex = color.strip().lstrip('#')
    discord_color = discord.Color(int(cleaned_hex, 16))

    perms = discord.Permissions.none()
    if permission == "Cosmetic":
        perms = discord.Permissions.none()
    elif permission == "Member":
        perms = discord.Permissions(send_messages=True, view_channel=True, read_message_history=True)
    elif permission == "Moderator":
        perms = discord.Permissions(kick_members=True, ban_members=True, moderate_members=True, manage_messages=True, view_channel=True)
    elif permission == "Manager":
        perms = discord.Permissions(administrator=True)

    try:
        role = await interaction.guild.create_role(name=name, color=discord_color, permissions=perms)
        await interaction.followup.send(
            f"Role {role.name} successfully created! Want to role a user quickly? Use the selection below. :)",
            view=CreateRoleView(role)
        )
    except discord.Forbidden:
        await interaction.followup.send("I dont have permission to role this user.")

@create_role.error
async def create_role_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("You dont have permission to use this command lol.", ephemeral=True)
        else:
            await interaction.response.send_message("You dont have permission to use this command lol.", ephemeral=True)

@client.tree.command(name="avatar", description="Get user avatar.")
@app_commands.describe(user="Select a user to get profile picture or avatar.")
async def avatar(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title=f"{user.name}'s Avatar",
        color=0x5865F2
    )
    embed.set_image(url=user.display_avatar.url)
    await interaction.followup.send(embed=embed, ephemeral=True)

@client.tree.command(name="channel-info", description="Get a channel info.")
@app_commands.describe(channel="Select a channel.")
async def channel_info(interaction: discord.Interaction, channel: discord.abc.GuildChannel):
    await interaction.response.defer(ephemeral=False)

    type_mapping = {
        discord.ChannelType.text: "Text",
        discord.ChannelType.voice: "Voice",
        discord.ChannelType.forum: "Forum",
        discord.ChannelType.stage_voice: "Stage",
        discord.ChannelType.category: "Category",
        discord.ChannelType.news: "Announcement/News",
        discord.ChannelType.news_thread: "News Thread",
        discord.ChannelType.public_thread: "Public Thread",
        discord.ChannelType.private_thread: "Private Thread"
    }

    channel_type = type_mapping.get(channel.type, str(channel.type).title())
    channel_desc = "No Description"
    if hasattr(channel, 'topic') and channel.topic:
        channel_desc = channel.topic

    category_name = "Uncategorized"
    if channel.category:
        category_name = channel.category.name

    slowmode_str = "No slowmode"
    if hasattr(channel, 'slowmode_delay') and channel.slowmode_delay > 0:
        slowmode_str = f"{channel.slowmode_delay} seconds"

    access_list = []
    for target, overwrite in channel.overwrites.items():
        if overwrite.view_channel is True:
            access_list.append(f"- {target.mention}")

    if not access_list:
        if channel.permissions_for(interaction.guild.default_role).view_channel:
            access_list = ["- Everyone has Access"]
        else:
            access_list = ["- No explicit roles or members assigned"]

    access_formatted = "\n".join(access_list)
    is_private = not channel.permissions_for(interaction.guild.default_role).view_channel
    private_str = "True" if is_private else "False"
    channel_link = channel.mention if hasattr(channel, 'mention') else "N/A"

    embed_desc = (
        f"**Channel Name:** {channel.name}\n"
        f"**Channel Desc:** {channel_desc}\n"
        f"**Type:** {channel_type}\n"
        f"**Category:** {category_name}\n"
        f"**Slowmode:** {slowmode_str}\n\n"
        f"**Access:**\n{access_formatted}\n\n"
        f"**Private Channel:** {private_str}\n"
        f"**Channel Id:** {channel.id}\n"
        f"**Channel Link:** {channel_link}"
    )

    embed = discord.Embed(
        title=f"{channel.name} Info",
        description=embed_desc,
        color=0x5865F2
    )
    await interaction.followup.send(embed=embed)

@client.tree.command(name="stick-create", description="Create a sticky message on the channel.")
@app_commands.describe(
    channel="Select a channel where to add sticky message.",
    message="Set sticky message.",
    duration="Number of messages to trigger sticky (Optional, default is 5)."
)
@app_commands.checks.has_permissions(manage_messages=True)
async def stick_create(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str,
    duration: int = 5
):
    await interaction.response.defer(ephemeral=True)
    doc_ref = db.collection("sticky").document(str(channel.id))
    doc_ref.set({
        "message": message,
        "duration": duration,
        "enabled": True,
        "last_message_id": None,
        "message_count": 0
    })
    await interaction.followup.send(
        f"Sticky message set in {channel.mention}!\n"
        f"**Message:** {message}\n"
        f"**Trigger every:** {duration} messages",
        ephemeral=True
    )

@stick_create.error
async def stick_create_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="stick-remove", description="Remove stick message from a specific channel.")
@app_commands.describe(
    channel="Select a channel where stick messages will be deleted."
)
@app_commands.checks.has_permissions(manage_messages=True)
async def stick_remove(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    await interaction.response.defer(ephemeral=True)
    doc_ref = db.collection("sticky").document(str(channel.id))
    doc = doc_ref.get()
    if not doc.exists:
        await interaction.followup.send(
            f"No sticky message found in {channel.mention}!",
            ephemeral=True
        )
        return

    data = doc.to_dict()
    last_message_id = data.get("last_message_id", None)
    if last_message_id:
        try:
            old_msg = await channel.fetch_message(int(last_message_id))
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    doc_ref.delete()
    await interaction.followup.send(
        f"Sticky message removed from {channel.mention}!",
        ephemeral=True
    )

@stick_remove.error
async def stick_remove_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="stick-list", description="Get the list of channels with stick messages.")
@app_commands.checks.has_permissions(manage_messages=True)
async def stick_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    docs = db.collection("sticky").stream()
    entries = []
    for doc in docs:
        channel_id = int(doc.id)
        channel = interaction.guild.get_channel(channel_id)
        if channel:
            entries.append(f"- #{channel.name} | Id: {channel_id}")

    if not entries:
        await interaction.followup.send(
            "No sticky messages found in this server!",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Sticky Messages List",
        description="\n".join(entries),
        color=0x5865F2
    )
    embed.set_footer(text=f"Total: {len(entries)} sticky channel(s)")
    await interaction.followup.send(embed=embed, ephemeral=True)

@stick_list.error
async def stick_list_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="trigger-create", description="Create a trigger messages.")
@app_commands.describe(
    trigger="Trigger message...",
    response="Bot response in message.",
    channel="Select the channel to set trigger (Optional)."
)
@app_commands.checks.has_permissions(manage_messages=True)
async def trigger_create(
    interaction: discord.Interaction,
    trigger: str,
    response: str,
    channel: discord.TextChannel = None
):
    await interaction.response.defer(ephemeral=True)
    target_channel = channel or interaction.channel
    doc_ref = db.collection("triggers").document(str(target_channel.id))
    doc_ref.set({
        "trigger": trigger,
        "response": response
    })
    await interaction.followup.send(
        f"Trigger created successfully for {target_channel.mention}!",
        ephemeral=True
    )

@client.tree.command(name="trigger-remove", description="Remove trigger from the channel.")
@app_commands.describe(
    channel="Channel to remove trigger.",
    remove_all="Delete all triggers in the channels."
)
@app_commands.choices(remove_all=[
    app_commands.Choice(name="Confirm", value="Confirm"),
    app_commands.Choice(name="Cancel", value="Cancel")
])
@app_commands.checks.has_permissions(manage_messages=True)
async def trigger_remove(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    remove_all: app_commands.Choice[str] = None
):
    await interaction.response.defer(ephemeral=True)
    doc_ref = db.collection("triggers").document(str(channel.id))
    doc = doc_ref.get()
    if not doc.exists:
        await interaction.followup.send("No triggers found in this channel.", ephemeral=True)
        return

    if remove_all and remove_all.value == "Cancel":
        await interaction.followup.send("Deletion cancelled.", ephemeral=True)
        return

    doc_ref.delete()
    await interaction.followup.send("Triggers deletion success!", ephemeral=True)

@client.tree.command(name="triggers-list", description="Get the list of triggers.")
async def triggers_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    docs = db.collection("triggers").stream()
    entries = []
    for doc in docs:
        channel_id = int(doc.id)
        channel = interaction.guild.get_channel(channel_id)
        if channel:
            entries.append(f"- #{channel.name}")

    if not entries:
        await interaction.followup.send("No active triggers found in this server!", ephemeral=True)
        return

    embed = discord.Embed(
        title="Triggers List",
        description="\n".join(entries),
        color=0x5865F2
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

@client.tree.command(name="ticket-panel", description="Create a ticket panel.")
@app_commands.describe(
    name="Give the ticket panel a name.",
    desc="Description of the ticket panel.",
    channel="Select a channel where panel will send.",
    category="Select a category where tickets channels will be located."
)
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_panel(
    interaction: discord.Interaction,
    name: str,
    desc: str,
    channel: discord.TextChannel,
    category: discord.CategoryChannel = None
):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title=name,
        description=desc,
        color=0xFFE135
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    embed.set_footer(text="Powered by SigmaBot")

    try:
        panel_message = await channel.send(embed=embed, view=CreateTicketView())
        doc_ref = db.collection("ticket_panels").document(str(interaction.guild.id))
        doc_ref.set({
            "channel_id": str(channel.id),
            "message_id": str(panel_message.id),
            "name": name,
            "desc": desc,
            "category_id": str(category.id) if category else None
        })
        await interaction.followup.send(
            f"Ticket panel created successfully in {channel.mention}!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "Buddy, give me permission to send messages or embed in the channel!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"An error occurred: {e}",
            ephemeral=True
        )

@ticket_panel.error
async def ticket_panel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="edt-ticket-panel", description="Edit the ticket panel.")
@app_commands.checks.has_permissions(manage_channels=True)
async def edt_ticket_panel(interaction: discord.Interaction):
    doc_ref = db.collection("ticket_panels").document(str(interaction.guild.id))
    doc = doc_ref.get()
    if not doc.exists:
        await interaction.response.send_message("No ticket panel found for this server!", ephemeral=True)
        return

    data = doc.to_dict()
    channel_id = int(data.get("channel_id", 0))
    message_id = int(data.get("message_id", 0))
    modal = EditTicketPanelModal(channel_id=channel_id, message_id=message_id)
    await interaction.response.send_modal(modal)

@edt_ticket_panel.error
async def edt_ticket_panel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

client.run(os.getenv("TOKEN"))
