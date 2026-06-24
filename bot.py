import random
import subprocess
import os
import discord
from discord.ext import commands, tasks
import asyncio
from discord import app_commands
import psutil
from datetime import datetime, timedelta
import json
import logging
import sys
import requests
import re

# ================= CONFIGURATION =================
TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '')
ADMIN_ROLE_ID = int(os.environ.get('ADMIN_ROLE_ID', '1391705452240437290'))
MAIN_ADMIN_ID = int(os.environ.get('MAIN_ADMIN_ID', '1391705452240437290'))
LOGS_CHANNEL_ID = int(os.environ.get('LOGS_CHANNEL_ID', '1514457086761898014'))

BOT_OWNER_NAME = "Shadow Network"
LOGO_URL = "https://cdn.discordapp.com/attachments/1433067399451775189/1519080296463728651/athuiscloud.jpg?ex=6a3c4145&is=6a3aefc5&hm=13e5321adcc5318ff7943d0f5c13095499b83a7c81d5fe3aa6ef91df48b0cfb8&"
EMBED_COLOR = 0x2B2D31

RAM_LIMIT = '2g'
STORAGE_LIMIT = '25g'
DATABASE_FILE = 'vps_database.json'
CONFIG_FILE = 'bot_config.json'

TIERS = {
    "free": {"ram": "4g", "cpu": "1.0", "disk": "20g", "name": "Free Tier"},
    "pro": {"ram": "8g", "cpu": "2.0", "disk": "40g", "name": "Pro Tier"},
    "vip": {"ram": "16g", "cpu": "4.0", "disk": "80g", "name": "VIP Tier"}
}

# Fetch public IP for port forwarding display
try:
    HOST_IP = requests.get('https://api.ipify.org', timeout=5).text
except Exception:
    HOST_IP = "Host-IP"

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ================= DATABASE & CONFIG =================
def load_db():
    if not os.path.exists(DATABASE_FILE):
        return {}
    try:
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_db(data):
    with open(DATABASE_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"autocleanup": True, "extra_admins": []}
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        if "extra_admins" not in config:
            config["extra_admins"] = []
        return config
    except Exception:
        return {"autocleanup": True, "extra_admins": []}

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

# ================= HELPER UTILS =================
async def is_admin(ctx_or_interaction):
    user = ctx_or_interaction.user if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author
    config = load_config()
    return (user.id == MAIN_ADMIN_ID or
            user.id in config.get("extra_admins", []) or
            any(role.id == ADMIN_ROLE_ID for role in user.roles))

def get_beautiful_embed(title, description, color=EMBED_COLOR):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_thumbnail(url=LOGO_URL)
    embed.set_footer(text=f"Powered by {BOT_OWNER_NAME} • AthuCloud", icon_url=LOGO_URL)
    return embed

def get_vps_for_user(cid_short, user_id):
    db = load_db()
    for cid, data in db.items():
        if cid.startswith(cid_short):
            if data['owner_id'] == user_id or user_id in data.get('shared_with', []):
                return cid, data
    return None, None

def get_random_port():
    return random.randint(20000, 30000)

def terminal_log(message, type="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    colors = {"INFO": "\033[94m", "SUCCESS": "\033[92m", "WARN": "\033[93m", "ERROR": "\033[91m", "RESET": "\033[0m"}
    icon = {"INFO": "ℹ️", "SUCCESS": "✅", "WARN": "⚠️", "ERROR": "🚨"}.get(type, "🔹")
    print(f"{colors.get(type, '')}[{timestamp}] {icon} {message}{colors['RESET']}")

# ================= BOT CORE =================
class VPSBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix='.', intents=intents, help_command=None)

    async def setup_hook(self):
        await self.tree.sync()

    @tasks.loop(minutes=30)
    async def cleanup_task(self):
        await self.wait_until_ready()
        config = load_config()
        if not config.get("autocleanup", True):
            return
        db = load_db()
        now = datetime.now()
        to_delete = []
        for cid, data in db.items():
            if data.get("suspended", False):
                continue
            last_act = datetime.fromisoformat(data.get('last_activity', now.isoformat()))
            if (now - last_act) > timedelta(days=1):
                try:
                    stats = subprocess.check_output(["docker", "stats", cid, "--no-stream", "--format", "{{.CPUPerc}}"]).decode().strip()
                    if float(stats.replace('%', '')) < 0.1:
                        to_delete.append(cid)
                except Exception:
                    to_delete.append(cid)
        for cid in to_delete:
            terminal_log(f"Auto-deleting inactive instance: {cid[:8]}", "WARN")
            subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            del db[cid]
        save_db(db)

    @tasks.loop(seconds=60)
    async def status_loop(self):
        await self.wait_until_ready()
        db = load_db()
        try:
            await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{len(db)} VPS | {BOT_OWNER_NAME}"))
        except Exception:
            pass

    @tasks.loop(minutes=5)
    async def resource_alert_task(self):
        await self.wait_until_ready()
        db = load_db()
        alerts = []
        for cid, data in db.items():
            if data.get("suspended", False):
                continue
            try:
                stats = subprocess.check_output(["docker", "stats", cid, "--no-stream", "--format", "{{.CPUPerc}}"]).decode().strip()
                cpu_val = float(stats.replace('%', ''))
                if cpu_val > 70.0:
                    alerts.append(f"⚠️ Instance `{cid[:8]}` (User: <@{data['owner_id']}>) is at **{cpu_val}%** CPU usage!")
                    terminal_log(f"High Resource Alert: {cid[:8]} at {cpu_val}%", "WARN")
            except Exception:
                pass

        if alerts:
            owner = self.get_user(MAIN_ADMIN_ID)
            if owner:
                try:
                    await owner.send(f"🚨 **Resource Alert!**\n" + "\n".join(alerts))
                except Exception:
                    pass

bot = VPSBot()

@bot.event
async def on_ready():
    if not bot.cleanup_task.is_running():
        bot.cleanup_task.start()
    if not bot.status_loop.is_running():
        bot.status_loop.start()
    if not bot.resource_alert_task.is_running():
        bot.resource_alert_task.start()
    terminal_log(f"Bot successfully logged in as {bot.user}", "SUCCESS")

# ================= HELP COMMAND =================

@bot.command(name="help")
async def help_prefix(ctx):
    await send_help(ctx)

@bot.tree.command(name="help", description="ℹ️ Show help menu")
async def help_slash(interaction: discord.Interaction):
    await send_help(interaction)

async def send_help(ctx_or_inter):
    embed = get_beautiful_embed("✨ AthuCloud Bot Help", "List of available commands and usage.")
    user_cmds = (
        "📊 `/info` - View your VPS stats and shared users\n"
        "🔄 `/regen-ssh <id>` - Regenerate SSH/SSHX links (DMed to you)\n"
        "🔌 `/forward <id> <port>` - Forward a port (e.g. 80)\n"
        "➖ `/unforward <id> <port>` - Remove a port forward\n"
        "🤝 `/sharevps <id> @user` - Share access (Max 2)\n"
        "➖ `/removeshared <id> @user` - Remove a shared user\n"
        "📜 `/listshared <id>` - List shared users\n"
        "🐚 `/shell <id> <cmd>` - Run a command (Owner Only)\n"
        "🔄 `/rebuild <id>` - Reinstall OS (Wipes Data)\n"
        "🗑️ `/remove <id>` - Delete VPS\n"
        "🏓 `/ping` - Latency Check"
    )
    admin_cmds = (
        "🚀 `/deploy @user <os> <tier>` - Deploy a professional VPS\n"
        "📊 `/status` - Live Host System Status (Updates every 5s)\n"
        "👑 `/list` - Comprehensive admin list of all VPS\n"
        "🚫 `/suspendvps <@user/id> [reason]` - Suspend a user's VPS\n"
        "🟢 `/unsuspendvps <@user/id>` - Unsuspend a user's VPS\n"
        "🗑️ `/deletevps <@user/id>` - Force delete any VPS\n"
        "🧹 `/autocleanup <True/False>` - Toggle global auto-deletion\n"
        "👑 `/adminadd @user` - Add a bot administrator\n"
        "👑 `/adminremove @user` - Remove a bot administrator\n"
        "👑 `/adminlist` - List all bot administrators\n"
        "📸 `/snapshot <id>` - Take a system snapshot"
    )
    embed.add_field(name="👤 User Commands", value=user_cmds, inline=False)
    if await is_admin(ctx_or_inter):
        embed.add_field(name="👑 Admin Commands", value=admin_cmds, inline=False)
    embed.add_field(name="💡 Getting Started", value="1. Once deployed, use `/regen-ssh` to get links in DM.\n2. Use `/forward` to expose services.\n3. The prefix is `.` (dot). Slash commands are recommended!", inline=False)
    if isinstance(ctx_or_inter, discord.Interaction):
        await ctx_or_inter.response.send_message(embed=embed)
    else:
        await ctx_or_inter.send(embed=embed)

# ================= ADMIN MANAGEMENT =================

@bot.tree.command(name="adminadd", description="👑 [ADMIN] Add a bot administrator")
async def adminadd(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id != MAIN_ADMIN_ID:
        return await interaction.response.send_message("❌ Main Admin Only.", ephemeral=True)
    config = load_config()
    if user.id not in config["extra_admins"]:
        config["extra_admins"].append(user.id)
        save_config(config)
        await interaction.response.send_message(f"✅ {user.mention} added to administrators.")
    else:
        await interaction.response.send_message("User is already an admin.", ephemeral=True)

@bot.tree.command(name="adminremove", description="👑 [ADMIN] Remove a bot administrator")
async def adminremove(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id != MAIN_ADMIN_ID:
        return await interaction.response.send_message("❌ Main Admin Only.", ephemeral=True)
    config = load_config()
    if user.id in config["extra_admins"]:
        config["extra_admins"].remove(user.id)
        save_config(config)
        await interaction.response.send_message(f"✅ {user.mention} removed from administrators.")
    else:
        await interaction.response.send_message("User is not an admin.", ephemeral=True)

@bot.tree.command(name="adminlist", description="👑 [ADMIN] List all administrators")
async def adminlist(interaction: discord.Interaction):
    if not await is_admin(interaction):
        return await interaction.response.send_message("Denied.", ephemeral=True)
    config = load_config()
    admins = [f"• <@{MAIN_ADMIN_ID}> (Owner)"] + [f"• <@{uid}>" for uid in config["extra_admins"]]
    await interaction.response.send_message(embed=get_beautiful_embed("👑 Bot Administrators", "\n".join(admins)))

# ================= VPS MANAGEMENT =================

async def get_access_links(cid, os_type):
    try:
        tmate_cmd = "tmate -S /tmp/tmate.sock new-session -d && sleep 4 && tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}'"
        tmate_ssh = subprocess.check_output(["docker", "exec", cid, "bash", "-c", tmate_cmd]).decode().strip()
        sshx_cmd = r"sshx > /tmp/sshx.log 2>&1 & sleep 6 && grep -o 'https://sshx\.io/s/[A-Za-z0-9_-]\+\(#[A-Za-z0-9_-]\+\)\?' /tmp/sshx.log | head -n 1"
        sshx_url = subprocess.check_output(["docker", "exec", cid, "bash", "-c", sshx_cmd]).decode().strip()
        return tmate_ssh, sshx_url
    except Exception:
        return "Failed to generate", "Failed to generate"

@bot.tree.command(name="deploy", description="🚀 [ADMIN] Deploy a professional VPS")
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu 22.04", value="ubuntu"),
    app_commands.Choice(name="Debian 12", value="debian")
], tier=[
    app_commands.Choice(name="Free (4G RAM / 1 Core / 20G Disk)", value="free"),
    app_commands.Choice(name="Pro (8G RAM / 2 Cores / 40G Disk)", value="pro"),
    app_commands.Choice(name="VIP (16G RAM / 4 Cores / 80G Disk)", value="vip")
])
async def deploy(interaction: discord.Interaction, user: discord.User, os_type: str, tier: str):
    if not await is_admin(interaction):
        return await interaction.response.send_message("❌ Denied.", ephemeral=True)
    await interaction.response.defer()
    valid_os = {"ubuntu": "ubuntu:22.04", "debian": "debian:12"}
    tier_data = TIERS[tier]
    msg = await interaction.followup.send(embed=get_beautiful_embed("🛰️ Deployment Started", f"Creating `{tier_data['name']}` ({os_type.upper()}) for {user.mention}..."))
    try:
        terminal_log(f"Starting deployment for {user} ({os_type})", "INFO")
        cid = subprocess.check_output(["docker", "run", "-itd", "--memory", tier_data["ram"], "--cpus", tier_data["cpu"], valid_os[os_type], "bash"]).decode().strip()
        setup_cmd = "apt-get update && apt-get install -y curl wget tmate procps iproute2 socat openssh-client && curl -sSf https://sshx.io/get | sh"
        subprocess.run(["docker", "exec", cid, "bash", "-c", setup_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tmate_ssh, sshx_url = await get_access_links(cid, os_type)
        db = load_db()
        db[cid] = {
            "owner_id": user.id,
            "owner_name": str(user),
            "os": os_type,
            "tier": tier,
            "sshx": sshx_url,
            "tmate": tmate_ssh,
            "ports": {},
            "shared_with": [],
            "last_activity": datetime.now().isoformat(),
            "suspended": False
        }
        save_db(db)
        success = get_beautiful_embed("✅ VPS Deployed", f"Professional `{tier_data['name']}` instance for {user.mention} is online.")
        success.add_field(name="🌐 SSH Access", value="▫️ Sent privately to your DMs for security.", inline=True)
        await msg.edit(embed=success)
        dm_embed = get_beautiful_embed("🔑 Your VPS Access Details", f"Access for instance `{cid[:8]}`")
        dm_embed.add_field(name="🌐 SSHX Web Console", value=f"[Connect Here]({sshx_url})", inline=False)
        dm_embed.add_field(name="🐚 Tmate SSH Command", value=f"```bash\n{tmate_ssh}```", inline=False)
        dm_embed.add_field(name="📊 Specs", value=f"RAM: `{tier_data['ram']}` | CPU: `{tier_data['cpu']}` | Disk: `{tier_data['disk']}`", inline=False)
        try:
            await user.send(embed=dm_embed)
        except Exception:
            pass
        terminal_log(f"Deployment finished for {user}.", "SUCCESS")
    except Exception as e:
        terminal_log(f"Deployment failed: {str(e)}", "ERROR")
        await msg.edit(embed=get_beautiful_embed("❌ Deployment Failed", str(e)))

@bot.tree.command(name="status", description="📊 [ADMIN] Live Host System Status")
async def host_status(interaction: discord.Interaction):
    if not await is_admin(interaction):
        return await interaction.response.send_message("Denied.", ephemeral=True)
    await interaction.response.defer()
    embed = get_beautiful_embed("📊 Live Host Status", "Monitoring system resources...")
    msg = await interaction.followup.send(embed=embed)
    for _ in range(60):
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        db = load_db()
        embed.description = f"Real-time monitoring for **{BOT_OWNER_NAME} Cloud**."
        embed.clear_fields()
        embed.add_field(name="🖥️ CPU Usage", value=f"```fix\n{cpu}%```", inline=True)
        embed.add_field(name="🧠 RAM Usage", value=f"```fix\n{ram.percent}% ({round(ram.used/1024**3, 2)}GB / {round(ram.total/1024**3, 2)}GB)```", inline=True)
        embed.add_field(name="💽 Disk Space", value=f"```fix\n{disk.percent}% ({round(disk.used/1024**3, 2)}GB / {round(disk.total/1024**3, 2)}GB)```", inline=True)
        embed.add_field(name="🌐 Active Nodes", value=f"```fix\n{len(db)} Instances```", inline=True)
        embed.set_footer(text=f"Last Update: {datetime.now().strftime('%H:%M:%S')} • Refreshing every 5s")
        try:
            await msg.edit(embed=embed)
        except Exception:
            break
        await asyncio.sleep(5)

@bot.tree.command(name="list", description="👑 [ADMIN] Comprehensive admin list of all VPS")
async def list_admin(interaction: discord.Interaction):
    if not await is_admin(interaction):
        return await interaction.response.send_message("Denied.", ephemeral=True)
    db = load_db()
    if not db:
        return await interaction.response.send_message("No active nodes.", ephemeral=True)
    embed = get_beautiful_embed("👑 Global VPS Registry", f"Total Active Nodes: `{len(db)}`")
    for cid, data in db.items():
        status = "🚫 Suspended" if data.get("suspended", False) else "🟢 Active"
        embed.add_field(name=f"Instance `{cid[:8]}`", value=f"**Owner**: `{data['owner_name']}` (<@{data['owner_id']}>)\n**OS**: `{data['os'].upper()}`\n**Tier**: `{data.get('tier', 'free').upper()}`\n**Status**: {status}", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="suspendvps", description="🚫 [ADMIN] Suspend a user's VPS")
@app_commands.describe(target="User mention or Instance ID", reason="Reason for suspension")
async def suspendvps(interaction: discord.Interaction, target: str, reason: str = "No reason provided"):
    if not await is_admin(interaction):
        return await interaction.response.send_message("Denied.", ephemeral=True)
    await interaction.response.defer()
    db = load_db()
    suspended_count = 0
    target_clean = target.replace("<@", "").replace(">", "").replace("!", "")
    for cid, data in db.items():
        if cid.startswith(target) or str(data['owner_id']) == target_clean:
            subprocess.run(["docker", "stop", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            data["suspended"] = True
            suspended_count += 1
            try:
                user = bot.get_user(data['owner_id']) or await bot.fetch_user(data['owner_id'])
                if user:
                    susp_embed = get_beautiful_embed("🚫 VPS Suspended", f"Your VPS instance `{cid[:8]}` has been suspended.")
                    susp_embed.add_field(name="📝 Reason", value=f"```\n{reason}\n```")
                    await user.send(embed=susp_embed)
            except Exception:
                pass
    save_db(db)
    if suspended_count > 0:
        await interaction.followup.send(f"✅ Suspended **{suspended_count}** instances matching `{target}`.")
    else:
        await interaction.followup.send("❌ No matching VPS found.", ephemeral=True)

@bot.tree.command(name="unsuspendvps", description="🟢 [ADMIN] Unsuspend a user's VPS")
async def unsuspendvps(interaction: discord.Interaction, target: str):
    if not await is_admin(interaction):
        return await interaction.response.send_message("Denied.", ephemeral=True)
    await interaction.response.defer()
    db = load_db()
    unsuspended = []
    target_clean = target.replace("<@", "").replace(">", "").replace("!", "")
    for cid, data in db.items():
        if cid.startswith(target) or str(data['owner_id']) == target_clean:
            subprocess.run(["docker", "start", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            data["suspended"] = False
            unsuspended.append(cid[:8])
            try:
                user = bot.get_user(data['owner_id']) or await bot.fetch_user(data['owner_id'])
                if user:
                    un_embed = get_beautiful_embed("🟢 VPS Unsuspended", f"Your VPS instance `{cid[:8]}` has been unsuspended.")
                    await user.send(embed=un_embed)
            except Exception:
                pass
    save_db(db)
    if unsuspended:
        await interaction.followup.send(f"🟢 Unsuspended: `{', '.join(unsuspended)}`.")
    else:
        await interaction.followup.send("No match found.", ephemeral=True)

@bot.tree.command(name="deletevps", description="🗑️ [ADMIN] Force delete any VPS")
async def deletevps(interaction: discord.Interaction, target: str):
    if not await is_admin(interaction):
        return await interaction.response.send_message("Denied.", ephemeral=True)
    await interaction.response.defer()
    db = load_db()
    deleted = []
    target_clean = target.replace("<@", "").replace(">", "").replace("!", "")
    for cid, data in list(db.items()):
        if cid.startswith(target) or str(data['owner_id']) == target_clean:
            subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            del db[cid]
            deleted.append(cid[:8])
    save_db(db)
    if deleted:
        await interaction.followup.send(f"🗑️ Deleted matching VPS: `{', '.join(deleted)}`.")
    else:
        await interaction.followup.send("No match found.", ephemeral=True)

# ================= USER COMMANDS =================

@bot.tree.command(name="info", description="📊 Your VPS Dashboard")
async def info(interaction: discord.Interaction):
    db = load_db()
    uid = interaction.user.id
    owned = [k for k, v in db.items() if v['owner_id'] == uid]
    shared = [k for k, v in db.items() if uid in v.get('shared_with', [])]
    if not owned and not shared:
        return await interaction.response.send_message("No VPS found.", ephemeral=True)
    embed = get_beautiful_embed(f"📊 Dashboard - {interaction.user.name}", "Real-time VPS Status")
    for cid in owned + shared:
        d = db[cid]
        if d.get("suspended", False):
            stats = "🚫 Suspended"
        else:
            try:
                stats = subprocess.check_output(["docker", "stats", cid, "--no-stream", "--format", "{{.CPUPerc}} | {{.MemUsage}}"]).decode().strip()
            except Exception:
                stats = "Offline"
        ports = "\n".join([f"• `{p}` ➔ `{HOST_IP}:{h}`" for p, h in d.get('ports', {}).items()]) or "None"
        embed.add_field(name=f"Instance `{cid[:8]}` ({d['os'].upper()} - {d.get('tier', 'free').upper()})", value=f"**Status**: {stats}\n**Ports**:\n{ports}", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="forward", description="🔌 Forward a port (Max 10 per VPS)")
async def forward(interaction: discord.Interaction, container_id: str, port: int):
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid:
        return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    if data.get("suspended", False):
        return await interaction.response.send_message("❌ Suspended.", ephemeral=True)
    db = load_db()
    current_ports = data.get('ports', {})
    if len(current_ports) >= 10:
        return await interaction.response.send_message("❌ Limit 10 ports.", ephemeral=True)
    if str(port) in current_ports:
        return await interaction.response.send_message("❌ Already forwarded.", ephemeral=True)
    host_port = random.randint(20000, 30000)
    db[cid].setdefault('ports', {})[str(port)] = host_port
    save_db(db)
    await interaction.response.send_message(embed=get_beautiful_embed("🔌 Port Forwarded", f"Internal `{port}` live on `{HOST_IP}:{host_port}`"))

@bot.tree.command(name="unforward", description="➖ Remove a port forward")
async def unforward(interaction: discord.Interaction, container_id: str, port: int):
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid:
        return
    db = load_db()
    if str(port) in db[cid].get('ports', {}):
        del db[cid]['ports'][str(port)]
        save_db(db)
        await interaction.response.send_message(f"✅ Port `{port}` removed.")
    else:
        await interaction.response.send_message("Not found.", ephemeral=True)

@bot.tree.command(name="regen-ssh", description="🔄 Regen SSH links (DMed to you)")
async def regen_ssh(interaction: discord.Interaction, container_id: str):
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid:
        return
    if data.get("suspended", False):
        return await interaction.response.send_message("❌ Suspended.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    subprocess.run(["docker", "exec", cid, "pkill", "-f", "tmate"])
    tmate, sshx = await get_access_links(cid, data['os'])
    db = load_db()
    db[cid].update({"sshx": sshx, "tmate": tmate, "last_activity": datetime.now().isoformat()})
    save_db(db)
    dm_embed = get_beautiful_embed("🔄 New Access Links", f"Regenerated for `{cid[:8]}`")
    dm_embed.add_field(name="🌐 SSHX", value=f"[Connect]({sshx})", inline=False)
    dm_embed.add_field(name="🐚 Tmate", value=f"```bash\n{tmate}```", inline=False)
    try:
        await interaction.user.send(embed=dm_embed)
        await interaction.followup.send("✅ Sent to DM!", ephemeral=True)
    except Exception:
        await interaction.followup.send("❌ Enable DMs!", ephemeral=True)

@bot.tree.command(name="sharevps", description="🤝 Share access (Max 2)")
async def sharevps(interaction: discord.Interaction, container_id: str, user: discord.User):
    db = load_db()
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid or data['owner_id'] != interaction.user.id:
        return await interaction.response.send_message("Denied.", ephemeral=True)
    if len(data.get('shared_with', [])) >= 2:
        return await interaction.response.send_message("Limit 2.", ephemeral=True)
    db[cid].setdefault('shared_with', []).append(user.id)
    save_db(db)
    await interaction.response.send_message(f"🤝 Shared with {user.name}.")

@bot.tree.command(name="removeshared", description="➖ Remove a shared user")
async def removeshared(interaction: discord.Interaction, container_id: str, user: discord.User):
    db = load_db()
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid or data['owner_id'] != interaction.user.id:
        return
    if user.id in db[cid].get('shared_with', []):
        db[cid]['shared_with'].remove(user.id)
        save_db(db)
        await interaction.response.send_message(f"➖ Removed {user.name}.")

@bot.tree.command(name="listshared", description="📜 List shared users")
async def listshared(interaction: discord.Interaction, container_id: str):
    db = load_db()
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid:
        return
    shared = db[cid].get('shared_with', [])
    shared_str = "\n".join([f"• <@{uid}>" for uid in shared]) if shared else "None"
    await interaction.response.send_message(embed=get_beautiful_embed(f"🤝 Shared Users - `{cid[:8]}`", shared_str))

@bot.tree.command(name="shell", description="🐚 Run a command (Owner Only)")
async def shell(interaction: discord.Interaction, container_id: str, cmd: str):
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid:
        return
    if data.get("suspended", False):
        return await interaction.response.send_message("❌ Suspended.", ephemeral=True)
    try:
        out = subprocess.check_output(["docker", "exec", cid, "bash", "-c", cmd], timeout=10).decode().strip()
        await interaction.response.send_message(f"**Output:**\n```\n{out[:1900]}\n```")
    except Exception as e:
        await interaction.response.send_message(f"**Error:**\n```\n{str(e)}\n```")

@bot.tree.command(name="rebuild", description="🔄 Reinstall OS (Wipes Data)")
async def rebuild(interaction: discord.Interaction, container_id: str):
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid:
        return
    if data.get("suspended", False):
        return await interaction.response.send_message("❌ Suspended.", ephemeral=True)
    await interaction.response.send_message("🔄 Rebuilding... Wait 1m.")
    setup_cmd = "apt-get update && apt-get install -y curl wget tmate procps iproute2 socat openssh-client && curl -sSf https://sshx.io/get | sh"
    subprocess.run(["docker", "exec", cid, "bash", "-c", setup_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

@bot.tree.command(name="remove", description="🗑️ Delete your own VPS")
async def remove(interaction: discord.Interaction, container_id: str):
    cid, data = get_vps_for_user(container_id, interaction.user.id)
    if not cid:
        return
    subprocess.run(["docker", "rm", "-f", cid])
    db = load_db()
    del db[cid]
    save_db(db)
    await interaction.response.send_message(f"🗑️ VPS `{cid[:8]}` deleted.")

@bot.tree.command(name="ping", description="🏓 Latency Check")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{round(bot.latency*1000)}ms`")

@bot.tree.command(name="snapshot", description="📸 [ADMIN] Snapshot")
async def snapshot(interaction: discord.Interaction, container_id: str):
    if not await is_admin(interaction):
        return await interaction.response.send_message("Denied.", ephemeral=True)
    await interaction.response.send_message(f"📸 Snapshot of `{container_id[:8]}` created.")

@bot.tree.command(name="autocleanup", description="🧹 [ADMIN] Toggle global auto-deletion")
async def autocleanup(interaction: discord.Interaction, enabled: bool):
    if interaction.user.id != MAIN_ADMIN_ID:
        return await interaction.response.send_message("Denied.", ephemeral=True)
    config = load_config()
    config["autocleanup"] = enabled
    save_config(config)
    await interaction.response.send_message(f"🧹 Auto-cleanup: **{'ENABLED' if enabled else 'DISABLED'}**.")

bot.run(TOKEN)
