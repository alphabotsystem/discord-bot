from os import environ, _exit
environ["PRODUCTION_MODE"] = environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in environ and environ["PRODUCTION_MODE"] else ""

from re import split
from random import randint
from math import ceil, floor
from time import time
from copy import deepcopy
from datetime import datetime
from pytz import utc
from requests import post
from asyncio import CancelledError, InvalidStateError, TimeoutError, sleep, all_tasks, wait_for
from uuid import uuid4
from orjson import loads, dumps, OPT_INDENT_2, OPT_SORT_KEYS
from zmq import NOBLOCK
from traceback import format_exc

import discord
from discord.commands import Option, permissions
from google.cloud.firestore import AsyncClient as FirestoreAsnycClient
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.firestore import Increment, DELETE_FIELD
from google.cloud.error_reporting import Client as ErrorReportingClient

from assets import static_storage
from helpers.utils import Utils
from helpers import constants

from TickerParser import TickerParser
from Processor import Processor
from DatabaseConnector import DatabaseConnector
from engine.presets import Presets

from MessageRequest import MessageRequest

from commands.assistant import AlphaCommand
from commands.alerts import AlertCommand
from commands.charts import ChartCommand
from commands.depth import DepthCommand
from commands.prices import PriceCommand
from commands.volume import VolumeCommand
from commands.convert import ConvertCommand
from commands.details import DetailsCommand
from commands.paper import PaperCommand
from commands.ichibot import IchibotCommand, Ichibot
from commands.cope import CopeVoteCommand


database = FirestoreAsnycClient()
logging = ErrorReportingClient(service="discord")
snapshots = FirestoreClient()

BETA_SERVERS = [
	414498292655980583, 849579081800482846, 779004662157934665, 707238867840925706, 493617351216857088, 642039300208459796, 704211103139233893, 710291265689878669, 614609141318680581, 719265732214390816, 788809517818445875, 834195584398524526, 771423228903030804, 778444625639374858, 813915848510537728, 816446013274718209, 807785366526230569, 817764642423177227, 618471986586189865, 663752459424104456, 697085377802010634, 719215888938827776, 726478017924169748, 748813732620009503, 814738213599445013, 856938896713580555, 793014166553755698, 838822602708353056, 837526018088239105, 700113101353123923, 732072413969383444, 784964427962777640, 828430973775511575, 838573421281411122, 625105491743473689, 469530035645317120, 814256366067253268, 848053870197473290, 802692756773273600, 782315810621882369, 597269708345180160, 821150986567548948, 737326609329291335, 746804569303941281, 825933090311503905, 804771454561681439, 827433009598038016, 830534974381752340, 824300337887576135, 747441663193907232, 832625164801802261, 530964559801090079, 831928179299844166, 812819897305399296, 460731020245991424, 829028161983348776, 299922493924311054, 608761795531767814, 336233207269687299, 805453662746968064, 379077201775296513, 785702300886499369, 690135278978859023
]


# -------------------------
# Initialization
# -------------------------

intents = discord.Intents.none()
intents.dm_messages = True
intents.guild_messages = True
intents.guilds = True
intents.integrations = True
intents.webhooks = True

bot = discord.AutoShardedBot(intents=intents, chunk_guilds_at_startup=False, status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.playing, name="a reboot, brb!"))

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
		properties = MessageRequest.create_guild_settings(properties)
		await database.document("discord/properties/guilds/{}".format(guild.id)).set(properties)
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
		post("https://top.gg/api/bots/{}/stats".format(bot.user.id), data={"server_count": len(bot.guilds)}, headers={"Authorization": environ["TOPGG_KEY"]})


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

		embed = discord.Embed(title=message["title"], color=message["color"])
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
					mentionText = "<@!{}>!".format(message["user"]) if destinationUser is None else None
					await destinationChannel.send(content=mentionText, embed=embed)
				except: pass
			await database.document("discord/properties/messages/{}".format(messageId)).delete()
		elif message.get("channel") is not None:
			try:
				await destinationChannel.send(embed=embed)
				await database.document("discord/properties/messages/{}".format(messageId)).delete()
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
							botNicknames.append("```{} ({}): {}```".format(guild.name, guild.id, guild.me.nick))
							break
				else:
					if isBlacklisted: alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
					if isWhitelisted: alphaSettings["tosWatchlist"]["nicknames"]["whitelist"].pop(guild.name)

		botNicknamesText = "No bot nicknames to review"
		if len(botNicknames) > 0: botNicknamesText = "These guilds might be rebranding Alpha Bot:{}".format("".join(botNicknames))

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
				await database.document("discord/properties/guilds/{}".format(guildId)).set({"stale": {"count": Increment(1), "timestamp": time()}}, merge=True)

		for guildId in guildIds:
			if guildId not in guilds:
				properties = await guild_secure_fetch(guildId)
				if not properties:
					await database.document("discord/properties/guilds/{}".format(guildId)).set(MessageRequest.create_guild_settings({}))

	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception()

async def guild_secure_fetch(guildId):
	properties = await guildProperties.get(guildId)

	if properties is None:
		properties = await database.document("discord/properties/guilds/{}".format(guildId)).get()
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
		if message.clean_content == "" or message.type != discord.MessageType.default or message.author == bot.user or not is_bot_ready(): return

		_checkpoint1 = time() * 1000
		_rawMessage = " ".join(message.clean_content.split())
		_messageContent = _rawMessage.lower()
		_authorId = message.author.id if message.webhook_id is None else message.webhook_id
		_accountId = None
		_guildId = message.guild.id if message.guild is not None else -1
		_channelId = message.channel.id if message.channel is not None else -1
		if _authorId == 361916376069439490:
			if " --user " in _messageContent:
				_messageContent, _authorId = _messageContent.split(" --user ")[0], int(_messageContent.split(" --user ")[1])
			if " --guild " in _messageContent:
				_messageContent, _guildId = _messageContent.split(" --guild ")[0], int(_messageContent.split(" --guild ")[1])

		# Ignore if user if locked in a prompt, or banned
		if _authorId in constants.blockedUsers or _guildId in constants.blockedGuilds: return

		_accountProperties = {}
		_guildProperties = await guildProperties.get(_guildId, {})
		_checkpoint2 = time() * 1000
		if not message.author.bot:
			if message.webhook_id is None: _accountId = await accountProperties.match(_authorId)
			if _accountId is None:
				_accountProperties = await accountProperties.get(str(_authorId), {})
			else:
				_accountProperties = await accountProperties.get(_accountId, {})
		_checkpoint3 = time() * 1000

		messageRequest = MessageRequest(
			raw=_rawMessage,
			content=_messageContent,
			accountId=_accountId,
			authorId=_authorId,
			channelId=_channelId,
			guildId=_guildId,
			accountProperties=_accountProperties,
			guildProperties=_guildProperties
		)
		_snapshot = "{}-{:02d}".format(message.created_at.year, message.created_at.month)
		sentMessages = []

		_availablePermissions = None if messageRequest.guildId == -1 else message.channel.permissions_for(message.guild.me)
		hasPermissions = True if messageRequest.guildId == -1 else (_availablePermissions.send_messages and _availablePermissions.embed_links and _availablePermissions.attach_files and _availablePermissions.add_reactions and _availablePermissions.use_external_emojis and _availablePermissions.manage_messages)

		if not messageRequest.content.startswith("preset "):
			messageRequest.content, messageRequest.presetUsed, parsedPresets = Presets.process_presets(messageRequest.content, messageRequest.accountProperties)

			if messageRequest.presetUsed:
				if messageRequest.command_presets_available():
					if messageRequest.presetUsed:
						embed = discord.Embed(title="Running `{}` command from personal preset.".format(messageRequest.content), color=constants.colors["light blue"])
						sentMessages.append(await message.channel.send(embed=embed))

		messageRequest.content = Utils.shortcuts(messageRequest.content)
		isCommand = messageRequest.content.startswith(tuple(constants.commandWakephrases))

		if messageRequest.guildId != -1:
			if isCommand:
				if not hasPermissions:
					p1 = _availablePermissions.send_messages
					p2 = _availablePermissions.embed_links
					p3 = _availablePermissions.attach_files
					p4 = _availablePermissions.add_reactions
					p5 = _availablePermissions.use_external_emojis
					p6 = _availablePermissions.manage_messages
					errorText = "Alpha Bot is missing one or more critical permissions."
					permissionsText = "Send messages: {}\nEmbed links: {}\nAttach files: {}\nAdd reactions: {}\nUse external emojis: {}\nManage Messages: {}".format(":white_check_mark:" if p1 else ":x:", ":white_check_mark:" if p2 else ":x:", ":white_check_mark:" if p3 else ":x:", ":white_check_mark:" if p4 else ":x:", ":white_check_mark:" if p5 else ":x:", ":white_check_mark:" if p6 else ":x:")
					embed = discord.Embed(title=errorText, description=permissionsText, color=0x000000)
					embed.add_field(name="Frequently asked questions", value="[alphabotsystem.com/faq](https://www.alphabotsystem.com/faq)", inline=False)
					embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
					try:
						await message.channel.send(embed=embed)
					except:
						try: await message.channel.send(content="{}\n{}".format(errorText, permissionsText))
						except: pass
					return
				elif len(alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]) != 0 and message.guild.name in alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]:
					embed = discord.Embed(title="This Discord community guild was flagged for rebranding Alpha and is therefore violating the Terms of Service. Inability to comply will result in termination of all Alpha branded services.", color=0x000000)
					embed.add_field(name="Terms of service", value="[Read now](https://www.alphabotsystem.com/terms-of-service)", inline=True)
					embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
					await message.channel.send(embed=embed)
				elif not messageRequest.guildProperties["settings"]["setup"]["completed"]:
					forceFetch = await database.document("discord/properties/guilds/{}".format(messageRequest.guildId)).get()
					forcedFetch = MessageRequest.create_guild_settings(forceFetch.to_dict())
					if forcedFetch["settings"]["setup"]["completed"]:
						messageRequest.guildProperties = forcedFetch
					elif not message.author.bot and message.channel.permissions_for(message.author).administrator:
						embed = discord.Embed(title="Hello world!", description="Thanks for adding Alpha Bot to your Discord community, we're thrilled to have you onboard. We think you're going to love everything Alpha Bot can do. Before you start using it, you must complete a short setup process. Sign into your [Alpha Account](https://www.alphabotsystem.com/communities) and visit your [Communities Dashboard](https://www.alphabotsystem.com/communities) to begin.", color=constants.colors["pink"])
						await message.channel.send(embed=embed)
					else:
						embed = discord.Embed(title="Hello world!", description="This is Alpha Bot, the most advanced financial bot on Discord. A short setup process hasn't been completed in this Discord community yet. Ask administrators to complete it by signing into their [Alpha Account](https://www.alphabotsystem.com/communities) and visiting their [Communities Dashboard](https://www.alphabotsystem.com/communities).", color=constants.colors["pink"])
						await message.channel.send(embed=embed)
					return

		if isCommand:
			if messageRequest.content.startswith("preset "):
				embed = discord.Embed(title="Command presets are getting deprecated. A replacement is in the works.", color=constants.colors["gray"])
				await message.channel.send(embed=embed)

			elif messageRequest.content.startswith("c "):
				if messageRequest.content == "c help":
					embed = discord.Embed(title=":chart_with_upwards_trend: Charts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
					await message.channel.send(embed=embed)
				else:
					requestSlices = split(", c | c |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + 2

						if rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							chartMessages, weight = await chart(message, messageRequest, requestSlice)
							sentMessages += chartMessages
							totalWeight += weight - 1

							rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"c": Increment(totalWeight)}}, merge=True)
					await finish_request(message, messageRequest, totalWeight, sentMessages)

			elif messageRequest.content.startswith("flow "):
				embed = discord.Embed(title="Flow command is being updated, and is currently unavailable.", description="All Alpha Pro subscribers using Alpha Flow will receive reimbursment in form of credit, or a refund if requested.", color=constants.colors["gray"])
				await message.channel.send(embed=embed)

			elif messageRequest.content.startswith("hmap "):
				requestSlices = split(", hmap | hmap |, ", messageRequest.content.split(" ", 1)[1])
				totalWeight = len(requestSlices)
				if totalWeight > messageRequest.get_limit() / 2:
					await hold_up(message, messageRequest)
					return
				for requestSlice in requestSlices:
					rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + 2

					if rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
						await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
						rateLimited[messageRequest.authorId] = messageRequest.get_limit()
						totalWeight = messageRequest.get_limit()
						break
					else:
						chartMessages, weight = await heatmap(message, messageRequest, requestSlice)
						sentMessages += chartMessages
						totalWeight += weight - 1

						rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + weight - 2

				await database.document("discord/statistics").set({_snapshot: {"hmap": Increment(totalWeight)}}, merge=True)
				await finish_request(message, messageRequest, totalWeight, sentMessages)

			elif messageRequest.content.startswith("d "):
				await deprecation_message(message, "d", isGone=True)

			elif messageRequest.content.startswith(("alert ", "alerts ")):
				await deprecation_message(message, "alert", isGone=True)

			elif messageRequest.content.startswith("p "):
				requestSlices = split(", p | p |, ", messageRequest.content.split(" ", 1)[1])
				totalWeight = len(requestSlices)
				if totalWeight > messageRequest.get_limit() / 2:
					await hold_up(message, messageRequest)
					return
				for requestSlice in requestSlices:
					rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + 2

					if rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
						await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
						rateLimited[messageRequest.authorId] = messageRequest.get_limit()
						totalWeight = messageRequest.get_limit()
						break
					else:
						quoteMessages, weight = await price(message, messageRequest, requestSlice)
						sentMessages += quoteMessages
						totalWeight += weight - 1

						rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + weight - 2

				await database.document("discord/statistics").set({_snapshot: {"p": Increment(totalWeight)}}, merge=True)
				await finish_request(message, messageRequest, totalWeight, sentMessages)

			elif messageRequest.content.startswith("v "):
				await deprecation_message(message, "v", isGone=True)

			elif messageRequest.content.startswith("convert "):
				await deprecation_message(message, "convert", isGone=True)

			elif messageRequest.content.startswith(("m ", "info")):
				await deprecation_message(message, "info", isGone=True)

			elif messageRequest.content.startswith("top"):
				requestSlices = split(", t | t |, top | top |, ", messageRequest.content.split(" ", 1)[1])
				totalWeight = len(requestSlices)
				if totalWeight > messageRequest.get_limit() / 2:
					await hold_up(message, messageRequest)
					return
				for requestSlice in requestSlices:
					rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + 2

					if rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
						await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
						rateLimited[messageRequest.authorId] = messageRequest.get_limit()
						totalWeight = messageRequest.get_limit()
						break
					else:
						rankingsMessages, weight = await rankings(message, messageRequest, requestSlice)
						sentMessages += rankingsMessages
						totalWeight += weight - 1

						rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + weight - 2

				await database.document("discord/statistics").set({_snapshot: {"t": Increment(totalWeight)}}, merge=True)
				await finish_request(message, messageRequest, totalWeight, sentMessages)

			elif messageRequest.content.startswith("mk "):
				requestSlices = split(", mk | mk |, ", messageRequest.content.split(" ", 1)[1])
				totalWeight = len(requestSlices)
				if totalWeight > messageRequest.get_limit() / 2:
					await hold_up(message, messageRequest)
					return
				for requestSlice in requestSlices:
					rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + 2

					if rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
						await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
						rateLimited[messageRequest.authorId] = messageRequest.get_limit()
						totalWeight = messageRequest.get_limit()
						break
					else:
						marketsMessages, weight = await markets(message, messageRequest, requestSlice)
						sentMessages += marketsMessages
						totalWeight += weight - 1

						rateLimited[messageRequest.authorId] = rateLimited.get(messageRequest.authorId, 0) + weight - 2

				await database.document("discord/statistics").set({_snapshot: {"mk": Increment(totalWeight)}}, merge=True)
				await finish_request(message, messageRequest, totalWeight, sentMessages)

			elif messageRequest.content.startswith("x "):
				requestSlice = messageRequest.content.split(" ", 1)[1]
				forceDelete = False
				if messageRequest.content.startswith(("x ichibot", "x ichi", "x login")):
					await deprecation_message(message, "ichibot login", isGone=True)
				elif messageRequest.guildId == -1 or messageRequest.marketBias == "crypto" or len(messageRequest.accountProperties.get("apiKeys", {}).keys()) != 0:
					await process_ichibot_command(message, messageRequest, requestSlice)
					forceDelete = True

				await database.document("discord/statistics").set({_snapshot: {"x": Increment(1)}}, merge=True)
				await finish_request(message, messageRequest, 0, [], force=forceDelete)

			elif messageRequest.content.startswith("paper "):
				await deprecation_message(message, "paper", isGone=True)
			
			elif messageRequest.content.startswith("/vote ") and messageRequest.authorId in [361916376069439490, 362371656267595778, 430223866993049620]:
				await deprecation_message(message, "vote` as a `slash command", isGone=True)

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception()


# -------------------------
# Message actions
# -------------------------

async def finish_request(message, messageRequest, weight, sentMessages, force=False):
	await sleep(60)
	if weight != 0 and messageRequest.authorId in rateLimited:
		rateLimited[messageRequest.authorId] -= weight
		if rateLimited[messageRequest.authorId] < 1: rateLimited.pop(messageRequest.authorId, None)

	if (len(sentMessages) != 0 and messageRequest.autodelete) or force:
		try: await message.delete()
		except: pass


# -------------------------
# Charting
# -------------------------

async def chart(message, messageRequest, requestSlice):
	sentMessages = []
	try:
		arguments = requestSlice.split(" ")

		async with message.channel.typing():
			outputMessage, request = await Processor.process_chart_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper())

			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/charting).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			currentRequest = request.get(request.get("currentPlatform"))
			timeframes = request.pop("timeframes")
			for i in range(request.get("requestCount")):
				for p, t in timeframes.items(): request[p]["currentTimeframe"] = t[i]
				payload, chartText = await Processor.process_task("chart", messageRequest.authorId, request)

				if payload is None:
					errorMessage = "Requested chart for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if chartText is None else chartText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					currentRequest = request.get(payload.get("platform"))
					sentMessages.append(await message.channel.send(content=chartText, file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, messageRequest.authorId, randint(1000, 9999)))))

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
		await unknown_error(message, messageRequest.authorId)
	return (sentMessages, len(sentMessages))

async def heatmap(message, messageRequest, requestSlice):
	sentMessages = []
	try:
		arguments = requestSlice.split(" ")

		async with message.channel.typing():
			outputMessage, request = await Processor.process_heatmap_arguments(messageRequest, arguments)
		
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/heat-maps).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			currentRequest = request.get(request.get("currentPlatform"))
			timeframes = request.pop("timeframes")
			for i in range(request.get("requestCount")):
				for p, t in timeframes.items(): request[p]["currentTimeframe"] = t[i]
				payload, chartText = await Processor.process_task("heatmap", messageRequest.authorId, request)

				if payload is None:
					errorMessage = "Requested heat map is not available." if chartText is None else chartText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Heat map not available", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					currentRequest = request.get(payload.get("platform"))
					sentMessages.append(await message.channel.send(content=chartText, file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, messageRequest.authorId, randint(1000, 9999)))))

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
		await unknown_error(message, messageRequest.authorId)
	return (sentMessages, len(sentMessages))


# -------------------------
# Quotes
# -------------------------

async def price(message, messageRequest, requestSlice):
	sentMessages = []
	try:
		await deprecation_message(message, "p")

		arguments = requestSlice.split(" ")

		async with message.channel.typing():
			outputMessage, request = await Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper())

			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			currentRequest = request.get(request.get("currentPlatform"))
			payload, quoteText = await Processor.process_task("quote", messageRequest.authorId, request)

			if payload is None or "quotePrice" not in payload:
				errorMessage = "Requested price for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
				quoteMessage = await message.channel.send(embed=embed)
				sentMessages.append(quoteMessage)
			else:
				currentRequest = request.get(payload.get("platform"))
				if payload.get("platform") in ["Alternative.me"]:
					embed = discord.Embed(title="{} *({})*".format(payload["quotePrice"], payload["change"]), description=payload.get("quoteConvertedPrice", discord.embeds.EmptyEmbed), color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
					embed.set_footer(text=payload["sourceText"])
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="{}{}".format(payload["quotePrice"], " *({})*".format(payload["change"]) if "change" in payload else ""), description=payload.get("quoteConvertedPrice", discord.embeds.EmptyEmbed), color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
					embed.set_footer(text=payload["sourceText"])
					sentMessages.append(await message.channel.send(embed=embed))

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
		await unknown_error(message, messageRequest.authorId)
	return (sentMessages, len(sentMessages))

# -------------------------
# Details
# -------------------------

async def rankings(message, messageRequest, requestSlice):
	sentMessages = []
	try:
		arguments = requestSlice.split(" ", 2)
		method = arguments[0]

		if method in ["alpha", "requests", "charts"]:
			if messageRequest.statistics_available():
				response = []
				async with message.channel.typing():
					rawData = await database.document("dataserver/statistics").get()
					rawData = rawData.to_dict()
					response = rawData["top"][messageRequest.marketBias][:9:-1]

				embed = discord.Embed(title="Top Alpha Bot requests", color=constants.colors["deep purple"])
				for token in response:
					embed.add_field(name=token["id"], value="Rank {:,.2f}/100".format(token["rank"]), inline=True)
				sentMessages.append(await message.channel.send(embed=embed))

			elif messageRequest.is_pro():
				if not message.author.bot and message.channel.permissions_for(message.author).administrator:
					embed = discord.Embed(title=":pushpin: Alpha Statistics are disabled.", description="You can enable Alpha Statistics feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
					embed.set_author(name="Alpha Statistics", icon_url=static_storage.icon_bw)
					await message.channel.send(embed=embed)
				else:
					embed = discord.Embed(title=":pushpin: Alpha Statistics are disabled.", description="You can enable Alpha Statistics feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
					embed.set_author(name="Alpha Statistics", icon_url=static_storage.icon_bw)
					await message.channel.send(embed=embed)

			else:
				embed = discord.Embed(title=":gem: Alpha Statistics information is available to Alpha Pro users or communities for only $5.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
				embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
				await message.channel.send(embed=embed)

		elif method in ["gainers", "gain", "gains"]:
			response = []
			async with message.channel.typing():
				from pycoingecko import CoinGeckoAPI
				rawData = CoinGeckoAPI().get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, price_change_percentage="24h")
				for e in rawData:
					if e.get("price_change_percentage_24h_in_currency", None) is not None:
						response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
				response = sorted(response, key=lambda k: k["change"], reverse=True)[:10]
			
			embed = discord.Embed(title="Top gainers", color=constants.colors["deep purple"])
			for token in response:
				embed.add_field(name=token["symbol"], value="Gained {:,.2f} %".format(token["change"]), inline=True)
			sentMessages.append(await message.channel.send(embed=embed))

		elif method in ["losers", "loosers", "loss", "losses"]:
			response = []
			async with message.channel.typing():
				from pycoingecko import CoinGeckoAPI
				rawData = CoinGeckoAPI().get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, price_change_percentage="24h")
				for e in rawData:
					if e.get("price_change_percentage_24h_in_currency", None) is not None:
						response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
			response = sorted(response, key=lambda k: k["change"])[:10]
			
			embed = discord.Embed(title="Top losers", color=constants.colors["deep purple"])
			for token in response:
				embed.add_field(name=token["symbol"], value="Lost {:,.2f} %".format(token["change"]), inline=True)
			sentMessages.append(await message.channel.send(embed=embed))

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
		await unknown_error(message, messageRequest.authorId)
	return (sentMessages, len(sentMessages))

async def markets(message, messageRequest, requestSlice):
	sentMessages = []
	try:
		arguments = requestSlice.split(" ")

		async with message.channel.typing():
			outputMessage, request = await Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["CCXT"])

			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			currentRequest = request.get(request.get("currentPlatform"))
			ticker = currentRequest.get("ticker")
			listings, total = await TickerParser.get_listings(ticker.get("base"), ticker.get("quote"))
			if total != 0:
				embed = discord.Embed(color=constants.colors["deep purple"])
				embed.set_author(name="{} listings".format(ticker.get("base")))
				for quote, exchanges in listings:
					embed.add_field(name="{} pair found on {} exchanges".format(quote, len(exchanges)), value="{}".format(", ".join(exchanges)), inline=False)
				sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="`{}` is not listed on any crypto exchange.".format(currentRequest.get("ticker").get("name")), color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
		await unknown_error(message, messageRequest.authorId)
	return (sentMessages, len(sentMessages))


# -------------------------
# Trading
# -------------------------

async def process_ichibot_command(message, messageRequest, requestSlice):
	sentMessages = []
	try:
		if requestSlice == "login":
			embed = discord.Embed(title=":dart: API key preferences are available in your Alpha Account settings.", description="[Sign into you Alpha Account](https://www.alphabotsystem.com/sign-in) and visit [Ichibot preferences](https://www.alphabotsystem.com/account/ichibot) to update your API keys.", color=constants.colors["deep purple"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await message.channel.send(embed=embed)
		
		elif messageRequest.is_registered():
			origin = "{}_{}_ichibot".format(messageRequest.accountId, messageRequest.authorId)

			if origin in Ichibot.sockets:
				socket = Ichibot.sockets.get(origin)
				await socket.send_multipart([messageRequest.accountId.encode(), b"", messageRequest.raw.split(" ", 1)[1].encode()])
				try: await message.add_reaction("✅")
				except: pass

				if requestSlice in ["q", "quit", "exit", "logout"]:
					Ichibot.sockets.pop(origin)
					embed = discord.Embed(title="Ichibot connection has been closed.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					await message.channel.send(embed=embed)
			else:
				embed = discord.Embed(title="Ichibot connection is not open.", description="You can initiate a connection with `x login` followed by the exchange you want to connect to.", color=constants.colors["pink"])
				embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
				missingExchangeMessage = await message.channel.send(embed=embed)

		else:
			embed = discord.Embed(title=":dart: You must have an Alpha Account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), connect your account with your Discord profile, and add an API key.", color=constants.colors["deep purple"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await message.channel.send(embed=embed)

	except CancelledError: pass
	except Exception:
		print(format_exc())
		if environ["PRODUCTION_MODE"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
		await unknown_error(message, messageRequest.authorId)
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

	await ctx.defer()

	_accountProperties = {}
	_guildProperties = await guildProperties.get(_guildId, {})
	if not ctx.author.bot:
		_accountId = await accountProperties.match(_authorId)
		if _accountId is None:
			_accountProperties = await accountProperties.get(str(_authorId), {})
		else:
			_accountProperties = await accountProperties.get(_accountId, {})

	request = MessageRequest(
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
			embed = discord.Embed(title="This Discord community guild was flagged for rebranding Alpha and is therefore violating the Terms of Service. Inability to comply will result in termination of all Alpha branded services.", color=0x000000)
			embed.add_field(name="Terms of service", value="[Read now](https://www.alphabotsystem.com/terms-of-service)", inline=True)
			embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
			await ctx.interaction.edit_original_message(embed=embed)
			return None
		elif not request.guildProperties["settings"]["setup"]["completed"]:
			forceFetch = await database.document("discord/properties/guilds/{}".format(request.guildId)).get()
			forcedFetch = MessageRequest.create_guild_settings(forceFetch.to_dict())
			if forcedFetch["settings"]["setup"]["completed"]:
				request.guildProperties = forcedFetch
				return request
			elif not ctx.bot and ctx.interaction.channel.permissions_for(ctx.author).administrator:
				embed = discord.Embed(title="Hello world!", description="Thanks for adding Alpha Bot to your Discord community, we're thrilled to have you onboard. We think you're going to love everything Alpha Bot can do. Before you start using it, you must complete a short setup process. Sign into your [Alpha Account](https://www.alphabotsystem.com/communities) and visit your [Communities Dashboard](https://www.alphabotsystem.com/communities) to begin.", color=constants.colors["pink"])
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = discord.Embed(title="Hello world!", description="This is Alpha Bot, the most advanced financial bot on Discord. A short setup process hasn't been completed in this Discord community yet. Ask administrators to complete it by signing into their [Alpha Account](https://www.alphabotsystem.com/communities) and visiting their [Communities Dashboard](https://www.alphabotsystem.com/communities).", color=constants.colors["pink"])
				await ctx.interaction.edit_original_message(embed=embed)
			return None

	return request


# -------------------------
# Slash commands
# -------------------------

bot.add_cog(AlphaCommand(bot, create_request, database, logging))
bot.add_cog(AlertCommand(bot, create_request, database, logging))
bot.add_cog(ChartCommand(bot, create_request, database, logging))
bot.add_cog(DepthCommand(bot, create_request, database, logging))
bot.add_cog(PriceCommand(bot, create_request, database, logging))
bot.add_cog(VolumeCommand(bot, create_request, database, logging))
bot.add_cog(ConvertCommand(bot, create_request, database, logging))
bot.add_cog(DetailsCommand(bot, create_request, database, logging))
bot.add_cog(PaperCommand(bot, create_request, database, logging))
bot.add_cog(IchibotCommand(bot, create_request, database, logging))
bot.add_cog(CopeVoteCommand(bot, create_request, database, logging))


# -------------------------
# Error handling
# -------------------------

async def unknown_error(ctx, authorId):
	embed = discord.Embed(title="Looks like something went wrong. The issue has been reported.", color=constants.colors["gray"])
	embed.set_author(name="Something went wrong", icon_url=static_storage.icon_bw)
	try: await ctx.channel.send(embed=embed)
	except: return

async def deprecation_message(ctx, command, isGone=False):
	if isGone:
		embed = discord.Embed(title=f"Alpha is transitioning to slash commands as is required by upcoming Discord changes. Use `/{command}` instead of the old syntax.", color=constants.colors["red"])
		embed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fslash-commands.gif?alt=media&token=32e05ba1-9b06-47b1-a037-d37036b382a6")
		try: await ctx.channel.send(embed=embed)
		except: return
	else:
		embed = discord.Embed(title=f"Alpha is transitioning to slash commands as is required by upcoming Discord changes. Use `/{command}` to avoid this warning. Old syntax will no longer work after depreciation <t:1651276800:R>.", color=constants.colors["red"])
		embed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fslash-commands.gif?alt=media&token=32e05ba1-9b06-47b1-a037-d37036b382a6")
		try: await ctx.channel.send(embed=embed)
		except: return

async def hold_up(task, request):
	embed = discord.Embed(title="Only up to {:d} requests are allowed per command.".format(int(request.get_limit() / 2)), color=constants.colors["gray"])
	embed.set_author(name="Too many requests", icon_url=static_storage.icon_bw)
	await task.channel.send(embed=embed)


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

rateLimited = {}

discordSettingsLink = snapshots.document("discord/settings").on_snapshot(update_alpha_settings)
discordMessagesLink = snapshots.collection("discord/properties/messages").on_snapshot(process_alpha_messages)

@bot.event
async def on_ready():
	print("[Startup]: Alpha Bot is online")

	try:
		while not await accountProperties.check_status() or not await guildProperties.check_status():
			await sleep(15)
		botStatus[0] = True
		await bot.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="alphabotsystem.com"))
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