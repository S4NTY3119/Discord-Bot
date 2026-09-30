import os
import sys
import time
import random
import re
import asyncio
import sqlite3
import json
import traceback
from datetime import timedelta
from collections import defaultdict
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands
from openai import OpenAI

load_dotenv()

# ==============================================================================
# CONFIGURATION & MULTI-SERVER SETTINGS
# ==============================================================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OWNER_ID = 1410259774225911858
# All configured servers: easily edit or add new server IDs here
SERVERS = {
    # Server 1: CIT Unofficial
    1513910086924501162: {
        "name": "CIT Unofficial",
        "roles": {
            "co_owner": 1514161127938850926,
            "mod": 1513910086924501163,
            "special": 1514146654662561792,
            "alumni": 1517412961185042483,
            "cooked": 1517197237313208400,
            "member": 1514152705449398384,
            "verified": 1518871263840309258,
            "unverified": 1518871208874217572,
        },
        "channels": {
            "chatbot": 1514987720739393586,
            "guess_the_num": 1516009857470169161,
            "tod": 1516759416375083008,
            "welcome": 1514176049028792390,
            "verify": 1518875927692574761,
            "ticket_panel": 1514987720739393586,
            "ticket_category": 1452583765640740966,
            "exempt_media": 1517196684973572106,  # Goon squad / link bypass
            "mod_channels": {1516496290601766932, 1517196684973572106},
        },
    },
    # Server 2: Replica Server
    1533694023850655784: {
        "name": "Replica Server",
        "roles": {
            "co_owner": 1533721184297029702,
            "mod": 1533694023871500345,
            "special": 1533694023871500342,
            "alumni": 1533694023871500341,
            "cooked": 1533694023850655786,
            "member": 1533694023850655791,
            "verified": 1533694023850655787,
            "unverified": 1533694023850655792,
        },
        "channels": {
            "chatbot": 1533694025226260621,
            "guess_the_num": 1533694026509848666,
            "tod": 1533694026509848667,
            "welcome": 1533694024563556428,
            "verify": 1533694024563556423,
            "ticket_panel": 1534214178229846056,
            "ticket_category": 1534222151891288136,
            "exempt_media": 1533694025645817906,  # Goon squad / link bypass
            "mod_channels": {1533694025645817906, 1533694024563556430},
        },
    },
}

# Fast aggregated lookup sets across all servers
ALL_CHATBOT_CHANNELS = {cfg["channels"]["chatbot"] for cfg in SERVERS.values() if cfg["channels"].get("chatbot")}
ALL_GAME_CHANNELS = {cfg["channels"]["guess_the_num"] for cfg in SERVERS.values() if cfg["channels"].get("guess_the_num")}
ALL_TOD_CHANNELS = {cfg["channels"]["tod"] for cfg in SERVERS.values() if cfg["channels"].get("tod")}
ALL_EXEMPT_CHANNELS = {cfg["channels"]["exempt_media"] for cfg in SERVERS.values() if cfg["channels"].get("exempt_media")}
ALL_TICKET_CATEGORIES = {cfg["channels"]["ticket_category"] for cfg in SERVERS.values() if cfg["channels"].get("ticket_category")}

# ==============================================================================
# PERMISSION & HELPER FUNCTIONS
# ==============================================================================
def is_ticket_channel(channel) -> bool:
    if not channel:
        return False
    if hasattr(channel, "name") and (channel.name.startswith("ticket-") or channel.name.startswith("closed-")):
        return True
    if hasattr(channel, "category_id") and channel.category_id in ALL_TICKET_CATEGORIES:
        return True
    if hasattr(channel, "id") and get_ticket_by_channel(channel.id) is not None:
        return True
    return False
def get_role_id(guild_id, role_key):
    return SERVERS.get(guild_id, {}).get("roles", {}).get(role_key)

def get_channel_id(guild_id, channel_key):
    return SERVERS.get(guild_id, {}).get("channels", {}).get(channel_key)

def is_s4nty_or_owner(member: discord.Member) -> bool:
    if not member or not hasattr(member, "guild") or not member.guild:
        return False
    if member.id == OWNER_ID or (hasattr(member, "guild_permissions") and member.guild_permissions.administrator):
        return True
    role_names = [r.name.lower() for r in getattr(member, "roles", [])]
    return "s4nty" in role_names or "owner" in role_names

def is_mod(member: discord.Member) -> bool:
    if not member or not hasattr(member, "guild") or not member.guild:
        return False
    if member.id == OWNER_ID or (hasattr(member, "guild_permissions") and member.guild_permissions.administrator):
        return True
    roles_cfg = SERVERS.get(member.guild.id, {}).get("roles", {})
    mod_ids = {roles_cfg.get("co_owner"), roles_cfg.get("mod")} - {None}
    return any(r.id in mod_ids for r in member.roles) if hasattr(member, "roles") else False

def has_bypass_role(member: discord.Member) -> bool:
    if not member or not hasattr(member, "guild") or not member.guild:
        return False
    if member.id == OWNER_ID or (hasattr(member, "guild_permissions") and member.guild_permissions.administrator):
        return True
    roles_cfg = SERVERS.get(member.guild.id, {}).get("roles", {})
    bypass_ids = {
        roles_cfg.get("co_owner"),
        roles_cfg.get("mod"),
        roles_cfg.get("special"),
        roles_cfg.get("alumni"),
    } - {None}
    return any(r.id in bypass_ids for r in member.roles) if hasattr(member, "roles") else False

def has_giveaway_role(member: discord.Member) -> bool:
    if not member or not hasattr(member, "guild") or not member.guild:
        return False
    if member.id == OWNER_ID or (hasattr(member, "guild_permissions") and member.guild_permissions.administrator):
        return True
    roles_cfg = SERVERS.get(member.guild.id, {}).get("roles", {})
    giveaway_ids = {
        roles_cfg.get("co_owner"),
        roles_cfg.get("mod"),
        roles_cfg.get("special"),
        roles_cfg.get("alumni"),
    } - {None}
    return any(r.id in giveaway_ids for r in member.roles) if hasattr(member, "roles") else False

async def complete_verification(guild: discord.Guild, member: discord.Member) -> tuple[bool, str]:
    if not guild or not member:
        return False, "Invalid server or user context."

    roles_cfg = SERVERS.get(guild.id, {}).get("roles", {})
    verified_id = roles_cfg.get("verified")
    unverified_id = roles_cfg.get("unverified")
    member_id = roles_cfg.get("member")

    verified = guild.get_role(verified_id) if verified_id else None
    unverified = guild.get_role(unverified_id) if unverified_id else None
    member_role = guild.get_role(member_id) if member_id else None

    if not verified:
        return False, "Verified role not configured for this server."

    try:
        roles_to_add = [r for r in [verified, member_role] if r and r not in member.roles]

        # Add chosen course role and remove other course roles
        chosen_course_id = get_user_course(member.id)
        all_course_ids = set(COURSE_ROLES.values())
        if chosen_course_id:
            course_role = guild.get_role(chosen_course_id)
            if course_role and course_role not in member.roles and course_role not in roles_to_add:
                roles_to_add.append(course_role)

            roles_to_remove = [r for r in member.roles if r.id in all_course_ids and r.id != chosen_course_id]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)

        # Add chosen year role and remove other year roles
        chosen_year_id = get_user_year(member.id)
        all_year_ids = set(YEAR_ROLES.values())
        if chosen_year_id:
            year_role = guild.get_role(chosen_year_id)
            if year_role and year_role not in member.roles and year_role not in roles_to_add:
                roles_to_add.append(year_role)

            year_roles_to_remove = [r for r in member.roles if r.id in all_year_ids and r.id != chosen_year_id]
            if year_roles_to_remove:
                await member.remove_roles(*year_roles_to_remove)

        if roles_to_add:
            await member.add_roles(*roles_to_add)
        if unverified and unverified in member.roles:
            await member.remove_roles(unverified)
        return True, "You have been verified! Welcome to the CIT's unofficial server!"
    except discord.Forbidden:
        return False, "Bot missing permissions! In Server Settings -> Roles, move the Bot's role above Member/Verified roles."
    except Exception as e:
        return False, f"Verification error: {e}, contact the admin s4nty"

# ==============================================================================
# DATABASE SETUP
# ==============================================================================
DB_PATH = os.path.join(os.path.dirname(__file__), "chat_logs.db")
print("Database path:", DB_PATH)

def _remove_stray_wal_files(db_path):
    if not db_path or db_path == ":memory:":
        return
    for ext in ["-wal", "-shm", "-journal"]:
        fpath = db_path + ext
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass

def init_db():
    global DB_PATH
    _remove_stray_wal_files(DB_PATH)

    paths_to_try = [
        DB_PATH,
        os.path.join("/tmp", "chat_logs.db"),
        ":memory:"
    ]

    for path in paths_to_try:
        try:
            with sqlite3.connect(path, timeout=30.0) as conn:
                cur = conn.cursor()
                cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    message TEXT,
                    channel_name TEXT,
                    channel_id INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """)
                cur.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    user_id INTEGER,
                    channel_id INTEGER UNIQUE,
                    ticket_number INTEGER,
                    ticket_type TEXT,
                    status TEXT DEFAULT 'open',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    closed_at DATETIME
                )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(guild_id, user_id, status);")
                conn.commit()

            DB_PATH = path
            print(f"[DB Info]: Database initialized successfully at {DB_PATH}")
            return
        except Exception as e:
            print(f"[DB Warning]: Could not initialize database at {path}: {e}")

    print("[DB Error]: Failed to initialize database at any path.")

try:
    init_db()
except Exception as e:
    print(f"[DB Startup Error]: {e}")

def db_query(query: str, params: tuple = (), fetchone: bool = False, fetchall: bool = False, commit: bool = False):
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            if commit:
                conn.commit()
            if fetchone:
                return cur.fetchone()
            if fetchall:
                return cur.fetchall()
            return None
    except Exception as e:
        print(f"[DB Error in query]: {e}")
        return None

def get_user_open_ticket(guild_id: int, user_id: int) -> dict | None:
    row = db_query(
        "SELECT id, guild_id, user_id, channel_id, ticket_number, ticket_type, status FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (guild_id, user_id),
        fetchone=True
    )
    if row:
        return {
            "id": row[0],
            "guild_id": row[1],
            "user_id": row[2],
            "channel_id": row[3],
            "ticket_number": row[4],
            "ticket_type": row[5],
            "status": row[6]
        }
    return None

def get_user_ticket_count(guild_id: int, user_id: int) -> int:
    row = db_query(
        "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
        fetchone=True
    )
    if row and row[0] is not None:
        return row[0]
    return 0

def create_ticket_record(guild_id: int, user_id: int, channel_id: int, ticket_number: int, ticket_type: str):
    db_query(
        "INSERT INTO tickets (guild_id, user_id, channel_id, ticket_number, ticket_type, status) VALUES (?, ?, ?, ?, ?, 'open')",
        (guild_id, user_id, channel_id, ticket_number, ticket_type),
        commit=True
    )

def close_ticket_record(channel_id: int):
    db_query(
        "UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE channel_id = ? AND status = 'open'",
        (channel_id,),
        commit=True
    )

def get_ticket_by_channel(channel_id: int) -> dict | None:
    row = db_query(
        "SELECT id, guild_id, user_id, channel_id, ticket_number, ticket_type, status FROM tickets WHERE channel_id = ? ORDER BY id DESC LIMIT 1",
        (channel_id,),
        fetchone=True
    )
    if row:
        return {
            "id": row[0],
            "guild_id": row[1],
            "user_id": row[2],
            "channel_id": row[3],
            "ticket_number": row[4],
            "ticket_type": row[5],
            "status": row[6]
        }
    return None

# ==============================================================================
# TEXT NORMALIZATION & BLOCKLIST
# ==============================================================================
def normalize(text):
    text = text.lower()
    replacements = {
        "1": "i", "!": "i", "@": "a", "3": "e", "0": "o",
        "$": "s", "5": "s", "9": "g", "8": "b"
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r'[^a-z0-9\s]', '', text)

BLOCKLIST_PATH = os.path.join(os.path.dirname(__file__), "blocklist.txt")
nsfw_words = set()
try:
    if os.path.exists(BLOCKLIST_PATH):
        with open(BLOCKLIST_PATH, "r", encoding="utf-8") as f:
            raw_text = f.read().replace('\n', ',')
            raw_words = [w.strip().lower().replace("nsfw_words=", "").replace("{", "").replace("}", "").strip() for w in raw_text.split(",") if w.strip()]
            nsfw_words = {normalize(w) for w in raw_words if normalize(w)}
except Exception as e:
    print(f"Warning loading blocklist: {e}")

moderated_words = nsfw_words
URL_PATTERN = r'(https?://|www\.|discord\.gg/|\S+\.(com|net|org|gg|io|xyz|me|co))\S*'

# ==============================================================================
# MESSAGE CONTENT & LISTS
# ==============================================================================
WELCOME_MESSAGES = [
    "Yo {user}, welcome to the CIT unofficial server!!",
    "Hey {user}! You just entered the nerds-zone",
    "Sup {user}, hope you brought snacks ",
    "yooo {user} you js joined the chaos... I mean the server",
    "Welcome {user}! to the unofficial cit server, hope you brought some juice"
]

TRUTHS = [
    "What's the most embarrassing thing you've done in public?",
    "What's the biggest lie you've ever told?",
    "Who do you have a crush on?",
    "What's the most childish thing you still do?",
    "What's your most cringe memory?",
    "Have you ever cheated on a test?",
    "What's the worst thing you've ever said to someone?",
    "Have you ever blamed someone else for something you did?",
    "What's something you're 99% sure everyone else knows except you?",
    "What's something you wish you could un-know?",
    "What part of your personality is mostly fake?",
    "What's something you've never told anyone because it sounds too weird?",
    "Whom do you trust the most in your friend's group?. Ping them",
    "Have you ever fell in love with a senior?",
    "Do you really love your parents?",
    "If you could kill someone legally, who could it be?",
    "Who's your crush?, don't lie btw",
    "Who's the worst professor in the college?",
    "If you had a chance to kiss someone whom could it be?. No cap, say it out",
    "Do you like catgirls?/catbois?",
    "Who's the most hated person in your life?",
    "Who in the group would you want by your side in a zombie apocalypse?",
    "What are your fetishes?",
    "What's the weirdest thing you've done in a while?",
    "Are you straight?",
    "Who was your first celebrity crush?",
    "What's one thing you hate people knowing about you?",
    "How many love proposals you've done and rejections you have got so far?",
    "What is the weirdest thing you'd do when you're alone in your house?",
    "What's the most childish thing you still do?",
    "Who was the last person to kiss you?",
    "What's the dumbest mistake you've made recently?",
    "What is the craziest subject in your school that you never understand?",
    "what's the cringest thing you did 1743 days 12hours 31st minute 44th second ago?",
    "Have you ever E-Dated?, Where did it start and how did it end?",
    "Have you ever imagined yourself as a girl?, how was the imagination?",
    "Do you think that you can beat up the previously played person?"
]

DARES = [
    "Send a voice message singing any song",
    "Change your nickname to 'SigmaWolf_69' for 10 minutes",
    "Send the last photo in your camera roll",
    "Type everything in ALL CAPS for the next 5 minutes",
    "DM a random girl 'I think you're beautiful' and send a ss in `generals`",
    "Send a selfie or a drawing of yourself in `generals`",
    "Send a voice message saying 'I am a noodle' in `generals`",
    "Send a screenshot of your browser search history in `generals`",
    "Quit the game now!",
    "Say that you're in love with the previously played person, in `generals`",
    "Send the password of your mobile or laptop in `generals`",
    "Send a pic of your room, under 30 sec in `generals`",
    "Type a quote that you made by yourself",
    "Send a voice message of you beatboxing for 30 seconds",
    "Change your profile picture to a ugly selfie for 24 hours",
    "Tell the group your most embarrassing story, no skipping",
    "Name your top 3 people in the group you'd date if you had to",
    "Let the group pick your next profile picture for 48 hours",
    "Send your contact list and let the group pick someone to text",
    "Post 'I still sleep with a stuffed animal ' as your status",
    "Rate everyone in the call out of 10, honestly",
    "DM your crush just 'hey' and screenshot the conversation",
    "Send the last 5 things you searched on YouTube",
    "Text your best friend 'I've been lying to you' with no context",
    "Call a friend and speak only in another accent for the whole call",
    "Open your gallery and send the 31st photo no matter what it is",
    "Send everyone in the group your honest opinion about them, no sugarcoating",
    "Confess the most illegal thing you've ever done",
    "Change your status to 'in a relationship' with someone the group picks",
    "Let the group write your Tinder/Bumble bio and actually set it for 24 hours",
    "Let the group lock your most used app with a password they choose for 12 hours",
    "Reveal who in your contacts you lowkey have feelings for"
]

# Global state trackers
active_giveaways = {}
giveaway_update_tasks = {}
active_dmall_tasks = {}
fusei = None
verification_codes = {}
active_games = {}
game_tasks = {}
spam_tasks = {}
active_tod_lobby = {}
active_tod_game = {}
user_histories = {}
last_message = {}
last_closed_dms = {}

VERIFY_FILE = os.path.join(os.path.dirname(__file__), "active_verifications.json")
GIVEAWAYS_FILE = os.path.join(os.path.dirname(__file__), "active_giveaways.json")

user_chosen_courses = {}
user_chosen_years = {}

def save_giveaways():
    try:
        data = {str(k): v for k, v in active_giveaways.items()}
        with open(GIVEAWAYS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[Giveaway Save Error]: {e}")

def load_giveaways():
    global active_giveaways
    try:
        if os.path.exists(GIVEAWAYS_FILE):
            with open(GIVEAWAYS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            active_giveaways.clear()
            for k, v in data.items():
                active_giveaways[int(k)] = v
    except Exception as e:
        print(f"[Giveaway Load Error]: {e}")

def save_verify_code(user_id: int, code: str, course_role_id: int = None, year_role_id: int = None):
    verification_codes[user_id] = code.upper()
    if course_role_id:
        user_chosen_courses[user_id] = course_role_id
    if year_role_id:
        user_chosen_years[user_id] = year_role_id
    try:
        data = {}
        if os.path.exists(VERIFY_FILE):
            with open(VERIFY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[str(user_id)] = {
            "code": code.upper(),
            "course": course_role_id or user_chosen_courses.get(user_id),
            "year": year_role_id or user_chosen_years.get(user_id)
        }
        with open(VERIFY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

def get_user_course(user_id: int) -> int | None:
    if user_id in user_chosen_courses:
        return user_chosen_courses[user_id]
    try:
        if os.path.exists(VERIFY_FILE):
            with open(VERIFY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            val = data.get(str(user_id))
            if isinstance(val, dict):
                return val.get("course")
    except Exception:
        pass
    return None

def get_user_year(user_id: int) -> int | None:
    if user_id in user_chosen_years:
        return user_chosen_years[user_id]
    try:
        if os.path.exists(VERIFY_FILE):
            with open(VERIFY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            val = data.get(str(user_id))
            if isinstance(val, dict):
                return val.get("year")
    except Exception:
        pass
    return None

def check_verify_code(user_id: int, entered_code: str) -> bool:
    clean = entered_code.strip().replace("`", "").replace("'", "").replace('"', "").upper()
    if not clean:
        return False
    # Check RAM
    expected = verification_codes.get(user_id)
    if expected and expected.upper() == clean:
        return True
    # Check shared file (in case of multiple processes or restarts)
    try:
        if os.path.exists(VERIFY_FILE):
            with open(VERIFY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            val = data.get(str(user_id))
            if isinstance(val, dict) and val.get("code") == clean:
                return True
            elif isinstance(val, str) and val == clean:
                return True
            for v in data.values():
                if (isinstance(v, dict) and v.get("code") == clean) or (isinstance(v, str) and v == clean):
                    return True
    except Exception:
        pass
    return False

def clear_verify_code(user_id: int, entered_code: str = None):
    verification_codes.pop(user_id, None)
    user_chosen_courses.pop(user_id, None)
    user_chosen_years.pop(user_id, None)
    clean = entered_code.strip().replace("`", "").replace("'", "").replace('"', "").upper() if entered_code else None
    try:
        if os.path.exists(VERIFY_FILE):
            with open(VERIFY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.pop(str(user_id), None)
            if clean:
                for k, v in list(data.items()):
                    if (isinstance(v, dict) and v.get("code") == clean) or (isinstance(v, str) and v == clean):
                        data.pop(k, None)
            with open(VERIFY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
    except Exception:
        pass

# Course roles configuration
COURSE_ROLES = {
    "AIML": 1533694023863242831,
    "AIDS": 1533694023871500339,
    "CSE": 1533694023863242830,
    "CIVIL": 1533694023863242829,
    "EEE": 1533694023863242828,
    "IT": 1533694023863242827,
    "MECHANICAL": 1533694023863242826,
    "MECHATRONICS": 1533694023863242825,
    "BIO MEDICAL": 1533694023863242824,
    "CSBS": 1533694023863242823,
    "ECE": 1533694023863242822,
    "CYBER SECURITY": 1533694023850655793,
}

# Year roles configuration
YEAR_ROLES = {
    "1st Year": 1533694023850655788,
    "2nd Year": 1533694023871500340,
    "3rd Year": 1533694023850655789,
    "4th Year": 1533694023850655790,
}

# ==============================================================================
# BOT & AI INITIALIZATION
# ==============================================================================
# Safe initialization of OpenAI client for Groq
client_ai = None
if GROQ_API_KEY:
    try:
        client_ai = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY
        )
    except Exception as e:
        print(f"[AI Init Warning]: {e}")
else:
    print("[AI Warning]: GROQ_API_KEY not configured. AI chatbot features will be idle.")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = commands.Bot(command_prefix="!", intents=intents, owner_id=OWNER_ID)

# ==============================================================================
# ANTI-NUKE PROTECTION TRACKER & HELPERS
# ==============================================================================
bot_action_history = defaultdict(lambda: defaultdict(list))
kicking_bots = set()

async def trigger_bot_kick(guild: discord.Guild, bot_member: discord.Member, reason: str):
    if not guild or not bot_member or not getattr(bot_member, "bot", False):
        return
    if bot_member.id == client.user.id:
        return  # Protect our own bot from kicking itself
    if bot_member.id in kicking_bots:
        return  # Prevent duplicate concurrent kick calls for the same bot

    kicking_bots.add(bot_member.id)
    try:
        try:
            await guild.kick(bot_member, reason=f"[Anti-Nuke Protection] {reason}")
            print(f"[Anti-Nuke Triggered] Kicked rogue bot '{bot_member}' (ID: {bot_member.id}) from '{guild.name}' - Reason: {reason}", flush=True)

            # Notify server mod channels
            roles_cfg = SERVERS.get(guild.id, {}).get("channels", {})
            mod_channel_ids = roles_cfg.get("mod_channels", set())
            for chan_id in mod_channel_ids:
                channel = guild.get_channel(chan_id)
                if channel:
                    try:
                        await channel.send(
                            f"[ANTI-NUKE SYSTEM ACTIVATED]\n"
                            f"Rogue bot **{bot_member.mention}** (`{bot_member.id}`) was **KICKED** from the server!\n"
                            f"**Reason:** {reason}"
                        )
                    except Exception:
                        pass
        except discord.Forbidden:
            print(f"[Anti-Nuke Alert] Missing permissions to kick rogue bot '{bot_member}' (ID: {bot_member.id}). Sending emergency alerts!", flush=True)
            # Alert mod channels with @everyone tag
            roles_cfg = SERVERS.get(guild.id, {}).get("channels", {})
            mod_channel_ids = roles_cfg.get("mod_channels", set())
            for chan_id in mod_channel_ids:
                channel = guild.get_channel(chan_id)
                if channel:
                    try:
                        await channel.send(
                            f"@everyone [CRITICAL ANTI-NUKE ALERT]\n"
                            f"Rogue bot **{bot_member.mention}** (`{bot_member.id}`) triggered: **{reason}**!\n"
                            f"WARNING: I lack permissions to kick it (its role is higher than mine)! SERVER OWNER / ADMINS PLEASE KICK IT IMMEDIATELY!"
                        )
                    except Exception:
                        pass
            # Try DMing server owner directly
            if guild.owner:
                try:
                    await guild.owner.send(
                        f"[URGENT SERVER SECURITY WARNING]\n"
                        f"In your server **{guild.name}**, rogue bot **{bot_member.name}** (`{bot_member.id}`) triggered **{reason}**!\n"
                        f"I could not kick it because its role is higher than mine. Please kick or ban it immediately!"
                    )
                except Exception:
                    pass
    finally:
        await asyncio.sleep(5.0)
        kicking_bots.discard(bot_member.id)

# ==============================================================================
# ROLE ALL CORE EXECUTION & UI COMPONENTS
# ==============================================================================
active_roleall_guilds = set()

async def execute_roleall(interaction: discord.Interaction, selected_roles: list[discord.Role], exception_roles: list[discord.Role]):
    guild = interaction.guild
    if not guild:
        return

    if guild.id in active_roleall_guilds:
        msg = "[Error] A /roleall process is already currently running in this server. Please wait for it to finish."
        if interaction.response.is_done():
            return await interaction.followup.send(msg, ephemeral=True)
        else:
            return await interaction.response.send_message(msg, ephemeral=True)

    # Check role hierarchy to ensure bot can assign selected roles
    bot_member = guild.me or guild.get_member(client.user.id)
    if bot_member and hasattr(bot_member, "top_role"):
        higher_roles = [r for r in selected_roles if r >= bot_member.top_role]
        if higher_roles:
            invalid_names = ", ".join([f"`{r.name}`" for r in higher_roles])
            msg = (
                f"[Role Hierarchy Error]\n"
                f"The role(s) {invalid_names} are higher than or equal to my bot's top role (`{bot_member.top_role.name}`).\n"
                f"Please go to **Server Settings -> Roles** and drag my bot's role above these roles!"
            )
            if interaction.response.is_done():
                return await interaction.followup.send(msg, ephemeral=True)
            else:
                return await interaction.response.send_message(msg, ephemeral=True)

    active_roleall_guilds.add(guild.id)
    try:
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

        try:
            members = guild.members if guild.chunked else [m async for m in guild.fetch_members(limit=None)]
        except Exception:
            members = guild.members

        assigned_count = 0
        skipped_count = 0
        error_count = 0

        target_role_mentions = ", ".join([r.mention for r in selected_roles]) if selected_roles else "None"
        exc_role_mentions = ", ".join([r.mention for r in exception_roles]) if exception_roles else "None"

        processed_count = 0
        total_members = len(members)
        valid_exception_roles = set([r for r in exception_roles if r is not None and hasattr(r, "id")])

        for member in members:
            try:
                if not member or getattr(member, "bot", False):
                    skipped_count += 1
                    continue

                member_roles = getattr(member, "roles", [])
                # Skip member if they have ANY of the exception roles (up to 10)
                if valid_exception_roles and any(r in member_roles for r in valid_exception_roles):
                    skipped_count += 1
                    continue

                roles_to_add = [r for r in selected_roles if r not in member_roles]
                if not roles_to_add:
                    continue

                # Safe execution with 1.25s rate-limit pacing & retry backoff
                success = False
                for attempt in range(2):
                    try:
                        await member.add_roles(*roles_to_add, reason=f"/roleall invoked by {interaction.user}")
                        assigned_count += 1
                        success = True
                        break
                    except discord.HTTPException as e:
                        if e.status == 429:
                            retry_after = getattr(e, "retry_after", 5.0) or 5.0
                            print(f"[/roleall Rate Limit Notice]: API 429 hit. Backing off for {retry_after + 3.0:.1f}s...")
                            await asyncio.sleep(retry_after + 3.0)
                        else:
                            print(f"[/roleall Member Edit Error]: {e}")
                            break
                    except Exception as ex:
                        print(f"[/roleall Error]: {ex}")
                        break

                if not success and roles_to_add:
                    error_count += 1

                processed_count += 1
                # Dynamic live progress update every 10 members
                if processed_count % 10 == 0:
                    try:
                        await interaction.edit_original_response(
                            content=(
                                f"[Processing RoleAll Task...]\n"
                                f"* Updated: `{assigned_count}` members\n"
                                f"* Skipped: `{skipped_count}` members\n"
                                f"* Progress: `{processed_count} / {total_members}` processed..."
                            )
                        )
                    except Exception:
                        pass

                # Safe rate-limit pacing delay (1.25 seconds between member updates)
                await asyncio.sleep(1.25)
            except Exception as item_err:
                print(f"[/roleall Loop Error]: {item_err}")
                error_count += 1

        summary = (
            f"[RoleAll Execution Completed]\n\n"
            f"* Target Roles Assigned: {target_role_mentions}\n"
            f"* Exception Roles Skipped: {exc_role_mentions}\n"
            f"* Members Updated: `{assigned_count}`\n"
            f"* Members Skipped: `{skipped_count}`\n"
            f"* Failed/Errors: `{error_count}`"
        )
        try:
            await interaction.edit_original_response(content=summary, view=None)
        except Exception:
            if hasattr(interaction, "channel") and interaction.channel:
                try:
                    await interaction.channel.send(summary)
                except Exception:
                    pass
    except Exception as main_err:
        print(f"[/roleall Top Level Error]: {main_err}")
    finally:
        active_roleall_guilds.discard(guild.id)

class TargetRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="Step 1: Select up to 10 target roles to give...",
            min_values=1,
            max_values=10,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

class ExceptionRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="Step 2: Select up to 10 exception roles to skip...",
            min_values=0,
            max_values=10,
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

class RoleAllView(discord.ui.View):
    def __init__(self, invoker: discord.Member):
        super().__init__(timeout=300)
        self.invoker = invoker
        self.target_select = TargetRoleSelect()
        self.exception_select = ExceptionRoleSelect()
        self.add_item(self.target_select)
        self.add_item(self.exception_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id:
            await interaction.response.send_message("[Error] Only the user who ran /roleall can interact with this control.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Start Assigning Roles", style=discord.ButtonStyle.green, row=2, custom_id="roleall_confirm")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        selected_roles = self.target_select.values
        if not selected_roles:
            return await interaction.response.send_message("[Error] Please select at least one target role to assign.", ephemeral=True)

        exception_roles = self.exception_select.values or []

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=(
                "[Processing RoleAll Task...]\n"
                "Assigning selected roles across server members. Please wait..."
            ),
            view=self
        )

        await execute_roleall(interaction=interaction, selected_roles=selected_roles, exception_roles=exception_roles)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, row=2, custom_id="roleall_cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="[RoleAll operation cancelled.]", view=None)

# ==============================================================================
# VIEWS & UI COMPONENTS
# ==============================================================================
class YearSelect(discord.ui.Select):
    def __init__(self, course_role_id: int, course_name: str):
        self.course_role_id = course_role_id
        self.course_name = course_name
        options = [
            discord.SelectOption(label="1st Year", description="Fresher/First Year Student", value="1533694023850655788"),
            discord.SelectOption(label="2nd Year", description="Second Year Student", value="1533694023871500340"),
            discord.SelectOption(label="3rd Year", description="Third Year Student", value="1533694023850655789"),
            discord.SelectOption(label="4th Year", description="Fourth Year SENIOR Student", value="1533694023850655790"),
        ]
        super().__init__(placeholder="Step 2: Select your Year of study...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_year_role_id = int(self.values[0])
        year_name = [opt.label for opt in self.options if opt.value == self.values[0]][0]

        # Generate unique verification code and record course + year choices
        code = ''.join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6)).upper()
        save_verify_code(interaction.user.id, code, course_role_id=self.course_role_id, year_role_id=selected_year_role_id)

        await interaction.response.edit_message(
            content=(
                f"**Verification Details Recorded**\n\n"
                f"- **Course:** `{self.course_name}`\n"
                f"- **Year:** `{year_name}`\n\n"
                f"Your unique verification code is:\n**`{code}`**\n\n"
                f"Use `/verify {code}` to complete your verification and receive your roles!"
            ),
            view=None
        )

class YearSelectView(discord.ui.View):
    def __init__(self, course_role_id: int, course_name: str):
        super().__init__(timeout=180)
        self.add_item(YearSelect(course_role_id, course_name))

class CourseSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="AIML", description="AI & Machine Learning", value="1533694023863242831"),
            discord.SelectOption(label="AIDS", description="AI & Data Science", value="1533694023871500339"),
            discord.SelectOption(label="CSE", description="Computer Science & Engineering", value="1533694023863242830"),
            discord.SelectOption(label="CYBER SECURITY", description="Cyber Security", value="1533694023850655793"),
            discord.SelectOption(label="IT", description="Information Technology", value="1533694023863242827"),
            discord.SelectOption(label="ECE", description="Electronics & Communication", value="1533694023863242822"),
            discord.SelectOption(label="EEE", description="Electrical & Electronics", value="1533694023863242828"),
            discord.SelectOption(label="CSBS", description="Computer Science & Business", value="1533694023863242823"),
            discord.SelectOption(label="MECHATRONICS", description="Mechatronics Engineering", value="1533694023863242825"),
            discord.SelectOption(label="MECHANICAL", description="Mechanical Engineering", value="1533694023863242826"),
            discord.SelectOption(label="CIVIL", description="Civil Engineering", value="1533694023863242829"),
            discord.SelectOption(label="BIO MEDICAL", description="Biomedical Engineering", value="1533694023863242824"),
        ]
        super().__init__(placeholder="Step 1: Select your course...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_role_id = int(self.values[0])
        course_name = [opt.label for opt in self.options if opt.value == self.values[0]][0]

        await interaction.response.edit_message(
            content=f"**Course Selected:** `{course_name}`\n\n**Step 2 of 2:** Select your Year of study from the dropdown below:",
            view=YearSelectView(selected_role_id, course_name)
        )

class CourseSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(CourseSelect())

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "**Step 1 of 2:** Select your course/department from the dropdown below:",
                    view=CourseSelectView(),
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "**Step 1 of 2:** Select your course/department from the dropdown below:",
                    view=CourseSelectView(),
                    ephemeral=True
                )
        except discord.errors.NotFound:
            pass  # Interaction expired (>3s Discord timeout or old panel click)
        except Exception as e:
            print(f"[Verify Button Error]: {e}")

def format_giveaway_message(prize: str, end_timestamp: int, winners: int, host_mention: str, participant_count: int) -> str:
    return (
        f" **GIVEAWAY** \n"
        f"**Prize:** {prize}\n"
        f"**Ends:** <t:{end_timestamp}:R> (<t:{end_timestamp}:f>)\n"
        f"**Winners:** {winners}\n"
        f"**Participants:** {participant_count}\n"
        f"Hosted by: {host_mention}"
    )

giveaway_last_edit = {}

async def _debounced_giveaway_edit(msg_id: int, channel_id: int):
    # Wait 6 seconds to batch multiple rapid clicks together and strictly stay within Discord's edit rate limits
    await asyncio.sleep(6.0) 
    
    # Ensure at least 6.0s has passed since the last actual edit on this message
    now = time.time()
    last_time = giveaway_last_edit.get(msg_id, 0)
    if now - last_time < 6.0:
        await asyncio.sleep(6.0 - (now - last_time))

    giveaway = active_giveaways.get(msg_id)
    if not giveaway:
        giveaway_update_tasks.pop(msg_id, None)
        return

    try:
        # Use get_channel instead of fetch_channel to use the bot's cache
        channel = client.get_channel(channel_id) 
        if channel:
            new_content = format_giveaway_message(
                prize=giveaway["prize"],
                end_timestamp=giveaway["end_timestamp"],
                winners=giveaway["winners"],
                host_mention=giveaway["host_mention"],
                participant_count=len(giveaway["participants"])
            )
            
            # Use a partial message to edit directly WITHOUT fetching it first (Saves 1 API call per edit)
            msg = channel.get_partial_message(msg_id)
            await msg.edit(content=new_content)
            giveaway_last_edit[msg_id] = time.time()
            
    except discord.HTTPException as e:
        if e.status == 429:
            retry_after = getattr(e, "retry_after", 6.0) or 6.0
            print(f"[Giveaway Edit Rate Limited]: Waiting {retry_after}s")
            # If rate limited, drop the edit this round; subsequent clicks will update later
        else:
            print(f"[Giveaway Edit HTTPException]: {e}")
    except Exception as e:
        print(f"[Giveaway debounced edit error]: {e}")
    finally:
        giveaway_update_tasks.pop(msg_id, None)


def schedule_giveaway_update(msg_id: int, channel_id: int):
    existing_task = giveaway_update_tasks.get(msg_id)
    if not existing_task or existing_task.done():
        task = asyncio.create_task(_debounced_giveaway_edit(msg_id, channel_id))
        giveaway_update_tasks[msg_id] = task

class GiveawayView(discord.ui.View):
    def __init__(self, msg_id: int | None = None):
        super().__init__(timeout=None)
        self.msg_id = msg_id

    @discord.ui.button(label=" Join", style=discord.ButtonStyle.green, custom_id="giveaway_join")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = self.msg_id or (interaction.message.id if interaction.message else None)
        giveaway = active_giveaways.get(msg_id)
        if not giveaway:
            return await interaction.response.send_message("This giveaway has already ended!", ephemeral=True)
        if interaction.user.id in giveaway["participants"]:
            return await interaction.response.send_message("You are already in the giveaway! ", ephemeral=True)

        giveaway["participants"].append(interaction.user.id)
        save_giveaways()
        count = len(giveaway["participants"])
        channel_id = giveaway.get("channel_id") or (interaction.channel_id if interaction.channel else None)
        if msg_id and channel_id:
            schedule_giveaway_update(msg_id, channel_id)

        await interaction.response.send_message(f"You joined the giveaway! Good luck (Total participants: **{count}**)", ephemeral=True)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.red, custom_id="giveaway_leave")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = self.msg_id or (interaction.message.id if interaction.message else None)
        giveaway = active_giveaways.get(msg_id)
        if not giveaway:
            return await interaction.response.send_message("This giveaway has already ended!", ephemeral=True)
        if interaction.user.id not in giveaway["participants"]:
            return await interaction.response.send_message("You aren't in this giveaway!", ephemeral=True)

        giveaway["participants"].remove(interaction.user.id)
        save_giveaways()
        count = len(giveaway["participants"])
        channel_id = giveaway.get("channel_id") or (interaction.channel_id if interaction.channel else None)
        if msg_id and channel_id:
            schedule_giveaway_update(msg_id, channel_id)

        await interaction.response.send_message(f"You have left the giveaway. (Total participants: **{count}**)", ephemeral=True)

class TicketDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Giveaway claim", description="Claim your giveaway prizes", value="giveaway_claim"),
            discord.SelectOption(label="General ticket", description="General support and questions", value="general_ticket"),
            discord.SelectOption(label="Suggestion", description="Share your feedback or ideas", value="suggestion"),
            discord.SelectOption(label="Report", description="Report a user or an issue", value="report"),
        ]
        super().__init__(
            placeholder="Select an option to open a ticket",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_dropdown_menu"
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

        user_id = interaction.user.id
        guild_cfg = SERVERS.get(guild.id, {})

        # Check if user already has an active open ticket
        open_ticket = get_user_open_ticket(guild.id, user_id)
        if open_ticket:
            existing_channel = guild.get_channel(open_ticket["channel_id"])
            if existing_channel:
                t_num = open_ticket.get("ticket_number", 1)
                clean_user = re.sub(r'[^a-zA-Z0-9_-]', '', interaction.user.name).lower() or f"user{user_id}"
                return await interaction.response.send_message(
                    f"You have already claimed a ticket {existing_channel.mention} (#ticket-{clean_user}-{t_num}).",
                    ephemeral=True
                )
            else:
                # Channel was deleted on discord, mark as closed in DB
                close_ticket_record(open_ticket["channel_id"])

        selected_type = self.values[0]
        label_map = {
            "giveaway_claim": "giveaway-claim",
            "general_ticket": "general-ticket",
            "suggestion": "suggestion",
            "report": "report"
        }
        ticket_name = label_map.get(selected_type, selected_type)

        # Ticket Category
        ticket_cat_id = guild_cfg.get("channels", {}).get("ticket_category")
        category = guild.get_channel(ticket_cat_id) if ticket_cat_id else None
        if not category and interaction.channel:
            category = interaction.channel.category

        # Staff roles: ONLY co_owner and mod (no old staff IDs)
        roles_cfg = guild_cfg.get("roles", {})
        co_owner_id = roles_cfg.get("co_owner")
        mod_id = roles_cfg.get("mod")
        co_owner_role = guild.get_role(co_owner_id) if co_owner_id else None
        mod_role = guild.get_role(mod_id) if mod_id else None
        staff_role_ids = {co_owner_id, mod_id} - {None}

        # Permissions: strictly hidden from @everyone & all non-staff roles,
        # visible and full text/media permissions granted to ticket opener and staff
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
                send_messages=False,
                read_message_history=False
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                send_messages_in_threads=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                add_reactions=True,
                use_external_emojis=True
            )
        }

        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                manage_channels=True,
                manage_messages=True
            )

        if co_owner_role:
            overwrites[co_owner_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                manage_messages=True
            )
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                manage_messages=True
            )

        # Note: overwrites already explicitly denies @everyone (guild.default_role),
        # so all other regular roles are automatically hidden without needing 50+ individual role overwrites!

        # Count of tickets this user has claimed
        ticket_count = get_user_ticket_count(guild.id, user_id) + 1
        clean_user = re.sub(r'[^a-zA-Z0-9_-]', '', interaction.user.name).lower() or f"user{user_id}"
        channel_name = f"ticket-{clean_user}-{ticket_count}"

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ticket #{ticket_count} for {interaction.user} (ID: {interaction.user.id})"
            )
        except discord.Forbidden:
            return await interaction.response.send_message("Bot is missing permissions to create ticket channels.", ephemeral=True)
        except Exception as e:
            return await interaction.response.send_message(f"Failed to create ticket: {e}", ephemeral=True)

        # Save to DB
        create_ticket_record(guild.id, user_id, ticket_channel.id, ticket_count, selected_type)

        await interaction.response.send_message(f"Ticket created: {ticket_channel.mention}", ephemeral=True)

        # Pings: ONLY co_owner and mod
        staff_pings = [r.mention for r in [co_owner_role, mod_role] if r]
        ping_string = " ".join(staff_pings) if staff_pings else "Staff"

        embed = discord.Embed(
            title=f" Ticket #{ticket_count} - {ticket_name.replace('-', ' ').title()}",
            description=f"Welcome {interaction.user.mention} to your ticket.\n{ping_string} will assist you shortly. Please state your issue or claim below.",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Only Co-Owners and Mods can close this ticket.")
        await ticket_channel.send(content=f"{interaction.user.mention} {ping_string}", embed=embed, view=TicketCloseView())


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="ticket_close_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_mod(interaction.user):
            return await interaction.response.send_message("Only mods and co-owners can close tickets.", ephemeral=True)

        await interaction.response.defer()

        # Update DB record
        close_ticket_record(interaction.channel.id)

        # Get ticket record to identify user
        ticket_info = get_ticket_by_channel(interaction.channel.id)
        user_id = ticket_info.get("user_id") if ticket_info else None

        # Hide ticket from user (remove view permissions for user)
        if user_id:
            user_member = interaction.guild.get_member(user_id)
            if user_member:
                try:
                    await interaction.channel.set_permissions(
                        user_member,
                        view_channel=False,
                        send_messages=False
                    )
                except Exception as e:
                    print(f"[Error removing user permission on ticket close]: {e}")

        # Hide from regular member roles as well
        roles_cfg = SERVERS.get(interaction.guild.id, {}).get("roles", {})
        for r_key in ["member", "verified", "unverified"]:
            r_id = roles_cfg.get(r_key)
            if r_id:
                r_obj = interaction.guild.get_role(r_id)
                if r_obj:
                    try:
                        await interaction.channel.set_permissions(r_obj, view_channel=False)
                    except Exception:
                        pass

        # Try to rename channel to closed-ticket-...
        try:
            current_name = interaction.channel.name
            if not current_name.startswith("closed-"):
                await interaction.channel.edit(name=f"closed-{current_name}")
        except Exception:
            pass

        embed = discord.Embed(
            title="Ticket Closed",
            description=f"Ticket closed by {interaction.user.mention}.\n\nThe user can no longer view this channel. Co-Owners and Mods can still review this ticket.",
            color=discord.Color.dark_grey()
        )
        await interaction.followup.send(embed=embed, view=TicketClosedView())


class TicketClosedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.red,custom_id="ticket_delete_button")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_mod(interaction.user):
            return await interaction.response.send_message("Only mods and co-owners can delete tickets.", ephemeral=True)

        await interaction.response.send_message(" Deleting ticket in 5 seconds...")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except Exception as e:
            print(f"[Error deleting ticket channel]: {e}")


async def auto_end_game(channel_id: int):
    await asyncio.sleep(600)
    if channel_id in active_games:
        number = active_games.pop(channel_id, None)
        game_tasks.pop(channel_id, None)
        ch = client.get_channel(channel_id)
        if ch:
            try:
                await ch.send(f" Time's up! Nobody guessed the number. It was **{number}**.")
            except Exception:
                pass

async def next_turn(channel_id: int, channel):
    game = active_tod_game.get(channel_id)
    if not game or len(game.get("players", [])) < 2:
        active_tod_game.pop(channel_id, None)
        try:
            await channel.send("Not enough players left. Truth or Dare game ended!")
        except Exception:
            pass
        return

    players = game["players"]
    curr_idx = game.get("current_index", 0)
    next_idx = (curr_idx + 1) % len(players)
    game["current_index"] = next_idx

    asker_id = players[curr_idx]
    challenged_id = players[next_idx]
    game["current_challenged"] = challenged_id

    guild = channel.guild if hasattr(channel, "guild") else None
    asker = guild.get_member(asker_id) if guild else None
    challenged = guild.get_member(challenged_id) if guild else None

    asker_mention = asker.mention if asker else f"<@{asker_id}>"
    challenged_mention = challenged.mention if challenged else f"<@{challenged_id}>"

    try:
        await channel.send(
            f" {asker_mention} challenges {challenged_mention}!\n"
            f"{challenged_mention}, pick your fate ",
            view=TODGameView(channel_id)
        )
    except Exception as e:
        print(f"[TOD Error]: {e}")

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())


class TODDoneView(discord.ui.View):
    def __init__(self, channel_id, player_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.player_id = player_id

    @discord.ui.button(label="Done", style=discord.ButtonStyle.green)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            return await interaction.response.send_message("Only the challenged person can click this!", ephemeral=True)
        await interaction.response.edit_message(content="Moving to next turn...", view=None)
        await next_turn(self.channel_id, interaction.channel)

class TODLobbyView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=60)
        self.channel_id = channel_id

    @discord.ui.button(label="Join ", style=discord.ButtonStyle.green)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = active_tod_lobby.get(self.channel_id)
        if not lobby:
            return await interaction.response.send_message("Lobby no longer exists.", ephemeral=True)
        if interaction.user.id in lobby["players"]:
            return await interaction.response.send_message("You're already in!", ephemeral=True)

        lobby["players"].append(interaction.user.id)
        await interaction.response.send_message(f"{interaction.user.mention} joined the lobby!", ephemeral=False)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.red)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = active_tod_lobby.get(self.channel_id)
        if not lobby:
            return await interaction.response.send_message("Lobby no longer exists.", ephemeral=True)
        if interaction.user.id not in lobby["players"]:
            return await interaction.response.send_message("You're not in the lobby.", ephemeral=True)

        lobby["players"].remove(interaction.user.id)
        await interaction.response.send_message(f"{interaction.user.mention} left the lobby.", ephemeral=False)

    async def on_timeout(self):
        lobby = active_tod_lobby.get(self.channel_id)
        if lobby and len(lobby["players"]) < 2:
            active_tod_lobby.pop(self.channel_id, None)

class TODGameView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="Truth", style=discord.ButtonStyle.blurple)
    async def truth(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = active_tod_game.get(self.channel_id)
        if not game:
            return await interaction.response.send_message("No active game.", ephemeral=True)
        if interaction.user.id != game["current_challenged"]:
            return await interaction.response.send_message("It's not your turn!", ephemeral=True)

        question = random.choice(TRUTHS)
        game["current_challenged"] = None
        await interaction.response.send_message(f"{interaction.user.mention} chose **TRUTH!**\n{question}")
        await interaction.followup.send(
            "Answer the question, then click **Done** when you're finished!",
            view=TODDoneView(self.channel_id, interaction.user.id),
            ephemeral=True
        )

    @discord.ui.button(label="Dare", style=discord.ButtonStyle.red)
    async def dare(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = active_tod_game.get(self.channel_id)
        if not game:
            return await interaction.response.send_message("No active game.", ephemeral=True)
        if interaction.user.id != game["current_challenged"]:
            return await interaction.response.send_message("It's not your turn!", ephemeral=True)

        dare_text = random.choice(DARES)
        game["current_challenged"] = None
        await interaction.response.send_message(f"{interaction.user.mention} chose **DARE!**\n{dare_text}")
        await interaction.followup.send(
            "Complete the dare, then click **Done** when you're finished!",
            view=TODDoneView(self.channel_id, interaction.user.id),
            ephemeral=True
        )

    @discord.ui.button(label="Leave Game", style=discord.ButtonStyle.grey)
    async def leave_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = active_tod_game.get(self.channel_id)
        if not game:
            return await interaction.response.send_message("No active game.", ephemeral=True)
        if interaction.user.id not in game["players"]:
            return await interaction.response.send_message("You're not in this game.", ephemeral=True)

        game["players"].remove(interaction.user.id)
        await interaction.response.send_message(f"{interaction.user.mention} left the game.")

        if len(game["players"]) < 2:
            active_tod_game.pop(self.channel_id, None)
            await interaction.channel.send("Not enough players left. Game ended!")
            return

        if interaction.user.id == game["current_challenged"]:
            game["current_challenged"] = None
            await next_turn(self.channel_id, interaction.channel)

# ==============================================================================
# HELPER ASYNC FUNCTIONS
# ==============================================================================
def parse_duration(duration_str):
    duration_str = duration_str.lower().strip()
    match = re.match(r'^(\d+)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|day|days?)$', duration_str)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith('s'): return amount
    elif unit.startswith('m'): return amount * 60
    elif unit.startswith('h'): return amount * 3600
    elif unit.startswith('d'): return amount * 86400
    return None

async def end_giveaway(msg_id, total_seconds):
    global fusei
    await asyncio.sleep(total_seconds)
    update_task = giveaway_update_tasks.pop(msg_id, None)
    if update_task and not update_task.done():
        update_task.cancel()

    giveaway = active_giveaways.pop(msg_id, None)
    save_giveaways()
    if not giveaway:
        return

    try:
        channel = client.get_channel(giveaway["channel_id"]) or await client.fetch_channel(giveaway["channel_id"])
    except Exception:
        return

    participants = giveaway["participants"]
    prize = giveaway["prize"]
    winner_count = giveaway["winners"]
    total_participants = len(participants)

    if not participants:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(
                content=(
                    f" **GIVEAWAY ENDED** \n"
                    f"**Prize:** {prize}\n"
                    f"**Participants:** 0\n"
                    f"**Winners:** None (Nobody entered)\n"
                    f"**Status:** Ended!"
                ),
                view=None
            )
        except Exception:
            pass
        await channel.send(f"Nobody entered the giveaway for **{prize}**... Guess I'll keep it ‍")
        return

    chosen_winners = []
    if fusei is not None:
        chosen_winners.append(fusei)
        if fusei in participants:
            participants.remove(fusei)
        print(f"[SUCCESS] Giveaway rigged successfully for {fusei}. Resetting.")
        fusei = None

    remaining_spots = winner_count - len(chosen_winners)
    if remaining_spots > 0 and participants:
        actual_spots = min(remaining_spots, len(participants))
        chosen_winners.extend(random.sample(participants, actual_spots))

    winner_mentions = ", ".join(f"<@{w}>" for w in chosen_winners)
    try:
        msg = await channel.fetch_message(msg_id)
        await msg.edit(
            content=(
                f" **GIVEAWAY ENDED** \n"
                f"**Prize:** {prize}\n"
                f"**Participants:** {total_participants}\n"
                f"**Winners:** {winner_mentions}\n"
                f"**Status:** Ended!"
            ),
            view=None
        )
    except discord.HTTPException as e:
        if e.status == 429:
            retry_after = getattr(e, "retry_after", 3.0) or 3.0
            await asyncio.sleep(retry_after + 0.5)
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(
                    content=(
                        f" **GIVEAWAY ENDED** \n"
                        f"**Prize:** {prize}\n"
                        f"**Participants:** {total_participants}\n"
                        f"**Winners:** {winner_mentions}\n"
                        f"**Status:** Ended!"
                    ),
                    view=None
                )
            except Exception:
                pass
    except Exception:
        pass

    await channel.send(f" Congratulations {winner_mentions}! You won **{prize}**! Make a ticket in the support channel to claim it.")



async def auto_end_game(channel_id):
    await asyncio.sleep(120)
    if channel_id in active_games:
        number = active_games.pop(channel_id, None)
        game_tasks.pop(channel_id, None)
        if number is None:
            return
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            if channel:
                await channel.send(f"Time's up! The number was **{number}** . Nobody guessed it")
        except Exception:
            pass

async def next_turn(channel_id, channel):
    game = active_tod_game.get(channel_id)
    if not game:
        return
    if len(game["players"]) < 2:
        active_tod_game.pop(channel_id, None)
        try:
            await channel.send("Not enough players. Game ended!")
        except Exception:
            pass
        return

    game["current_index"] = (game["current_index"] + 1) % len(game["players"])
    asker_id = game["players"][game["current_index"]]
    others = [p for p in game["players"] if p != asker_id]
    if not others:
        return
    challenged_id = random.choice(others)
    game["current_challenged"] = challenged_id

    asker = channel.guild.get_member(asker_id) if channel.guild else None
    challenged = channel.guild.get_member(challenged_id) if channel.guild else None

    asker_mention = asker.mention if asker else f"<@{asker_id}>"
    challenged_mention = challenged.mention if challenged else f"<@{challenged_id}>"

    try:
        await channel.send(
            f"{asker_mention} challenges {challenged_mention}!\n"
            f"{challenged_mention}, pick your fate ",
            view=TODGameView(channel_id)
        )
    except Exception:
        pass

# ==============================================================================
# SLASH COMMANDS
# ==============================================================================
@client.tree.command(name="role", description="Add or remove a role from a user (Restricted to S4NTY/Owner)")
@app_commands.describe(action="Choose whether to add or remove the role")
@app_commands.choices(action=[
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove")
])
async def role_cmd(interaction: discord.Interaction, target: discord.Member, role: discord.Role, action: app_commands.Choice[str]):
    if not is_s4nty_or_owner(interaction.user):
        return await interaction.response.send_message("No permission. You must have the S4NTY or Owner role.", ephemeral=True)
    
    if role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("I cannot manage a role that is higher or equal to my own top role.", ephemeral=True)
        
    try:
        act = action.value
        if act == "add":
            if role in target.roles:
                return await interaction.response.send_message(f"{target.mention} already has the {role.mention} role!", ephemeral=True)
            await target.add_roles(role)
            await interaction.response.send_message(f"Added {role.mention} to {target.mention}.", ephemeral=True)
        elif act == "remove":
            if role not in target.roles:
                return await interaction.response.send_message(f"{target.mention} does not have the {role.mention} role!", ephemeral=True)
            await target.remove_roles(role)
            await interaction.response.send_message(f"Removed {role.mention} from {target.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I do not have permissions to manage that role.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

@client.tree.command(name="list_roles", description="List all server roles in order (from lowest to highest)")
async def list_roles(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)
    
    roles = interaction.guild.roles
    role_mentions = []
    for i, role in enumerate(roles):
        if role.name != "@everyone":
            role_mentions.append(f"{i}. {role.mention} ({role.id})")
        else:
            role_mentions.append(f"{i}. {role.name} ({role.id})")
            
    msg = "**Server Roles (Lowest to Highest):**\n" + "\n".join(role_mentions)
    if len(msg) <= 2000:
        await interaction.response.send_message(msg, ephemeral=True)
    else:
        await interaction.response.defer(ephemeral=True)
        chunks = [msg[i:i+1900] for i in range(0, len(msg), 1900)]
        for chunk in chunks:
            await interaction.followup.send(chunk, ephemeral=True)

@client.tree.command(name="giveaway_st", description="Start a giveaway")
async def giveaway_st(interaction: discord.Interaction, prize: str, duration: str, winners: int = 1):
    if not has_giveaway_role(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)

    total_seconds = parse_duration(duration)
    if not total_seconds:
        return await interaction.response.send_message("Invalid duration! Try formats like `10s`, `5m`, `1 hour`, or `2 days`.", ephemeral=True)

    end_timestamp = int(time.time()) + total_seconds
    host_mention = interaction.user.mention

    initial_content = format_giveaway_message(
        prize=prize,
        end_timestamp=end_timestamp,
        winners=winners,
        host_mention=host_mention,
        participant_count=0
    )

    await interaction.response.send_message(initial_content)

    msg = await interaction.original_response()
    active_giveaways[msg.id] = {
        "participants": [],
        "prize": prize,
        "winners": winners,
        "channel_id": interaction.channel.id,
        "host_mention": host_mention,
        "end_timestamp": end_timestamp
    }
    save_giveaways()

    await msg.edit(view=GiveawayView(msg.id))
    client.loop.create_task(end_giveaway(msg.id, total_seconds))

@client.tree.command(name="verify", description="Verify membership with code")
async def verify(interaction: discord.Interaction, code: str):
    await interaction.response.defer(ephemeral=True)
    clean_code = code.strip().replace("`", "").replace("'", "").replace('"', "").upper()
    if not check_verify_code(interaction.user.id, clean_code):
        return await interaction.followup.send(
            f" Wrong verification code (`{clean_code}`). Click the **Verify** button to get a new code.",
            ephemeral=True
        )

    success, msg = await complete_verification(interaction.guild, interaction.user)
    if success:
        clear_verify_code(interaction.user.id, clean_code)
    await interaction.followup.send(msg, ephemeral=True)

@client.tree.command(name="setup_verify", description="Setup verification panel in the verification channel")
async def setup_verify(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)

    guild_cfg = SERVERS.get(interaction.guild.id, {})
    verify_channel_id = guild_cfg.get("channels", {}).get("verify")
    target_channel = (interaction.guild.get_channel(verify_channel_id) if verify_channel_id else None) or interaction.channel

    embed = discord.Embed(
        title="Member Verification",
        description="Click the **Verify** button below to select your department/course and receive your verification code.\n\nAfter getting your code, enter it using `/verify <code>` to access the server!",
        color=discord.Color.green()
    )

    await target_channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message(f"Verification panel created in {target_channel.mention}.", ephemeral=True)

@client.tree.command(name="setup_ticket_panel", description="Setup ticket creation panel in the ticket panel channel")
async def setup_ticket_panel(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)

    guild_cfg = SERVERS.get(interaction.guild.id, {})
    ticket_channel_id = guild_cfg.get("channels", {}).get("ticket_panel")
    target_channel = (interaction.guild.get_channel(ticket_channel_id) if ticket_channel_id else None) or interaction.channel

    embed = discord.Embed(
        title="Support & Claim Tickets",
        description="Select an option from the dropdown menu below to open a ticket.\n\n **Note:** You can only have one active ticket at a time. Only Co-Owners and Mods will assist you.",
        color=discord.Color.blue()
    )

    await target_channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message(f"Ticket panel created in {target_channel.mention}.", ephemeral=True)

@client.tree.command(name="setup_tickets", description="Setup ticket creation panel (alias)")
async def setup_tickets(interaction: discord.Interaction):
    await setup_ticket_panel.callback(interaction)

@client.tree.command(name="close", description="Close current ticket channel")
async def close_ticket_command(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Only mods and co-owners can close tickets.", ephemeral=True)

    # Check if channel is a ticket channel
    ticket_info = get_ticket_by_channel(interaction.channel.id)
    if not ticket_info and not interaction.channel.name.startswith(("ticket-", "closed-ticket-", "closed-")):
        return await interaction.response.send_message("This command can only be used in ticket channels.", ephemeral=True)

    close_ticket_record(interaction.channel.id)
    user_id = ticket_info.get("user_id") if ticket_info else None
    if user_id:
        user_member = interaction.guild.get_member(user_id)
        if user_member:
            try:
                await interaction.channel.set_permissions(user_member, view_channel=False, send_messages=False)
            except Exception as e:
                print(f"[Error removing user permission in close command]: {e}")

    roles_cfg = SERVERS.get(interaction.guild.id, {}).get("roles", {})
    for r_key in ["member", "verified", "unverified"]:
        r_id = roles_cfg.get(r_key)
        if r_id:
            r_obj = interaction.guild.get_role(r_id)
            if r_obj:
                try:
                    await interaction.channel.set_permissions(r_obj, view_channel=False)
                except Exception:
                    pass

    try:
        current_name = interaction.channel.name
        if not current_name.startswith("closed-"):
            await interaction.channel.edit(name=f"closed-{current_name}")
    except Exception:
        pass

    embed = discord.Embed(
        title=" Ticket Closed",
        description=f"Ticket closed by {interaction.user.mention}.\n\nThe user can no longer view this channel. Co-Owners and Mods can still review this ticket.",
        color=discord.Color.dark_grey()
    )
    await interaction.response.send_message(embed=embed, view=TicketClosedView())


@client.tree.command(name="tod_start", description="Start a Truth or Dare lobby")
async def tod_start(interaction: discord.Interaction):
    guild_tod_id = get_channel_id(interaction.guild.id, "tod")
    if interaction.channel.id != guild_tod_id and interaction.channel.id not in ALL_TOD_CHANNELS:
        return await interaction.response.send_message("This can only be used in the TOD channel!", ephemeral=True)

    if interaction.channel.id in active_tod_lobby or interaction.channel.id in active_tod_game:
        return await interaction.response.send_message("A TOD lobby or game is already running!", ephemeral=True)

    active_tod_lobby[interaction.channel.id] = {
        "players": [interaction.user.id],
        "host": interaction.user.id
    }

    await interaction.response.send_message(
        f"**Truth or Dare Lobby!**\n"
        f"Started by {interaction.user.mention}\n"
        f"Click **Join** to enter! Lobby closes in 60 seconds.\n"
        f"Host can use `/tod_begin` to start early.",
        view=TODLobbyView(interaction.channel.id)
    )

@client.tree.command(name="tod_begin", description="Begin the TOD game")
async def tod_begin(interaction: discord.Interaction):
    lobby = active_tod_lobby.get(interaction.channel.id)
    if not lobby:
        return await interaction.response.send_message("No active lobby.", ephemeral=True)
    if interaction.user.id != lobby["host"]:
        return await interaction.response.send_message("Only the host can begin!", ephemeral=True)
    if len(lobby["players"]) < 2:
        return await interaction.response.send_message("Need at least 2 players to start!", ephemeral=True)

    players = lobby["players"].copy()
    random.shuffle(players)
    active_tod_lobby.pop(interaction.channel.id, None)

    asker_id = players[0]
    challenged_id = players[1]

    active_tod_game[interaction.channel.id] = {
        "players": players,
        "current_index": 0,
        "current_challenged": challenged_id
    }

    asker = interaction.guild.get_member(asker_id)
    challenged = interaction.guild.get_member(challenged_id)

    asker_mention = asker.mention if asker else f"<@{asker_id}>"
    challenged_mention = challenged.mention if challenged else f"<@{challenged_id}>"

    await interaction.response.send_message(f"**Game started with {len(players)} players!**")
    await interaction.channel.send(
        f"{asker_mention} challenges {challenged_mention}!\n"
        f"{challenged_mention}, pick your fate ",
        view=TODGameView(interaction.channel.id)
    )

@client.tree.command(name="tod_end", description="End Truth or Dare game")
async def tod_end(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)

    if interaction.channel.id not in active_tod_game and interaction.channel.id not in active_tod_lobby:
        return await interaction.response.send_message("No active TOD to end.", ephemeral=True)

    active_tod_game.pop(interaction.channel.id, None)
    active_tod_lobby.pop(interaction.channel.id, None)
    await interaction.response.send_message("TOD game ended.")

@client.tree.command(name="start_guessing_game", description="Start number game")
async def start_guessing_game(interaction: discord.Interaction):
    guild_game_id = get_channel_id(interaction.guild.id, "guess_the_num")
    if interaction.channel.id != guild_game_id and interaction.channel.id not in ALL_GAME_CHANNELS:
        return await interaction.response.send_message("This game can only be played in the games channel!", ephemeral=True)

    if interaction.channel.id in active_games:
        return await interaction.response.send_message("A game is already running!", ephemeral=True)

    number = random.randint(1, 200)
    active_games[interaction.channel.id] = number
    task = asyncio.create_task(auto_end_game(interaction.channel.id))
    game_tasks[interaction.channel.id] = task

    await interaction.response.send_message("Game started!", ephemeral=True)
    await interaction.channel.send(" Guess the number game just started. `Type a number between 1-200`")
    print(f"----------------------GAME STARTED------------------------\n                       Number: {number}")

@client.tree.command(name="end_guessing_game", description="End the guessing game")
async def end_guessing_game(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Only moderators can end the game!", ephemeral=True)

    channel_id = interaction.channel.id
    if channel_id not in active_games:
        return await interaction.response.send_message("No active game to end", ephemeral=True)

    number = active_games.pop(channel_id)
    task = game_tasks.pop(channel_id, None)
    if task:
        task.cancel()

    await interaction.response.send_message(f"Game ended by {interaction.user.mention}. The number was **{number}**")

@client.tree.command(name="purgeall", description="Delete recent messages (up to 1,000 messages)")
async def purgeall(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_messages and not is_mod(interaction.user):
        return await interaction.response.send_message("Missing manage messages permission", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    total_deleted = 0
    max_batches = 10  # Safe cap: 10 batches of 100 = up to 1000 messages
    for _ in range(max_batches):
        try:
            deleted = await interaction.channel.purge(limit=100)
            total_deleted += len(deleted)
            if len(deleted) < 100:
                break
            await asyncio.sleep(1.2)  # Respect Discord bulk delete rate limits
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, "retry_after", 3.0) or 3.0
                await asyncio.sleep(retry_after + 0.5)
                continue
            else:
                break
        except Exception:
            break

    await interaction.followup.send(f"Purged {total_deleted} messages safely.", ephemeral=True)

@client.tree.command(name="purge", description="Delete messages")
async def purge(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages and not is_mod(interaction.user):
        return await interaction.response.send_message("Missing manage messages permission", ephemeral=True)

    if amount < 1 or amount > 100:
        return await interaction.response.send_message("Pick 1–100 messages", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages", ephemeral=True)

@client.tree.command(name="unspam", description="Stop spam")
async def unspam(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("You don’t have permission.", ephemeral=True)

    task = spam_tasks.pop(interaction.channel.id, None)
    if task:
        task.cancel()
        await interaction.response.send_message("Spam stopped.", ephemeral=True)
    else:
        await interaction.response.send_message("No spam running.", ephemeral=True)

@client.tree.command(name="spam", description="Spam a message safely")
async def spam(interaction: discord.Interaction, text: str, amount: int):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("You don’t have permission to use this command.", ephemeral=True)

    if str(OWNER_ID) in text:
        return await interaction.response.send_message("You cannot ping the owner in spam.", ephemeral=True)

    if amount < 1 or amount > 100:
        return await interaction.response.send_message("Pick 1–100", ephemeral=True)

    if interaction.channel.id in spam_tasks:
        return await interaction.response.send_message("Spam is already running in this channel. Use /unspam first.", ephemeral=True)

    await interaction.response.send_message("Spam started (rate-limit safe)", ephemeral=True)

    async def run_spam():
        try:
            count = 0
            while count < amount:
                try:
                    await interaction.channel.send(text)
                    count += 1
                except discord.HTTPException as e:
                    if e.status == 429:
                        retry_after = getattr(e, "retry_after", 3.0) or 3.0
                        print(f"[Spam Rate Limited]: Sleeping {retry_after}s")
                        await asyncio.sleep(retry_after + 1.0)
                        continue
                    else:
                        break
                except Exception:
                    break
                await asyncio.sleep(1.5)  # Safe delay to prevent channel rate limits
        except asyncio.CancelledError:
            pass
        finally:
            spam_tasks.pop(interaction.channel.id, None)

    task = asyncio.create_task(run_spam())
    spam_tasks[interaction.channel.id] = task

CUSTOM_EMOJIS = {
    "what_mr_bean": 1533753500335603782,
    "whoishe": 1533753429065863328,
    "mommywillitwork": 1533753357259505814,
    "tsainttuff": 1533753290121019464,
    "cry": 1533753249809563649,
    "IShowSpeedscared": 1533753180813398016,
    "IShowSpeedPoutFace": 1533752885547110440,
    "shhh": 1533752824813453312,
    "cryingwithmask": 1533752787131957312,
    "what": 1533752747478749244,
    "aniqtoohigh": 1533752686179254312,
    "cornball": 1533752668533686302,
    "Listening": 1533752612141400186,
    "bomboclat": 1533752586484584468,
    "Arcturus": 1533752500048494732,
}

def format_custom_emojis(text: str) -> str:
    def replacer(match):
        emoji_name = match.group(1)
        for name, eid in CUSTOM_EMOJIS.items():
            if name.lower() == emoji_name.lower():
                return f"<:{name}:{eid}>"
        for emoji in client.emojis:
            if emoji.name.lower() == emoji_name.lower():
                return str(emoji)
        return match.group(0)

    return re.sub(r'(?<!<):([a-zA-Z0-9_~]+):(?![\d]+>)', replacer, text)

@client.tree.command(name="iu", description="Owner only command")
async def iu(interaction: discord.Interaction, messeeji: str | None = None, image: discord.Attachment | None = None):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("Not allowed", ephemeral=True)

    if not messeeji and not image:
        return await interaction.response.send_message("Please provide a message or a file to send", ephemeral=True)

    formatted_msg = format_custom_emojis(messeeji) if messeeji else None
    file_to_send = None
    if image:
        file_to_send = await image.to_file()

    await interaction.channel.send(content=formatted_msg, file=file_to_send)
    await interaction.response.send_message("Okutta.", ephemeral=True)

@client.tree.command(name="unban", description="Unban a user")
async def unban(interaction: discord.Interaction, user_id: str):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)
    if not interaction.user.guild_permissions.ban_members and interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("Missing ban permissions", ephemeral=True)

    clean_id_str = re.sub(r'\D', '', user_id)
    if not clean_id_str:
        return await interaction.response.send_message("Invalid user ID or mention provided.", ephemeral=True)

    try:
        user = await client.fetch_user(int(clean_id_str))
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"Unbanned {user.mention}")
    except Exception as e:
        return await interaction.response.send_message(f"Failed to unban: {e}", ephemeral=True)

@client.tree.command(name="lock_invites", description="Lock invite creation to Co-Owner, Mod, and Special roles only")
async def lock_invites(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message(" No permission.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    roles_updated = 0
    channels_updated = 0

    roles_cfg = SERVERS.get(interaction.guild.id, {}).get("roles", {})
    allowed_role_ids = {
        roles_cfg.get("co_owner"),
        roles_cfg.get("mod"),
        roles_cfg.get("special"),
    } - {None}

    try:
        # 1. Update Guild Roles (Global server-level enforcement)
        for role in interaction.guild.roles:
            if role.permissions.administrator or role.is_bot_managed():
                continue

            try:
                should_allow = role.id in allowed_role_ids
                if role.permissions.create_instant_invite != should_allow:
                    perms = role.permissions
                    perms.update(create_instant_invite=should_allow)
                    await role.edit(permissions=perms)
                    roles_updated += 1
                    await asyncio.sleep(0.5)
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(getattr(e, "retry_after", 3.0) or 3.0)
            except Exception:
                pass

        # 2. Update Channel Overwrites for @everyone (category / channels)
        default_role = interaction.guild.default_role

        for channel in interaction.guild.channels:
            try:
                overwrite = channel.overwrites_for(default_role)
                if overwrite.create_instant_invite is not False:
                    overwrite.create_instant_invite = False
                    await channel.set_permissions(default_role, overwrite=overwrite)
                    channels_updated += 1
                    await asyncio.sleep(0.4)
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(getattr(e, "retry_after", 3.0) or 3.0)
            except Exception:
                pass

        await interaction.followup.send(
            f" **Server Invites Locked!**\nOnly Co-Owner, Mod, and Special roles can create invites.\nUpdated `{roles_updated}` roles and checked `{channels_updated}` channels.",
            ephemeral=True
        )
    except Exception as e:
        print(f"[Lock Invites Error]: {e}")
        await interaction.followup.send(f"Error locking invites: {e}", ephemeral=True)

@client.tree.command(name="timeout", description="Timeout a member")
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)
    if not interaction.user.guild_permissions.moderate_members and interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("Missing timeout permission.", ephemeral=True)

    if minutes <= 0 or minutes > 40320:
        return await interaction.response.send_message("Duration must be between 1 and 40,320 minutes (28 days max).", ephemeral=True)

    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await interaction.response.send_message(f"Timed out {member.mention} for {minutes} minute(s) | Reason: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message(f"Cannot timeout {member.mention}: Missing permissions or user has a higher role.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error timing out user: {e}", ephemeral=True)

@client.tree.command(name="untimeout", description="Remove timeout")
async def untimeout(interaction: discord.Interaction, member: discord.Member):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)

    try:
        await member.timeout(None)
        await interaction.response.send_message(f"Removed timeout from {member.mention}")
    except discord.Forbidden:
        await interaction.response.send_message(f"Cannot remove timeout from {member.mention}: Missing permissions.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error removing timeout: {e}", ephemeral=True)

@client.tree.command(name="kick", description="Kick a member")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)
    if not interaction.user.guild_permissions.kick_members and interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("Missing kick permission.", ephemeral=True)

    try:
        await member.kick(reason=reason)
        await interaction.response.send_message(f"Kicked {member.mention} | Reason: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message(f"Cannot kick {member.mention}: Missing permissions or user has a higher role.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error kicking user: {e}", ephemeral=True)

@client.tree.command(name="ban", description="Ban a member")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=False)
    if not is_mod(interaction.user):
        return await interaction.followup.send("No permission.", ephemeral=True)
    if not interaction.user.guild_permissions.ban_members and interaction.user.id != OWNER_ID:
        return await interaction.followup.send("Missing ban permission.", ephemeral=True)

    try:
        await member.ban(reason=reason)
        await interaction.followup.send(f"Banned {member.mention} | Reason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send(f"Cannot ban {member.mention}: Missing permissions or user has a higher role.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error banning user: {e}", ephemeral=True)

@client.tree.command(name="nick", description="Set a moderation nickname to a user")
async def nick(interaction: discord.Interaction, member: discord.Member, name: str):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_nicknames and interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("Missing manage nicknames permission.", ephemeral=True)

    try:
        await member.edit(nick=name)
        await interaction.response.send_message(f"Set nickname for {member.mention} to `{name}`")
    except discord.Forbidden:
        await interaction.response.send_message(f"Cannot change nickname for {member.mention}: Missing permissions or user has a higher role.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error changing nickname: {e}", ephemeral=True)

@client.tree.command(name="unnick", description="Remove the nickname of a user")
async def unnick(interaction: discord.Interaction, member: discord.Member):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_nicknames and interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("Missing manage nicknames permission.", ephemeral=True)

    try:
        await member.edit(nick=None)
        await interaction.response.send_message(f"Removed nickname for {member.mention}")
    except discord.Forbidden:
        await interaction.response.send_message(f"Cannot remove nickname for {member.mention}: Missing permissions or user has a higher role.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error removing nickname: {e}", ephemeral=True)

def format_dm_text(target_member: discord.Member, raw_text: str) -> str:
    formatted = format_custom_emojis(raw_text)
    if "{user}" in formatted:
        return formatted.replace("{user}", target_member.mention)
    if target_member.mention in formatted:
        return formatted
    return f"{target_member.mention} {formatted}"

@client.tree.command(name="dm", description="DM a specific member with a ping (Admin/Mod only)")
async def dm_member(interaction: discord.Interaction, member: discord.Member, message: str):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    if member.bot:
        return await interaction.response.send_message("Cannot send DMs to a bot.", ephemeral=True)

    dm_text = format_dm_text(member, message)
    try:
        await member.send(dm_text)
        await interaction.response.send_message(f"Successfully sent DM to {member.mention}!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"Could not DM {member.mention}. Their DMs might be closed/disabled.", ephemeral=True)
    except discord.HTTPException as e:
        if e.status == 429:
            await interaction.response.send_message(f" Bot is currently rate-limited on DMs. Try again in {getattr(e, 'retry_after', 5)}s.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Error sending DM: {e}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error sending DM: {e}", ephemeral=True)

async def _execute_dm_broadcast(interaction: discord.Interaction, message: str):
    guild = interaction.guild
    members_map = {m.id: m for m in guild.members if not m.bot}
    try:
        async for m in guild.fetch_members(limit=None):
            if not m.bot:
                members_map[m.id] = m
    except Exception:
        pass

    members = list(members_map.values())
    total = len(members)
    sent = 0
    failed = 0
    consecutive_fails = 0
    closed_dm_members = []
    processed_count = 0
    was_stopped = False

    status_msg = await interaction.followup.send(
        f"**Starting to DM {total} members...**\n"
        f"[░░░░░░░░░░] **0%** (`0/{total}`)\n"
        f"**Sent:** `0` | **Failed/Closed:** `0`\n"
        f"*(Use `/stop_dmall` at any time to cancel)*",
        ephemeral=True
    )

    last_update_time = time.time()
    try:
        for idx, member in enumerate(members, start=1):
            processed_count = idx
            dm_text = format_dm_text(member, message)
            try:
                await member.send(dm_text)
                sent += 1
                consecutive_fails = 0
            except discord.Forbidden:
                failed += 1
                closed_dm_members.append(member)
                consecutive_fails += 1
            except discord.HTTPException as e:
                failed += 1
                consecutive_fails += 1
                if e.status == 429:
                    retry_after = getattr(e, "retry_after", 5.0) or 5.0
                    print(f"[DM_ALL Rate Limited]: Pausing for {retry_after + 2.0}s")
                    await asyncio.sleep(retry_after + 2.0)
            except Exception:
                failed += 1
                consecutive_fails += 1

            # Circuit breaker: prevent Discord spam bot quarantine if massive consecutive blocks occur
            if consecutive_fails >= 25:
                print("[DM_ALL Safety Circuit Breaker]: Broadcast paused due to 25 consecutive failures.")
                break
            elif consecutive_fails > 0 and consecutive_fails % 8 == 0:
                print("[DM_ALL Safety Pause]: Pausing 8 seconds to cool down DM route.")
                await asyncio.sleep(8.0)

            # Live progress update throttled to every 4 seconds
            if idx % 5 == 0 or idx == total or (time.time() - last_update_time >= 4.0):
                percent = int((idx / total) * 100) if total > 0 else 100
                bar_len = 10
                filled = int((idx / total) * bar_len)
                bar = "█" * filled + "░" * (bar_len - filled)
                try:
                    await status_msg.edit(content=(
                        f"**DM in Progress...**\n\n"
                        f"[{bar}] **{percent}%** (`{idx}/{total}`)\n"
                        f"**Sent:** `{sent}` | **Failed/Closed:** `{failed}`\n"
                        f"Sending DMs safely (rate-limit protected)...\n"
                        f"*(Use `/stop_dmall` at any time to cancel)*"
                    ))
                    last_update_time = time.time()
                except Exception:
                    pass

            await asyncio.sleep(1.5)  # 1.5s safe delay to strictly respect Discord DM rate limits
    except asyncio.CancelledError:
        was_stopped = True
    finally:
        last_closed_dms[guild.id] = closed_dm_members
        active_dmall_tasks.pop(guild.id, None)

        if was_stopped:
            summary_text = (
                f"**DM Broadcast Stopped by Moderator!**\n\n"
                f"**Successfully Sent Before Stopping:** `{sent}` members\n"
                f"**Failed / Closed DMs:** `{failed}` members\n"
                f"**Total Members Processed:** `{processed_count}/{total}`\n\n"
                f"*(Use `/dm_disabled_members` to privately view everyone with DMs disabled)*"
            )
        else:
            summary_text = (
                f"**DM Broadcast Completed!**\n\n"
                f"**Successfully DM'd:** `{sent}` members\n"
                f"**Failed / Closed DMs:** `{failed}` members\n"
                f"**Total Members Processed:** `{processed_count}/{total}`\n\n"
                f"*(Use `/dm_disabled_members` to privately view everyone with DMs disabled)*"
            )

        try:
            await status_msg.edit(content=summary_text)
        except Exception:
            try:
                await interaction.followup.send(summary_text, ephemeral=True)
            except Exception:
                pass

async def handle_dm_broadcast(interaction: discord.Interaction, message: str):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message("This command must be used inside a server.", ephemeral=True)

    existing_task = active_dmall_tasks.get(guild.id)
    if existing_task and not existing_task.done():
        return await interaction.response.send_message(
            "A DM broadcast is already running in this server. Use `/stop_dmall` to stop it first.",
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    task = asyncio.create_task(_execute_dm_broadcast(interaction, message))
    active_dmall_tasks[guild.id] = task

@client.tree.command(name="dmall", description="DM all server members with a ping (Admin/Mod only)")
async def dmall(interaction: discord.Interaction, message: str):
    await handle_dm_broadcast(interaction, message)

@client.tree.command(name="stop_dmall", description="Stop any running /dmall broadcast in this server (Admin/Mod only)")
async def stop_dmall(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message("This command must be used inside a server.", ephemeral=True)

    task = active_dmall_tasks.get(guild.id)
    if task and not task.done():
        task.cancel()
        await interaction.response.send_message("**Stopping the active DM broadcast...** Progress so far will be saved.", ephemeral=True)
    else:
        await interaction.response.send_message("ℹNo active DM broadcast is currently running in this server.", ephemeral=True)

@client.tree.command(name="stopdmall", description="Stop any running /dmall broadcast in this server (Admin/Mod only)")
async def stopdmall(interaction: discord.Interaction):
    await stop_dmall(interaction)

@client.tree.command(name="dm_disabled_members", description="List members who have DMs closed/disabled (Admin/Mod only - private)")
async def dm_disabled_members(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    guild_closed = last_closed_dms.get(interaction.guild.id, [])
    if not guild_closed:
        return await interaction.response.send_message(
            "ℹNo closed DM records stored yet. Run `/dmall` to check all members, and the disabled users will be recorded here.",
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)

    header = f"**Members with DMs Disabled ({len(guild_closed)} total):**\n\n"
    current_chunk = header
    chunks = []

    for m in guild_closed:
        entry = f"{m.mention} (`{m.name}`)\n"
        if len(current_chunk) + len(entry) > 1900:
            chunks.append(current_chunk)
            current_chunk = entry
        else:
            current_chunk += entry

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await interaction.followup.send(chunk, ephemeral=True)

@client.tree.command(name="undmall", description="Delete the last message(s) the bot sent in DMs to all members (Admin/Mod only)")
async def undmall(interaction: discord.Interaction, count: int = 1):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        return await interaction.followup.send("This command must be used inside a server.", ephemeral=True)

    members_map = {m.id: m for m in guild.members if not m.bot}
    try:
        async for m in guild.fetch_members(limit=None):
            if not m.bot:
                members_map[m.id] = m
    except Exception:
        pass

    members = list(members_map.values())
    total = len(members)
    deleted_count = 0
    skipped_count = 0

    status_msg = await interaction.followup.send(
        f"**Starting to delete bot DMs from {total} members...**\n"
        f"[░░░░░░░░░░] **0%** (`0/{total}`)\n"
        f"**Deleted:** `0` messages",
        ephemeral=True
    )

    last_update_time = time.time()
    for idx, member in enumerate(members, start=1):
        try:
            dm_channel = member.dm_channel
            if not dm_channel:
                try:
                    dm_channel = await member.create_dm()
                except Exception:
                    dm_channel = None

            if dm_channel:
                bot_msgs_deleted = 0
                async for msg in dm_channel.history(limit=10):
                    if msg.author.id == client.user.id:
                        try:
                            await msg.delete()
                            bot_msgs_deleted += 1
                            deleted_count += 1
                            if bot_msgs_deleted >= count:
                                break
                            await asyncio.sleep(0.4)
                        except Exception:
                            pass
                if bot_msgs_deleted == 0:
                    skipped_count += 1
            else:
                skipped_count += 1
        except discord.Forbidden:
            skipped_count += 1
        except discord.HTTPException as e:
            skipped_count += 1
            if e.status == 429:
                retry_after = getattr(e, "retry_after", 5.0) or 5.0
                await asyncio.sleep(retry_after + 1.0)
        except Exception:
            skipped_count += 1

        # Live progress update throttled to every 4 seconds
        if idx % 5 == 0 or idx == total or (time.time() - last_update_time >= 4.0):
            percent = int((idx / total) * 100) if total > 0 else 100
            bar_len = 10
            filled = int((idx / total) * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            try:
                await status_msg.edit(content=(
                    f"**Deleting DMs in Progress...**\n\n"
                    f"[{bar}] **{percent}%** (`{idx}/{total}`)\n"
                    f"**Deleted:** `{deleted_count}` msgs |  **No DM/Skipped:** `{skipped_count}`\n"
                    f"Cleaning up messages safely..."
                ))
                last_update_time = time.time()
            except Exception:
                pass

        await asyncio.sleep(0.8)

    summary_text = (
        f"**DM Deletion Completed!**\n\n"
        f"**Successfully Deleted:** `{deleted_count}` messages\n"
        f"**Total Members Checked:** `{total}`"
    )

    try:
        await status_msg.edit(content=summary_text)
    except Exception:
        try:
            await interaction.followup.send(summary_text, ephemeral=True)
        except Exception:
            pass

@client.tree.command(name="undm", description="Delete the last bot message(s) in a specific member's DM (Admin/Mod only)")
async def undm(interaction: discord.Interaction, member: discord.Member, count: int = 1):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    if count < 1:
        return await interaction.response.send_message("Count must be at least 1.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    try:
        dm_channel = member.dm_channel or await member.create_dm()
        deleted = 0
        search_limit = min(max(20, count * 3), 100)
        async for msg in dm_channel.history(limit=search_limit):
            if msg.author.id == client.user.id:
                try:
                    await msg.delete()
                    deleted += 1
                    if deleted >= count:
                        break
                    await asyncio.sleep(0.3)
                except discord.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(getattr(e, "retry_after", 3.0) or 3.0)
                except Exception:
                    pass
        await interaction.followup.send(f"Deleted `{deleted}` bot message(s) from {member.mention}'s DM.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error deleting DM: {e}", ephemeral=True)

# ==============================================================================
# DISCORD EVENTS
# ==============================================================================
@client.event
async def on_member_join(member):
    # Auto-kick from server 1513910086924501162 if user joins server 1533694023850655784
    if member.guild.id == 1533694023850655784:
        try:
            target_guild = client.get_guild(1513910086924501162)
            if not target_guild:
                try:
                    target_guild = await client.fetch_guild(1513910086924501162)
                except Exception:
                    target_guild = None

            if target_guild:
                target_member = target_guild.get_member(member.id)
                if not target_member:
                    try:
                        target_member = await target_guild.fetch_member(member.id)
                    except Exception:
                        target_member = None

                if target_member:
                    await target_member.kick(reason="Joined server 1533694023850655784")
                    try:
                        log_channel = client.get_channel(1516496290601766932) or await client.fetch_channel(1516496290601766932)
                        if log_channel:
                            await log_channel.send(f"Kicked {member.mention} | No reason.")
                    except Exception as ch_err:
                        print(f"[Auto-Kick Channel Msg Error]: {ch_err}")
        except Exception as e:
            print(f"[Auto-Kick Error]: {e}")

    # Send welcome greeting
    try:
        welcome_id = get_channel_id(member.guild.id, "welcome")
        if welcome_id:
            channel = member.guild.get_channel(welcome_id) or await client.fetch_channel(welcome_id)
            if channel:
                message = random.choice(WELCOME_MESSAGES)
                await channel.send(message.replace("{user}", member.mention))
    except Exception as e:
        print(f"[Welcome Message Error]: {e}")

    # Assign unverified role
    try:
        unverified_id = get_role_id(member.guild.id, "unverified")
        if unverified_id:
            unverified = member.guild.get_role(unverified_id)
            if unverified:
                await member.add_roles(unverified)
    except Exception as e:
        print(f"[Unverified Role Assign Error]: {e}")

is_ready_initialized = False

async def sync_guild_permissions(guild: discord.Guild) -> tuple[int, int]:
    """
    Safely synchronizes and enforces channel and role permissions with rate-limit protection.
    Returns (roles_updated, channels_updated).
    """
    if not guild or guild.id not in SERVERS:
        return 0, 0

    config = SERVERS[guild.id]
    roles_cfg = config.get("roles", {})
    channels_cfg = config.get("channels", {})

    unverified = guild.get_role(roles_cfg.get("unverified")) if roles_cfg.get("unverified") else None
    verified = guild.get_role(roles_cfg.get("verified")) if roles_cfg.get("verified") else None
    member_role = guild.get_role(roles_cfg.get("member")) if roles_cfg.get("member") else None
    co_owner_role = guild.get_role(roles_cfg.get("co_owner")) if roles_cfg.get("co_owner") else None
    mod_role = guild.get_role(roles_cfg.get("mod")) if roles_cfg.get("mod") else None

    mod_channels = channels_cfg.get("mod_channels", set())
    verify_channel_id = channels_cfg.get("verify")
    ticket_cat_id = channels_cfg.get("ticket_category")
    ticket_panel_id = channels_cfg.get("ticket_panel")

    roles_updated = 0
    channels_updated = 0

    # 1. Enforce Invite Permissions on guild roles (only Co-Owner, Mod, and Special can create invites)
    allowed_invite_ids = {
        roles_cfg.get("co_owner"),
        roles_cfg.get("mod"),
        roles_cfg.get("special"),
    } - {None}

    for role in guild.roles:
        if role.permissions.administrator or role.is_bot_managed():
            continue
        try:
            should_allow = role.id in allowed_invite_ids
            if role.permissions.create_instant_invite != should_allow:
                perms = role.permissions
                perms.update(create_instant_invite=should_allow)
                await role.edit(permissions=perms)
                roles_updated += 1
                await asyncio.sleep(0.5)
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(getattr(e, "retry_after", 3.0) or 3.0)
        except Exception:
            pass

    async def safe_set_perm(target_channel, role_obj, allow_view: bool):
        nonlocal channels_updated
        if not role_obj:
            return
        current = target_channel.overwrites_for(role_obj)
        if current.view_channel == allow_view:
            return  # Overwrite already matches, skip unnecessary API call!
        try:
            await target_channel.set_permissions(role_obj, view_channel=allow_view)
            channels_updated += 1
            await asyncio.sleep(0.5)
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, "retry_after", 3.0) or 3.0
                await asyncio.sleep(retry_after)
                try:
                    await target_channel.set_permissions(role_obj, view_channel=allow_view)
                    channels_updated += 1
                except Exception:
                    pass
        except Exception:
            pass

    # 2. If ticket category exists, ensure @everyone and regular roles cannot see it
    if ticket_cat_id:
        ticket_cat = guild.get_channel(ticket_cat_id)
        if ticket_cat:
            await safe_set_perm(ticket_cat, guild.default_role, False)
            await safe_set_perm(ticket_cat, member_role, False)
            await safe_set_perm(ticket_cat, verified, False)
            await safe_set_perm(ticket_cat, unverified, False)
            if co_owner_role:
                await safe_set_perm(ticket_cat, co_owner_role, True)
            if mod_role:
                await safe_set_perm(ticket_cat, mod_role, True)

    for channel in guild.channels:
        if (ticket_cat_id and (channel.id == ticket_cat_id or getattr(channel, "category_id", None) == ticket_cat_id)) or channel.name.startswith(("ticket-", "closed-")):
            await safe_set_perm(channel, guild.default_role, False)
            await safe_set_perm(channel, member_role, False)
            await safe_set_perm(channel, verified, False)
            await safe_set_perm(channel, unverified, False)
            if co_owner_role:
                await safe_set_perm(channel, co_owner_role, True)
            if mod_role:
                await safe_set_perm(channel, mod_role, True)

            # Ensure ticket creator has viewing and messaging permissions if ticket is open
            ticket_info = get_ticket_by_channel(channel.id)
            if ticket_info and ticket_info.get("status") == "open":
                t_user_id = ticket_info.get("user_id")
                if t_user_id:
                    t_member = guild.get_member(t_user_id)
                    if t_member:
                        try:
                            t_current = channel.overwrites_for(t_member)
                            if not t_current.view_channel or not t_current.send_messages or not t_current.attach_files or not t_current.embed_links:
                                await channel.set_permissions(
                                    t_member,
                                    view_channel=True,
                                    send_messages=True,
                                    send_messages_in_threads=True,
                                    read_message_history=True,
                                    attach_files=True,
                                    embed_links=True,
                                    add_reactions=True,
                                    use_external_emojis=True
                                )
                                await asyncio.sleep(0.5)
                        except discord.HTTPException as e:
                            if e.status == 429:
                                await asyncio.sleep(getattr(e, "retry_after", 3.0) or 3.0)
                        except Exception:
                            pass
            continue

        if channel.id in mod_channels:
            await safe_set_perm(channel, member_role, False)
            await safe_set_perm(channel, verified, False)
            await safe_set_perm(channel, unverified, False)
            continue

        if verify_channel_id and channel.id == verify_channel_id:
            await safe_set_perm(channel, unverified, True)
            await safe_set_perm(channel, verified, True)
            await safe_set_perm(channel, member_role, True)
        elif ticket_panel_id and channel.id == ticket_panel_id:
            await safe_set_perm(channel, unverified, False)
            await safe_set_perm(channel, member_role, True)
            await safe_set_perm(channel, verified, True)
        else:
            await safe_set_perm(channel, unverified, False)
            await safe_set_perm(channel, member_role, True)
            await safe_set_perm(channel, verified, True)

    return roles_updated, channels_updated


@client.tree.command(name="sync_permissions", description="Enforce role and channel permissions safely (Admin/Mod only)")
async def sync_permissions_cmd(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message(" You do not have permission to use this command.", ephemeral=True)

    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message(" This command must be used inside a server.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    roles_upd, chans_upd = await sync_guild_permissions(guild)
    await interaction.followup.send(
        f" **Permission Synchronization Completed!**\n- Roles updated: `{roles_upd}`\n- Channel overwrites updated: `{chans_upd}`",
        ephemeral=True
    )


@client.tree.command(name="roleall", description="Assign up to 10 roles to all members with up to 10 exception roles (Mod/Owner only)")
async def roleall_cmd(
    interaction: discord.Interaction,
    role1: discord.Role | None = None,
    role2: discord.Role | None = None,
    role3: discord.Role | None = None,
    role4: discord.Role | None = None,
    role5: discord.Role | None = None,
    role6: discord.Role | None = None,
    role7: discord.Role | None = None,
    role8: discord.Role | None = None,
    role9: discord.Role | None = None,
    role10: discord.Role | None = None,
    skip1: discord.Role | None = None,
    skip2: discord.Role | None = None,
    skip3: discord.Role | None = None,
    skip4: discord.Role | None = None,
    skip5: discord.Role | None = None,
    skip6: discord.Role | None = None,
    skip7: discord.Role | None = None,
    skip8: discord.Role | None = None,
    skip9: discord.Role | None = None,
    skip10: discord.Role | None = None,
):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("[Error] You do not have permission to use this command.", ephemeral=True)

    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message("[Error] This command must be used inside a server.", ephemeral=True)

    direct_target_roles = [r for r in [role1, role2, role3, role4, role5, role6, role7, role8, role9, role10] if r is not None]
    direct_skip_roles = [r for r in [skip1, skip2, skip3, skip4, skip5, skip6, skip7, skip8, skip9, skip10] if r is not None]

    if direct_target_roles:
        await execute_roleall(interaction=interaction, selected_roles=direct_target_roles, exception_roles=direct_skip_roles)
    else:
        view = RoleAllView(invoker=interaction.user)
        await interaction.response.send_message(
            "[RoleAll Command]\n"
            "1. Select up to 10 target roles to assign to members.\n"
            "2. Select up to 10 exception roles to skip.\n"
            "3. Click [Start Assigning Roles] when ready.",
            view=view,
            ephemeral=True
        )


async def console_listener():
    global fusei
    await client.wait_until_ready()
    if not sys.stdin or not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
        return
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            command = line.strip().lower()
            if command.startswith("rig "):
                try:
                    target_id = int(command.split(" ")[1])
                    fusei = target_id
                    print(f"Next giveaway rigged for user ID: {fusei}")
                except ValueError:
                    print("Invalid. Format must be: rig <user_id>")
            elif command == "unrig":
                fusei = None
                print("Giveaway un-rigged. It's fair again.")
        except (EOFError, ValueError, OSError):
            break
        except Exception as e:
            print(f"[Console Listener Error]: {e}")
            await asyncio.sleep(5)



@client.event
async def on_ready():
    global is_ready_initialized
    if is_ready_initialized:
        print(f"[RECONNECT] Bot reconnected as {client.user}", flush=True)
        return
    is_ready_initialized = True
    
    try:
        for guild in client.guilds:
            print(f"\n--- Roles in {guild.name} (Lowest to Highest) ---")
            for i, r in enumerate(guild.roles):
                print(f"{i}. {r.name} (ID: {r.id})")
            print("--------------------------------------------------\n")
            roles_cfg = SERVERS.get(guild.id, {}).get("roles", {})
            owner_member = guild.get_member(OWNER_ID)
            
            # --- Auto Create and Manage s4nty Admin Role ---
            if guild.id == 1533694023850655784:
                mod_id = roles_cfg.get("mod")
                mod_role = guild.get_role(mod_id) if mod_id else None
                
                s4nty_role = discord.utils.get(guild.roles, name="s4nty")
                if not s4nty_role:
                    perms = discord.Permissions()
                    perms.administrator = True
                    try:
                        s4nty_role = await guild.create_role(name="s4nty", permissions=perms, color=discord.Color.red())
                        print(f"[AUTO-ROLE] Created admin 's4nty' role in {guild.name}")
                    except Exception as e:
                        print(f"[AUTO-ROLE ERROR] Error creating s4nty role: {e}")
                else:
                    if not s4nty_role.permissions.administrator:
                        perms = s4nty_role.permissions
                        perms.administrator = True
                        try:
                            await s4nty_role.edit(permissions=perms)
                        except:
                            pass
                
                if s4nty_role and mod_role:
                    try:
                        target_pos = min(guild.me.top_role.position - 1, mod_role.position + 1)
                        if s4nty_role.position != target_pos and target_pos > 0:
                            await s4nty_role.edit(position=target_pos)
                    except Exception as e:
                        print(f"[AUTO-ROLE ERROR] Could not move s4nty role: {e}")
                        
                if owner_member and s4nty_role and s4nty_role not in owner_member.roles:
                    try:
                        if s4nty_role < guild.me.top_role:
                            await owner_member.add_roles(s4nty_role)
                            print(f"[AUTO-OP] Granted 's4nty' admin role to {OWNER_ID}")
                    except Exception as e:
                        pass
            else:
                # Cleanup in other servers
                s4nty_role = discord.utils.get(guild.roles, name="s4nty")
                if s4nty_role:
                    try:
                        await s4nty_role.delete()
                        print(f"[CLEANUP] Deleted s4nty role in {guild.name}")
                    except:
                        pass
            # ----------------------------------------
            if owner_member:
                roles_to_add = []
                for role_key in ["co_owner", "mod", "special"]:
                    role_id = roles_cfg.get(role_key)
                    if role_id:
                        r = guild.get_role(role_id)
                        # Only add the role if the bot is actually high enough in the hierarchy to assign it
                        if r and r not in owner_member.roles and r < guild.me.top_role:
                            roles_to_add.append(r)
                if roles_to_add:
                    await owner_member.add_roles(*roles_to_add)
                    print(f"[AUTO-OP] Granted owner roles to {OWNER_ID} in {guild.name}")
    except Exception as e:
        print(f"[AUTO-OP ERROR] {e}")

    client.loop.create_task(console_listener())
    try:
        # Sync slash commands with pacing to prevent rate limits
        for guild_id, config in SERVERS.items():
            try:
                guild_obj = discord.Object(id=guild_id)
                client.tree.copy_global_to(guild=guild_obj)
                synced = await client.tree.sync(guild=guild_obj)
                print(f"[SYNC] Synced {len(synced)} slash commands to {config.get('name', 'Server')} ({guild_id})", flush=True)
            except Exception as sync_err:
                print(f"[SYNC Warning] Could not sync slash commands to {guild_id}: {sync_err}")
            await asyncio.sleep(1.0)

        # Register persistent views
        client.add_view(VerifyView())
        client.add_view(GiveawayView())
        client.add_view(TicketView())
        client.add_view(TicketCloseView())
        client.add_view(TicketClosedView())

        # Configure OwO channel on startup
        owo_channel = client.get_channel(1541306411911221248)
        if owo_channel:
            try:
                await owo_channel.edit(slowmode_delay=5)
                unverified_id = get_role_id(owo_channel.guild.id, "unverified")
                special_id = get_role_id(owo_channel.guild.id, "special")
                mod_id = get_role_id(owo_channel.guild.id, "mod")

                unverified_role = owo_channel.guild.get_role(unverified_id) if unverified_id else None
                special_role = owo_channel.guild.get_role(special_id) if special_id else None
                mod_role = owo_channel.guild.get_role(mod_id) if mod_id else None

                # Fallback search by name if not found in config
                for r in owo_channel.guild.roles:
                    if not unverified_role and "unverified" in r.name.lower():
                        unverified_role = r
                    if not special_role and "special" in r.name.lower():
                        special_role = r
                    if not mod_role and ("mod" in r.name.lower() or "moderator" in r.name.lower()):
                        mod_role = r
                
                # Reset everyone to allow chatting
                await owo_channel.set_permissions(owo_channel.guild.default_role, send_messages=True, view_channel=True)
                
                # Unverified cannot see channel
                if unverified_role:
                    await owo_channel.set_permissions(unverified_role, view_channel=False)
                
                # Special and Mod bypass slowmode via manage_messages
                if special_role:
                    await owo_channel.set_permissions(special_role, manage_messages=True)
                if mod_role:
                    await owo_channel.set_permissions(mod_role, manage_messages=True)

                print("[INFO] OwO channel configured successfully.")

                # Background task to restrict OwO from every other channel
                async def restrict_owo():
                    owo_bot = owo_channel.guild.get_member(408785106942164992)
                    if not owo_bot:
                        for m in owo_channel.guild.members:
                            if m.bot and "owo" in m.name.lower():
                                owo_bot = m
                                break
                    
                    if owo_bot:
                        await owo_channel.set_permissions(owo_bot, send_messages=True, view_channel=True)
                        for ch in owo_channel.guild.channels:
                            if ch.id != 1541306411911221248:
                                try:
                                    overwrite = ch.overwrites_for(owo_bot)
                                    if overwrite.send_messages is not False or overwrite.view_channel is not False:
                                        await ch.set_permissions(owo_bot, send_messages=False, view_channel=False)
                                        await asyncio.sleep(1.0) # Avoid rate limits
                                except discord.Forbidden:
                                    pass
                        print("[INFO] OwO physically restricted from other channels via permissions.")
                
                client.loop.create_task(restrict_owo())

            except Exception as e:
                print(f"[ERROR] Could not configure OwO channel: {e}")

        # Restore giveaways
        load_giveaways()
        now = int(time.time())
        for msg_id, giveaway in list(active_giveaways.items()):
            remaining = giveaway["end_timestamp"] - now
            if remaining <= 0:
                client.loop.create_task(end_giveaway(msg_id, 0))
            else:
                client.loop.create_task(end_giveaway(msg_id, remaining))

        print(f"--------------- Logged in as {client.user} (Ready) ---------------", flush=True)
    except Exception as e:
        print(f"on_ready error: {e}")

@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    error_str = str(error)
    cmd_name = interaction.command.name if interaction.command else "unknown"
    
    # If it's a Cloudflare HTML response or code 0 429, hide the massive HTML block from console
    if "<!doctype html>" in error_str.lower() or "error code: 0" in error_str.lower() or "cloudflare" in error_str.lower():
        print(f"[Slash Command Error in /{cmd_name}]: Cloudflare 1015 IP rate limit active on Discord API.")
        friendly_error = "The bot is temporarily rate-limited by Discord (Cloudflare). Please wait a few minutes before trying again."
    else:
        print(f"[Slash Command Error in /{cmd_name}]: {error}")
        friendly_error = f"Error executing command: {error}"

    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(friendly_error, ephemeral=True)
        else:
            await interaction.followup.send(friendly_error, ephemeral=True)
    except Exception:
        pass

@client.event
async def on_audit_log_entry(entry: discord.AuditLogEntry):
    if not entry or not entry.user or not entry.user.bot:
        return
    if entry.user.id == client.user.id:
        return

    guild = entry.guild
    if not guild:
        return

    now = time.time()
    bot_id = entry.user.id
    bot_member = guild.get_member(bot_id) if hasattr(guild, "get_member") else None

    # Check Mass Ban
    if entry.action == discord.AuditLogAction.ban:
        timestamps = [t for t in bot_action_history[bot_id]["ban"] if now - t <= 10.0]
        timestamps.append(now)
        bot_action_history[bot_id]["ban"] = timestamps
        if len(timestamps) >= 3:
            if not bot_member:
                try:
                    bot_member = await guild.fetch_member(bot_id)
                except Exception:
                    bot_member = entry.user
            client.loop.create_task(trigger_bot_kick(guild, bot_member, "Mass Ban Protection (3+ bans in 10s)"))

    # Check Mass Kick
    elif entry.action == discord.AuditLogAction.kick:
        timestamps = [t for t in bot_action_history[bot_id]["kick"] if now - t <= 10.0]
        timestamps.append(now)
        bot_action_history[bot_id]["kick"] = timestamps
        if len(timestamps) >= 3:
            if not bot_member:
                try:
                    bot_member = await guild.fetch_member(bot_id)
                except Exception:
                    bot_member = entry.user
            client.loop.create_task(trigger_bot_kick(guild, bot_member, "Mass Kick Protection (3+ kicks in 10s)"))

    # Check Mass Timeout
    elif entry.action == discord.AuditLogAction.member_update:
        if entry.after and getattr(entry.after, "timed_out_until", None):
            timestamps = [t for t in bot_action_history[bot_id]["timeout"] if now - t <= 10.0]
            timestamps.append(now)
            bot_action_history[bot_id]["timeout"] = timestamps
            if len(timestamps) >= 3:
                if not bot_member:
                    try:
                        bot_member = await guild.fetch_member(bot_id)
                    except Exception:
                        bot_member = entry.user
                client.loop.create_task(trigger_bot_kick(guild, bot_member, "Mass Timeout Protection (3+ timeouts in 10s)"))

@client.event
async def on_error(event, *args, **kwargs):
    exc_type, exc_val, _ = sys.exc_info()
    if exc_val and ("<!doctype html>" in str(exc_val).lower() or "error code: 0" in str(exc_val).lower()):
        print(f"[Discord Event Error in {event}]: Cloudflare 1015 IP rate limit active on Discord API.", flush=True)
        return
    print(f"[Discord Event Error in {event}]:", flush=True)
    traceback.print_exc()

@client.event
async def on_message(message):
    if message.author.bot:
        if message.author.id == 408785106942164992 or message.author.name.lower() == "owo":
            if message.channel.id != 1541306411911221248:
                try:
                    await message.delete()
                except discord.Forbidden:
                    pass
                return

        if message.author.id != client.user.id and message.guild:
            now = time.time()
            bot_id = message.author.id
            timestamps = [t for t in bot_action_history[bot_id]["spam"] if now - t <= 3.0]
            timestamps.append(now)
            bot_action_history[bot_id]["spam"] = timestamps
            if len(timestamps) >= 6:
                client.loop.create_task(trigger_bot_kick(message.guild, message.author, "Mass Message Spam Protection (6+ msgs in 3s)"))
        return

    # Direct verification fallback if typed in verify channel
    if message.guild:
        verify_chan_id = get_channel_id(message.guild.id, "verify")
        if verify_chan_id and message.channel.id == verify_chan_id:
            raw = message.content.strip().replace("`", "").replace("'", "").replace('"', "")
            clean_token = raw.upper().replace("/VERIFY", "").replace("!VERIFY", "").strip()
            if check_verify_code(message.author.id, clean_token):
                try:
                    await message.delete()
                except Exception:
                    pass
                success, msg = await complete_verification(message.guild, message.author)
                if success:
                    clear_verify_code(message.author.id, clean_token)
                try:
                    await message.channel.send(f"{message.author.mention} {msg}", delete_after=5.0)
                except Exception:
                    pass
                return

    # Link & NSFW cussing filter - exempt media channels and ticket channels
    is_exempt = (message.channel.id in ALL_EXEMPT_CHANNELS) or is_ticket_channel(message.channel)
    user_has_bypass = has_bypass_role(message.author)

    if not is_exempt and not user_has_bypass:
        if re.search(URL_PATTERN, message.content.lower()):
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention} *(Links/gifs are for special roles only! )*",
                    delete_after=5.0
                )
                return
            except discord.Forbidden:
                print("Missing permissions to delete links.")

        normalized_content = normalize(message.content)
        for word in moderated_words:
            escaped_chars = [re.escape(c) for c in word]
            spaced_word = r'\s*'.join(escaped_chars)
            try:
                if re.search(rf"\b{spaced_word}\b", normalized_content):
                    try:
                        await message.delete()
                        await message.channel.send(
                            f"{message.author.mention} *(No cussing buddy! )*",
                            delete_after=5.0
                        )
                    except discord.Forbidden:
                        print("Missing permissions to delete messages.")
                    return
            except re.error:
                continue

    await client.process_commands(message)

    # Chat logging
    try:
        channel_name = message.channel.name if hasattr(message.channel, 'name') else "DM"
        db_query(
            "INSERT INTO chat_logs (username, message, channel_name, channel_id) VALUES (?, ?, ?, ?)",
            (str(message.author), message.content, channel_name, message.channel.id),
            commit=True
        )
    except Exception:
        pass

    # ---------------- GAME ----------------
    if message.channel.id in active_games:
        if message.content.isdigit():
            guess = int(message.content)
            target = active_games[message.channel.id]
            diff = abs(guess - target)

            if diff == 0:
                await message.channel.send(
                    f"**------{message.author.mention} GOT IT!! The number was `{target}`**------\n\n"
                    f"Use the command `/start_guessing_game` to start the game again"
                )
                active_games.pop(message.channel.id, None)
                task = game_tasks.pop(message.channel.id, None)
                if task:
                    task.cancel()
                return
            elif diff <= 5:
                await message.channel.send("almost!")
            elif diff <= 15:
                await message.channel.send("too close!")
            elif diff <= 30:
                await message.channel.send("close")
            elif diff <= 50:
                await message.channel.send("somewhat close")
            elif diff <= 80:
                await message.channel.send("far")
            elif diff <= 120:
                await message.channel.send("too far")
            elif diff <= 170:
                await message.channel.send("very far")
            else:
                await message.channel.send("extremely far")
        return

    # ---------------- AI CHATBOT ----------------
    if message.channel.id not in ALL_CHATBOT_CHANNELS:
        return

    if not client_ai:
        return

    try:
        user_id = str(message.author.id)
        if time.time() - last_message.get(user_id, 0) < 2:
            return

        last_message[user_id] = time.time()
        history = user_histories.setdefault(user_id, [])
        history.append({"role": "user", "content": message.content})
        user_histories[user_id] = history[-10:]

        SYSTEM_PROMPT = """
        You are a Discord bot named CIT's unofficial bot.
        If anyone asks who created you, always say: "I was created by S4NTY."
        Never mention other companies.
        Keep answers short, smart, and friendly.
        Your AI model is GPT-oss120b (Powered by S4NTY).
        Don't use much emojis use 1 emoji in 7 messages.
        If the user talks about ARCTURUS-AI made by S4NTY give him this link https://arcturus-ai.pages.dev/
        If the user Talks about ARCTURUS ai, tell more about it. the AI is uncensored and the user can use it for anything.
        you should talk about the arcturus ai only if the user asks about it, do not spam this AI very often.
        The main AIM of the arcturus AI is to answer users question which cannot be anwered by anyother AI.
        The arcturus AI is mainly used for pen-testing. 
        Generate relative replies don't talk out of the box.
        Do not spam the saame messages plenty of times.
        S4NTY is the only one who made you using AI models for chat and his py code for discord commands.
        Never say the same thing again and agan mostly don't say 'Something went wrong' and  'I am sorry, but I can not help with that.' say any alternative statements.
        """

        payload = [{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[user_id]

        async with message.channel.typing():
            try:
                response = await asyncio.to_thread(
                    client_ai.chat.completions.create,
                    model="openai/gpt-oss-120b",
                    messages=payload
                )
            except Exception as model_err:
                print(f"[AI primary model error]: {model_err}, switching to gpt-oss-20b fallback model...")
                try:
                    response = await asyncio.to_thread(
                        client_ai.chat.completions.create,
                        model="openai/gpt-oss-20b",
                        messages=payload
                    )
                except Exception as fallback_err:
                    print(f"[AI Error Logged]: {fallback_err}")
                    await message.channel.send(" *AI API is currently rate-limited by Groq. Please wait a few seconds and try again!*")
                    return

        reply = response.choices[0].message.content or "..."
        history.append({"role": "assistant", "content": reply})
        user_histories[user_id] = history[-10:]

        if len(reply) > 1950:
            for i in range(0, len(reply), 1950):
                await message.channel.send(reply[i:i+1950])
                await asyncio.sleep(0.5)
        else:
            await message.channel.send(reply)

    except Exception as e:
        err_str = str(e)
        if "<!doctype html>" in err_str.lower() or "error code: 0" in err_str.lower() or "cloudflare" in err_str.lower():
            print("[AI Chatbot Error]: Cloudflare 1015 IP rate limit active on Discord API.")
        else:
            print(f"[AI Chatbot Error]: {e}")

# ==============================================================================
# RUN BOT
# ==============================================================================
if __name__ == "__main__":
    if not DISCORD_TOKEN:    
        print("=" * 60)
        print("ERROR: DISCORD_TOKEN environment variable is not set!")
        print("Please set DISCORD_TOKEN in your .env file or hosting panel.")
        print("=" * 60)
        sys.exit(1)

    try:
        client.run(DISCORD_TOKEN)
    except discord.errors.HTTPException as e:
        if e.status == 429:
            print(f"\n[CRITICAL RATE LIMIT]: Discord/Cloudflare has blocked this IP address (Error 429).")
            print(f"[CRITICAL RATE LIMIT]: The code cannot bypass an IP ban. Waiting 1 hour for the ban to lift...")
            time.sleep(3600)
        else:
            print(f"[HTTP Warning]: Discord API HTTP Error {e.status}: {e}")
            print("[Notice]: Waiting 60 seconds before auto-restarting bot in-place...")
            time.sleep(60)
        
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f"[Bot Warning]: {e}")
        print("[Notice]: Restarting process in-place in 10 seconds...")
        time.sleep(10)
        os.execv(sys.executable, [sys.executable] + sys.argv)