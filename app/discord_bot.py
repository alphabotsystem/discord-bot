from os import environ, _exit
environ["PRODUCTION_MODE"] = environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in environ and environ["PRODUCTION_MODE"] else ""

from re import split
from random import randint
from time import time
from copy import deepcopy
from datetime import datetime
from pytz import utc
from requests import post
from asyncio import CancelledError, sleep
from zmq import NOBLOCK
from traceback import format_exc

import discord
from discord import AutoShardedBot, Embed, Intents, Activity, Status, ActivityType, MessageType
from google.cloud.firestore import AsyncClient as FirestoreAsyncClient
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.firestore import Increment
from google.cloud.error_reporting import Client as ErrorReportingClient

from assets import static_storage
from helpers.utils import Utils
from helpers import constants

from TickerParser import TickerParser
from Processor import Processor
from DatabaseConnector import DatabaseConnector

from CommandRequest import CommandRequest

from commands.assistant import AlphaCommand
from commands.alerts import AlertCommand
from commands.charts import ChartCommand
from commands.flow import FlowCommand
from commands.heatmaps import HeatmapCommand
from commands.depth import DepthCommand
from commands.prices import PriceCommand
from commands.volume import VolumeCommand
from commands.convert import ConvertCommand
from commands.details import DetailsCommand
from commands.lookup import LookupCommand
from commands.paper import PaperCommand
from commands.ichibot import IchibotCommand, Ichibot
from commands.cope import CopeVoteCommand


database = FirestoreAsyncClient()
logging = ErrorReportingClient(service="discord")
snapshots = FirestoreClient()

BETA_SERVERS = [
	414498292655980583, 849579081800482846, 779004662157934665, 707238867840925706, 493617351216857088, 642039300208459796, 704211103139233893, 710291265689878669, 614609141318680581, 719265732214390816, 788809517818445875, 834195584398524526, 771423228903030804, 778444625639374858, 813915848510537728, 816446013274718209, 807785366526230569, 817764642423177227, 618471986586189865, 663752459424104456, 697085377802010634, 719215888938827776, 726478017924169748, 748813732620009503, 814738213599445013, 856938896713580555, 793014166553755698, 838822602708353056, 837526018088239105, 700113101353123923, 732072413969383444, 784964427962777640, 828430973775511575, 838573421281411122, 625105491743473689, 469530035645317120, 814256366067253268, 848053870197473290, 802692756773273600, 782315810621882369, 597269708345180160, 821150986567548948, 737326609329291335, 746804569303941281, 825933090311503905, 804771454561681439, 827433009598038016, 830534974381752340, 824300337887576135, 747441663193907232, 832625164801802261, 530964559801090079, 831928179299844166, 812819897305399296, 460731020245991424, 829028161983348776, 299922493924311054, 608761795531767814, 336233207269687299, 805453662746968064, 379077201775296513, 785702300886499369, 690135278978859023
]


# -------------------------
# Initialization
# -------------------------

intents = Intents.none()
intents.dm_messages = True
intents.guild_messages = True
intents.guilds = True
intents.integrations = True
intents.webhooks = True

discord.http.API_VERSION = 9 # TEMP
bot = AutoShardedBot(intents=intents, chunk_guilds_at_startup=False, status=Status.idle, activity=Activity(type=ActivityType.playing, name="a reboot, brb!"))


# -------------------------
# Guild count & management
# -------------------------

@bot.event
async def on_guild_join(guild):
	try:
		if guild.id in constants.bannedGuilds:
			await guild.leave()
			return
		properties = await guild_secure_fetch(guild.id)
		properties = CommandRequest.create_guild_settings(properties)
		await database.document(f"discord/properties/guilds/{guild.id}").set(properties)
		await update_guild_count()
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=str(guild.id))

@bot.event
async def on_guild_remove(guild):
	try:
		await update_guild_count()
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=str(guild.id))

async def update_guild_count():
	if environ["PRODUCTION_MODE"] and len(bot.guilds) > 20000:
		t = datetime.now().astimezone(utc)
		await database.document("discord/statistics").set({"{}-{:02d}".format(t.year, t.month): {"servers": len(bot.guilds)}}, merge=True)
		post(f"https://top.gg/api/bots/{bot.user.id}/stats", data={"server_count": len(bot.guilds)}, headers={"Authorization": environ["TOPGG_KEY"]})


# -------------------------
# Database management
# -------------------------

def update_alpha_settings(settings, changes, timestamp):
	global alphaSettings
	alphaSettings = settings[0].to_dict()
	botStatus[1] = True

# -------------------------
# Message processing
# -------------------------

def process_alpha_messages(pendingMessages, changes, timestamp):
	if len(changes) == 0 or not environ["PRODUCTION_MODE"]: return
	try:
		for change in changes:
			message = change.document.to_dict()
			if change.type.name in ["ADDED", "MODIFIED"]:
				bot.loop.create_task(send_alpha_messages(change.document.id, message))

	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception()

async def send_alpha_messages(messageId, message):
	try:
		while not botStatus[0]:
			await sleep(60)

		embed = Embed(title=message["title"], color=message["color"])
		if message.get("description") is not None: embed.description = message.get("description")
		if message.get("subtitle") is not None: embed.set_author(name=message["subtitle"], icon_url=message.get("icon", static_storage.icon))
		if message.get("image") is not None: embed.set_image(url=message["image"])
		if message.get("url") is not None: embed.url = message["url"]

		destinationUser = None
		destinationChannel = None
		if message.get("user") is not None:
			try:
				destinationUser = bot.get_user(int(message["user"]))
				if destinationUser is None:
					destinationUser = await bot.fetch_user(int(message["user"]))
			except: pass
		if message.get("channel") is not None:
			try:
				destinationChannel = bot.get_channel(int(message["channel"]))
				if destinationChannel is None:
					destinationChannel = await bot.fetch_channel(int(message["channel"]))
			except: pass

		if message.get("user") is not None:
			try:
				await destinationUser.send(embed=embed)
			except:
				try:
					mentionText = f"<@!{message['user']}>!" if destinationUser is None else None
					await destinationChannel.send(content=mentionText, embed=embed)
				except: pass
			await database.document(f"discord/properties/messages/{messageId}").delete()
		elif message.get("channel") is not None:
			try:
				await destinationChannel.send(embed=embed)
				await database.document(f"discord/properties/messages/{messageId}").delete()
			except:
				pass

	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception()

# -------------------------
# Job functions
# -------------------------

async def security_check():
	try:
		guildNames = [e.name for e in bot.guilds]
		guildsToRemove = []
		for key in ["blacklist", "whitelist"]:
			for guild in alphaSettings["tosWatchlist"]["nicknames"][key]:
				if guild not in guildNames: guildsToRemove.append(guild)
			for guild in guildsToRemove:
				if guild in alphaSettings["tosWatchlist"]["nicknames"][key]: alphaSettings["tosWatchlist"]["nicknames"][key].pop(guild)

		botNicknames = []
		for guild in bot.guilds:
			if guild.id in constants.bannedGuilds:
				await guild.leave()

			if guild.me is not None:
				isBlacklisted = guild.name in alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]
				isWhitelisted = guild.name in alphaSettings["tosWatchlist"]["nicknames"]["whitelist"]

				if guild.me.nick is not None:
					if isBlacklisted:
						if guild.me.nick == alphaSettings["tosWatchlist"]["nicknames"]["blacklist"][guild.name]:
							if guild.me.guild_permissions.change_nickname:
								try:
									await guild.me.edit(nick=None)
									alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
								except: pass
							continue
						else: alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
					if isWhitelisted:
						if guild.me.nick == alphaSettings["tosWatchlist"]["nicknames"]["whitelist"][guild.name]: continue
						else: alphaSettings["tosWatchlist"]["nicknames"]["whitelist"].pop(guild.name)

					for i in range(0, len(guild.me.nick.replace(" ", "")) - 2):
						nameSlice = guild.me.nick.lower().replace(" ", "")[i:i+3]
						if nameSlice in guild.name.lower() and nameSlice not in ["the"]:
							botNicknames.append(f"```{guild.name} ({guild.id}): {guild.me.nick}```")
							break
				else:
					if isBlacklisted: alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
					if isWhitelisted: alphaSettings["tosWatchlist"]["nicknames"]["whitelist"].pop(guild.name)

		botNicknamesText = "No bot nicknames to review"
		if len(botNicknames) > 0: botNicknamesText = f"These guilds might be rebranding Alpha Bot: {''.join(botNicknames)}"

		if environ["PRODUCTION_MODE"]:
			usageReviewChannel = bot.get_channel(571786092077121536)
			botNicknamesMessage = await usageReviewChannel.fetch_message(709335020174573620)
			await botNicknamesMessage.edit(content=botNicknamesText[:2000])

			await database.document("discord/settings").set({"tosWatchlist": alphaSettings["tosWatchlist"]}, merge=True)

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception()

async def database_sanity_check():
	if not environ["PRODUCTION_MODE"]: return
	try:
		guilds = await guildProperties.keys()
		if guilds is None: return

		guildIds = [str(g.id) for g in bot.guilds]

		for guildId in guilds:
			if guildId not in guildIds:
				await database.document(f"discord/properties/guilds/{guildId}").set({"stale": {"count": Increment(1), "timestamp": time()}}, merge=True)

		for guildId in guildIds:
			if guildId not in guilds:
				properties = await guild_secure_fetch(guildId)
				if not properties:
					await database.document(f"discord/properties/guilds/{guildId}").set(CommandRequest.create_guild_settings({}))

	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception()

async def guild_secure_fetch(guildId):
	properties = await guildProperties.get(guildId)

	if properties is None:
		properties = await database.document(f"discord/properties/guilds/{guildId}").get()
		properties = properties.to_dict()
		if properties is None: properties = {}

	return properties

# -------------------------
# Message handling
# -------------------------

@bot.event
async def on_message(message):
	try:
		# Skip messages with empty content field, messages from self, or all messages when in startup mode
		if message.clean_content == "" or message.type != MessageType.default or message.author == bot.user or not is_bot_ready(): return

		_rawMessage = " ".join(message.clean_content.split())
		_messageContent = _rawMessage.lower()
		_authorId = message.author.id if message.webhook_id is None else message.webhook_id
		_accountId = None
		_guildId = message.guild.id if message.guild is not None else -1
		_channelId = message.channel.id if message.channel is not None else -1

		# Ignore if user or server is banned
		if _authorId in constants.blockedUsers or _guildId in constants.blockedGuilds: return

		_accountProperties = {}
		_guildProperties = await guildProperties.get(_guildId, {})

		commandRequest = CommandRequest(
			raw=_rawMessage,
			content=_messageContent,
			authorId=_authorId,
			channelId=_channelId,
			guildId=_guildId,
			guildProperties=_guildProperties
		)
		_snapshot = "{}-{:02d}".format(message.created_at.year, message.created_at.month)

		if commandRequest.content.startswith("/c "):
			embed = Embed(title="Plain text commands will not work. To use slash commands, type `/c`, and select the command from the popup above the chat box.", color=constants.colors["red"])
			embed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fslash-commands.gif?alt=media&token=32e05ba1-9b06-47b1-a037-d37036b382a6")
			await message.channel.send(embed=embed)

		if commandRequest.content.startswith("c "):
			if commandRequest.guildId != -1:
				_availablePermissions = None if commandRequest.guildId == -1 else message.channel.permissions_for(message.guild.me)
				hasPermissions = True if commandRequest.guildId == -1 else (_availablePermissions.send_messages and _availablePermissions.embed_links and _availablePermissions.attach_files and _availablePermissions.add_reactions and _availablePermissions.use_external_emojis and _availablePermissions.manage_messages)
				if not hasPermissions:
					await deprecation_message(message, "c")
					return
				elif not commandRequest.guildProperties["settings"]["setup"]["completed"]:
					await deprecation_message(message, "c")
					return

			requestSlices = split(", c | c |, ", commandRequest.content.split(" ", 1)[1])
			if len(requestSlices) > 1:
				embed = Embed(title="Chained requests are no longer supported by traditional commands due to imminent deprecation. Use slash commands instead.", color=constants.colors["gray"])
				embed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fslash-commands.gif?alt=media&token=32e05ba1-9b06-47b1-a037-d37036b382a6")
				await message.channel.send(embed=embed)
				return
			for requestSlice in requestSlices:
				await chart(message, commandRequest, requestSlice)

			await database.document("discord/statistics").set({_snapshot: {"c": Increment(1)}}, merge=True)

		elif commandRequest.content.startswith("x "):
			if commandRequest.content.startswith(("x ichibot", "x ichi", "x login")):
				await deprecation_message(message, "ichibot login")
				return
			elif commandRequest.guildId == -1:
				_accountId = await accountProperties.match(_authorId)
				if _accountId is None:
					_accountProperties = await accountProperties.get(str(_authorId), {})
				else:
					_accountProperties = await accountProperties.get(_accountId, {})
				commandRequest.accountId = _accountId
				commandRequest.accountProperties = _accountProperties

				await process_ichibot_command(message, commandRequest, commandRequest.content.split(" ", 1)[1])
				await database.document("discord/statistics").set({_snapshot: {"x": Increment(1)}}, merge=True)

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception()


# -------------------------
# Legacy
# -------------------------

async def chart(message, request, requestSlice):
	try:
		arguments = requestSlice.split(" ")

		async with message.channel.typing():
			outputMessage, task = await Processor.process_chart_arguments(request, arguments[1:], request.get_platform_order_for("c"), tickerId=arguments[0].upper())
			if outputMessage is not None: return

			currentTask = task.get(task.get("currentPlatform"))
			timeframes = task.pop("timeframes")
			for i in range(task.get("requestCount")):
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
				payload, chartText = await Processor.process_task("chart", request.authorId, task)

				if payload is not None:
					currentTask = task.get(payload.get("platform"))
					embed = Embed(title=f"Alpha is transitioning to slash commands as is required by upcoming Discord changes. Use `/c` to avoid this warning. Old syntax will no longer work after deprecation <t:1656676800:R>.", color=constants.colors["red"])
					# embed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fslash-commands.gif?alt=media&token=32e05ba1-9b06-47b1-a037-d37036b382a6")
					await message.channel.send(content=chartText, embed=embed, file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")

# -------------------------
# Ichibot
# -------------------------

async def process_ichibot_command(message, commandRequest, requestSlice):
	sentMessages = []
	try:
		if requestSlice == "login":
			embed = Embed(title=":dart: API key preferences are available in your Alpha Account settings.", description="[Sign into you Alpha Account](https://www.alphabotsystem.com/sign-in) and visit [Ichibot preferences](https://www.alphabotsystem.com/account/ichibot) to update your API keys.", color=constants.colors["deep purple"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await message.channel.send(embed=embed)

		elif commandRequest.is_registered():
			origin = f"{commandRequest.accountId}_{commandRequest.authorId}_ichibot"

			if origin in Ichibot.sockets:
				socket = Ichibot.sockets.get(origin)
				await socket.send_multipart([commandRequest.accountId.encode(), b"", commandRequest.raw.split(" ", 1)[1].encode()])
				try: await message.add_reaction("✅")
				except: pass

				if requestSlice in ["q", "quit", "exit", "logout"]:
					Ichibot.sockets.pop(origin)
					embed = Embed(title="Ichibot connection has been closed.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					await message.channel.send(embed=embed)
			else:
				embed = Embed(title="Ichibot connection is not open.", description="You can initiate a connection with `x login` followed by the exchange you want to connect to.", color=constants.colors["pink"])
				embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
				missingExchangeMessage = await message.channel.send(embed=embed)

		else:
			embed = Embed(title=":dart: You must have an Alpha Account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), connect your account with your Discord profile, and add an API key.", color=constants.colors["deep purple"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await message.channel.send(embed=embed)

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
		await unknown_error(message, commandRequest.authorId)
	return (sentMessages, len(sentMessages))


# -------------------------
# Slash command prelight
# -------------------------

async def create_request(ctx, autodelete=-1):
	_authorId = ctx.author.id
	_accountId = None
	_guildId = ctx.guild.id if ctx.guild is not None else -1
	_channelId = ctx.channel.id if ctx.channel is not None else -1

	# Ignore if user if locked in a prompt, or banned
	if _authorId in constants.blockedUsers or _guildId in constants.blockedGuilds: return

	try: await ctx.defer()
	except: return None

	_accountProperties = {}
	_guildProperties = await guildProperties.get(_guildId, {})
	if not ctx.author.bot:
		_accountId = await accountProperties.match(_authorId)
		if _accountId is None:
			_accountProperties = await accountProperties.get(str(_authorId), {})
		else:
			_accountProperties = await accountProperties.get(_accountId, {})

	request = CommandRequest(
		accountId=_accountId,
		authorId=_authorId,
		channelId=_channelId,
		guildId=_guildId,
		accountProperties=_accountProperties,
		guildProperties=_guildProperties,
		autodelete=autodelete
	)

	if request.guildId != -1:
		if len(alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]) != 0 and ctx.interaction.guild.name in alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]:
			embed = Embed(title="This Discord community guild was flagged for rebranding Alpha and is therefore violating the Terms of Service. Inability to comply will result in termination of all Alpha branded services.", color=0x000000)
			embed.add_field(name="Terms of service", value="[Read now](https://www.alphabotsystem.com/terms-of-service)", inline=True)
			embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
			await ctx.interaction.edit_original_message(embed=embed)
			return None
		elif not request.guildProperties["settings"]["setup"]["completed"]:
			forceFetch = await database.document(f"discord/properties/guilds/{request.guildId}").get()
			forcedFetch = CommandRequest.create_guild_settings(forceFetch.to_dict())
			if forcedFetch["settings"]["setup"]["completed"]:
				request.guildProperties = forcedFetch
				return request
			elif not ctx.bot and ctx.interaction.channel.permissions_for(ctx.author).administrator:
				embed = Embed(title="Hello world!", description="Thanks for adding Alpha Bot to your Discord community, we're thrilled to have you onboard. We think you're going to love everything Alpha Bot can do. Before you start using it, you must complete a short setup process. Sign into your [Alpha Account](https://www.alphabotsystem.com/communities) and visit your [Communities Dashboard](https://www.alphabotsystem.com/communities) to begin.", color=constants.colors["pink"])
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = Embed(title="Hello world!", description="This is Alpha Bot, the most advanced financial bot on Discord. A short setup process hasn't been completed in this Discord community yet. Ask administrators to complete it by signing into their [Alpha Account](https://www.alphabotsystem.com/communities) and visiting their [Communities Dashboard](https://www.alphabotsystem.com/communities).", color=constants.colors["pink"])
				await ctx.interaction.edit_original_message(embed=embed)
			return None

	return request


# -------------------------
# Slash commands
# -------------------------

bot.add_cog(AlphaCommand(bot, create_request, database, logging))
bot.add_cog(AlertCommand(bot, create_request, database, logging))
bot.add_cog(ChartCommand(bot, create_request, database, logging))
bot.add_cog(FlowCommand(bot, create_request, database, logging))
bot.add_cog(HeatmapCommand(bot, create_request, database, logging))
bot.add_cog(DepthCommand(bot, create_request, database, logging))
bot.add_cog(PriceCommand(bot, create_request, database, logging))
bot.add_cog(VolumeCommand(bot, create_request, database, logging))
bot.add_cog(ConvertCommand(bot, create_request, database, logging))
bot.add_cog(DetailsCommand(bot, create_request, database, logging))
bot.add_cog(LookupCommand(bot, create_request, database, logging))
bot.add_cog(PaperCommand(bot, create_request, database, logging))
bot.add_cog(IchibotCommand(bot, create_request, database, logging))
bot.add_cog(CopeVoteCommand(bot, create_request, database, logging))


# -------------------------
# Error handling
# -------------------------

async def unknown_error(ctx, authorId):
	embed = Embed(title="Looks like something went wrong. The issue has been reported.", color=constants.colors["gray"])
	embed.set_author(name="Something went wrong", icon_url=static_storage.icon_bw)
	try: await ctx.channel.send(embed=embed)
	except: return

async def deprecation_message(ctx, command):
	embed = Embed(title=f"Alpha is transitioning to slash commands as is required by upcoming Discord changes. Use `/{command}` instead of the old syntax.", color=constants.colors["red"])
	embed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fslash-commands.gif?alt=media&token=32e05ba1-9b06-47b1-a037-d37036b382a6")
	try: await ctx.channel.send(embed=embed)
	except: return


# -------------------------
# Job queue
# -------------------------

async def job_queue():
	while True:
		try:
			await sleep(Utils.seconds_until_cycle())
			if not is_bot_ready() or not await guildProperties.check_status() or not await accountProperties.check_status(): continue
			t = datetime.now().astimezone(utc)
			timeframes = Utils.get_accepted_timeframes(t)

			if "15m" in timeframes:
				await database_sanity_check()
				await update_guild_count()
			if "1H" in timeframes:
				await security_check()

		except CancelledError: return
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: logging.report_exception()


# -------------------------
# Startup
# -------------------------

botStatus = [False, False]

alphaSettings = {}
accountProperties = DatabaseConnector(mode="account")
guildProperties = DatabaseConnector(mode="guild")
Processor.clientId = b"discord_alpha"
Ichibot.logging = logging

discordSettingsLink = snapshots.document("discord/settings").on_snapshot(update_alpha_settings)
discordMessagesLink = snapshots.collection("discord/properties/messages").on_snapshot(process_alpha_messages)

@bot.event
async def on_ready():
	print("[Startup]: Alpha Bot is online")

	try:
		while not await accountProperties.check_status() or not await guildProperties.check_status():
			await sleep(15)
		botStatus[0] = True
		await bot.change_presence(status=Status.online, activity=Activity(type=ActivityType.watching, name="alphabotsystem.com"))
	except:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception()
		_exit(1)
	print("[Startup]: Alpha Bot startup complete")

def is_bot_ready():
	return all(botStatus)


# -------------------------
# Login
# -------------------------

bot.loop.create_task(job_queue())
token = environ["DISCORD_PRODUCTION_TOKEN" if environ["PRODUCTION_MODE"] else "DISCORD_DEVELOPMENT_TOKEN"]
bot.loop.run_until_complete(bot.start(token))