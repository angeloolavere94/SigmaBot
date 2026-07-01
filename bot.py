import struct
import sys
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
import shutil
import math

load_dotenv()

LUA_SIGNATURE = b"\x1bLua"
LUAC_VERSION = 0x51

OPNAMES = [
    "MOVE", "LOADK", "LOADBOOL", "LOADNIL", "GETUPVAL",
    "GETGLOBAL", "GETTABLE", "SETGLOBAL", "SETUPVAL", "SETTABLE",
    "NEWTABLE", "SELF", "ADD", "SUB", "MUL", "DIV", "MOD", "POW",
    "UNM", "NOT", "LEN", "CONCAT", "JMP", "EQ", "LT", "LE",
    "TEST", "TESTSET", "CALL", "TAILCALL", "RETURN", "FORLOOP",
    "FORPREP", "TFORLOOP", "SETLIST", "CLOSE", "CLOSURE", "VARARG",
]

OP_MODE_ABC = 0
OP_MODE_ABx = 1
OP_MODE_AsBx = 2

OPMODES = [
    OP_MODE_ABC, OP_MODE_ABx, OP_MODE_ABC, OP_MODE_ABC, OP_MODE_ABC,
    OP_MODE_ABx, OP_MODE_ABC, OP_M
