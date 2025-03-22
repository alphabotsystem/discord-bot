from os import environ, _exit
environ["PRODUCTION"] = environ["PRODUCTION"] if "PRODUCTION" in environ and environ["PRODUCTION"] else ""
botId = -1 if len(environ["HOSTNAME"].split("-")) != 3 else int(environ["HOSTNAME"].split("-")[-1])

from time import time
from datetime import datetime, timezone
from requests import post
from asyncio import CancelledError, sleep, gather, wait, create_task
from traceback import format_exc

from discord import AutoShardedBot, Embed, Intents, CustomActivity, Status, ActivityType, MessageType
from discord.ext import tasks
from discord.errors import NotFound
from google.cloud.firestore import AsyncClient as FirestoreAsyncClient
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.firestore import Increment
from google.cloud.firestore import Query
from google.cloud.error_reporting import Client as ErrorReportingClient

from assets import static_storage
from helpers import constants

from DatabaseConnector import DatabaseConnector
from CommandRequest import CommandRequest

from commands.alerts import AlertCommand
from commands.charts import ChartCommand
from commands.convert import ConvertCommand
from commands.depth import DepthCommand
from commands.details import DetailsCommand
from commands.flow import FlowCommand
from commands.heatmaps import HeatmapCommand
from commands.ichibot import IchibotCommand, Ichibot
from commands.layout import LayoutCommand
from commands.lookup import LookupCommand
from commands.paper import PaperCommand
from commands.prices import PriceCommand
from commands.schedule import ScheduleCommand
from commands.volume import VolumeCommand


database = FirestoreAsyncClient()
logging = ErrorReportingClient(service="discord")
snapshots = FirestoreClient()


# -------------------------
# Initialization
# -------------------------

intents = Intents.none()
intents.dm_messages = True
intents.guild_messages = True
intents.guilds = True
intents.integrations = True
intents.webhooks = True

bot = AutoShardedBot(intents=intents, chunk_guilds_at_startup=False, max_messages=None, status=Status.idle, activity=CustomActivity(name="www.alpha.bot"))


# -------------------------
# Guild count & management
# -------------------------

@bot.event
async def on_guild_join(guild):
	# Method should not run on licensed bots
	if bot.user.id not in constants.PRIMARY_BOTS:
		print(f"{bot.user.name} Bot ({bot.user.id}) joined {guild.name} ({guild.id})")
		return

	try:
		if guild.id in constants.bannedGuilds:
			await guild.leave()
			return
		properties = await guild_secure_fetch(guild.id)
		properties.pop("connection", None)
		properties = CommandRequest.create_guild_settings(properties)
		await database.document(f"discord/properties/guilds/{guild.id}").set(properties)
		await update_guild_count()
	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception(user=str(guild.id))

@bot.event
async def on_guild_remove(guild):
	# Method should not run on licensed bots
	if bot.user.id not in constants.PRIMARY_BOTS:
		print(f"{bot.user.name} Bot ({bot.user.id}) left {guild.name} ({guild.id})")
		return

	try:
		await update_guild_count()
	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception(user=str(guild.id))

@tasks.loop(hours=8.0)
async def update_guild_count():
	await bot.wait_until_ready()

	# Method should not run on licensed bots
	if bot.user.id not in constants.PRIMARY_BOTS: return
	# Method should only run in production and after the guild cache is populated
	if not environ["PRODUCTION"] or len(bot.guilds) < 25000: return

	t = datetime.now().astimezone(timezone.utc)
	await database.document("discord/statistics").set({"{}-{:02d}".format(t.year, t.month): {"servers": len(bot.guilds)}}, merge=True)
	post(f"https://top.gg/api/bots/{bot.user.id}/stats", data={"server_count": len(bot.guilds)}, headers={"Authorization": environ["TOPGG_KEY"]})

@tasks.loop(hours=12.0)
async def update_paid_guilds():
	await bot.wait_until_ready()

	# Method should not run on licensed bots
	if bot.user.id not in constants.PRIMARY_BOTS: return
	# Method should only run in production and after the guild cache is populated
	if not environ["PRODUCTION"]: return

	await bot.wait_until_ready()

	BLACKLIST = ["ebOX1w1N2DgMtXVN978fnL0FKCP2"]

	try:
		response = await database.collection("accounts").order_by("customer.subscriptions", direction=Query.DESCENDING).limit(200).get()
		ids = set()
		guilds = []

		for account in response:
			if account.id in BLACKLIST: continue
			properties = account.to_dict()

			for feature in properties["customer"]["slots"]:
				for guildId in properties["customer"]["slots"][feature].keys():
					if guildId != "personal" and guildId not in ids:
						ids.add(guildId)
						try: guild = await bot.fetch_guild(int(guildId), with_counts=True)
						except: continue
						guilds.append(guild)

		guilds.sort(key=lambda g: g.approximate_member_count, reverse=True)
		icons = [{"url": g.icon.url, "name": g.name, "members": g.approximate_member_count} for g in guilds if g is not None and g.icon is not None]

		await database.document("examples/servers").set({"paid": icons})
	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception()


# -------------------------
# Database management
# -------------------------

def update_settings(s, changes, timestamp):
	global settings
	settings = s[0].to_dict()


# -------------------------
# Message processing
# -------------------------

def process_messages(pendingMessages, changes, timestamp):
	# Method should only run in production
	if not environ["PRODUCTION"]: return

	try:
		for change in changes:
			message = change.document.to_dict()
			if change.type.name in ["ADDED", "MODIFIED"]:
				bot.loop.create_task(send_messages(change.document.id, message))

	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception()

async def send_messages(messageId, message):
	await bot.wait_until_ready()

	# Method should only run if the message is addressed to the right bot
	if message["botId"] != str(bot.user.id): return

	try:
		print(f"Sending message: {messageId}")

		content = None
		embed = Embed(title=message["title"], color=message["color"])
		if message.get("description") is not None: embed.description = message.get("description")
		if message.get("tag") is not None: content = f"<@&{message.get('tag')}>"
		if message.get("subtitle") is not None: embed.set_author(name=message["subtitle"], icon_url=message.get("icon", bot.user.avatar.url))
		if message.get("image") is not None: embed.set_image(url=message["image"])
		if message.get("url") is not None: embed.url = message["url"]

		destinationUser = None
		destinationChannel = None
		backupUser = None
		backupChannel = None
		error = ""

		if message.get("user") is not None:
			try:
				destinationUser = bot.get_user(int(message["user"]))
				if destinationUser is None:
					destinationUser = await bot.fetch_user(int(message["user"]))
			except: print(format_exc())
			try:
				backupChannel = bot.get_channel(int(message["backupChannel"]))
				if backupChannel is None:
					backupChannel = await bot.fetch_channel(int(message["backupChannel"]))
			except: print(format_exc())
		else:
			try:
				destinationChannel = bot.get_channel(int(message["channel"]))
				if destinationChannel is None:
					destinationChannel = await bot.fetch_channel(int(message["channel"]))
			except: print(format_exc())
			try:
				backupUser = bot.get_user(int(message["backupUser"]))
				if backupUser is None:
					backupUser = await bot.fetch_user(int(message["backupUser"]))
			except: print(format_exc())

		if destinationUser is not None:
			try:
				await destinationUser.send(embed=embed)
				await database.document(f"discord/properties/messages/{messageId}").delete()
				return
			except:
				print(format_exc())
		elif destinationChannel is not None:
			try:
				await destinationChannel.send(content=content, embed=embed)
				await database.document(f"discord/properties/messages/{messageId}").delete()
				return
			except Exception as e:
				print(format_exc())
				error = e.text.lower() if hasattr(e, 'text') else str(e)
				print(error)

		if backupChannel is not None:
			try:
				mentionText = f"<@!{message['user']}>, you weren't reachable via DMs!" if destinationUser is None else None
				await backupChannel.send(content=mentionText, embed=embed)
				await database.document(f"discord/properties/messages/{messageId}").delete()
				return
			except:
				print(format_exc())
		elif backupUser is not None:
			try:
				await backupUser.send(content=f"The alert could not be sent into the channel that was initially requested. Reason: `{error}`", embed=embed)
				await database.document(f"discord/properties/messages/{messageId}").delete()
				return
			except:
				print(format_exc())

		print("Could not send message to any destination.")

	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception()


# -------------------------
# Job functions
# -------------------------

@tasks.loop(minutes=60.0)
async def security_check():
	await bot.wait_until_ready()

	# Method should not run on licensed bots
	if bot.user.id not in constants.PRIMARY_BOTS: return
	# Method should only run after the guild cache is populated
	if len(bot.guilds) < 25000: return

	try:
		guildIds = [str(e.id) for e in bot.guilds]

		for guildId in list(settings["nicknames"].keys()):
			if guildId not in guildIds:
				settings["nicknames"].pop(guildId)

		for guild in bot.guilds:
			if guild.id in constants.bannedGuilds:
				await guild.leave()

			guildId = str(guild.id)
			if guild.me is not None:
				if guildId in settings["nicknames"]:
					if guild.me.nick is None or guild.me.nick in settings["nicknameWhitelist"]:
						settings["nicknames"].pop(guildId)
					elif settings["nicknames"][guildId].get("nickname") != guild.me.nick or settings["nicknames"][guildId]["server name"] != guild.name:
						settings["nicknames"][guildId] = {"nickname": guild.me.nick, "server name": guild.name, "allowed": None}
				elif guild.me.nick is not None and guild.me.nick not in settings["nicknameWhitelist"]:
					settings["nicknames"][guildId] = {"nickname": guild.me.nick, "server name": guild.name, "allowed": None}

		if environ["PRODUCTION"]:
			await database.document("discord/settings").set(settings)

	except CancelledError: pass
	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception()

@tasks.loop(minutes=15.0)
async def database_sanity_check():
	await bot.wait_until_ready()

	# Method should not run on licensed bots
	if bot.user.id not in constants.PRIMARY_BOTS: return
	# Method should only run in production and after the guild cache is populated
	if not environ["PRODUCTION"] or len(bot.guilds) < 25000: return

	try:
		databaseKeys = set(await guildProperties.keys())
		if databaseKeys is None: return

		guilds = set([str(g.id) for g in bot.guilds])
		difference = guilds.symmetric_difference(databaseKeys)

		tasks = []
		for guildId in difference:
			if guildId not in guilds and int(guildId) not in constants.LICENSED_BOTS:
				tasks.append(create_task(database.document(f"discord/properties/guilds/{guildId}").set({"stale": {"count": Increment(1), "timestamp": time()}}, merge=True)))

		for guildId in difference:
			if guildId not in databaseKeys:
				properties = await guild_secure_fetch(guildId)
				if not properties:
					tasks.append(create_task(database.document(f"discord/properties/guilds/{guildId}").set(CommandRequest.create_guild_settings({}))))

		if len(tasks) > 0:
			await wait(tasks)

	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception()

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
		# Skip messages in servers, messages with empty content field, messages from self
		if message.clean_content == "" or message.type != MessageType.default or message.author == bot.user: return

		# Ignore if user is banned
		if message.author.id in constants.blockedUsers: return

		[accountId, user] = await gather(
			accountProperties.match(message.author.id),
			accountProperties.get(str(message.author.id), {})
		)

		commandRequest = CommandRequest(
			raw=message.clean_content,
			content=message.clean_content.lower(),
			accountId=accountId,
			authorId=message.author.id,
			accountProperties=user,
		)
		_snapshot = "{}-{:02d}".format(message.created_at.year, message.created_at.month)

		# Ichibot should not run on licensed bots
		if bot.user.id in constants.PRIMARY_BOTS and commandRequest.content.startswith("x ") and message.guild is not None:
			await process_ichibot_command(message, commandRequest, commandRequest.content.split(" ", 1)[1])
			await database.document("discord/statistics").set({_snapshot: {"x": Increment(1)}}, merge=True)

	except CancelledError: pass
	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception()


# -------------------------
# Ichibot
# -------------------------

async def process_ichibot_command(message, commandRequest, requestSlice):
	sentMessages = []
	try:
		if requestSlice == "login":
			embed = Embed(title=":dart: API key preferences are available in your Alpha.bot account settings.", description="[Sign into you Alpha.bot account](https://www.alpha.bot/login) and visit [Ichibot preferences](https://www.alpha.bot/account/trading) to update your API keys.", color=constants.colors["deep purple"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await message.channel.send(embed=embed)

		elif commandRequest.is_registered():
			origin = f"{commandRequest.accountId}_{commandRequest.authorId}_ichibot"

			if origin in Ichibot.sockets:
				socket = Ichibot.sockets.get(origin)
				await socket.send_multipart([commandRequest.accountId.encode(), b"", commandRequest.raw.split(" ", 1)[1].encode()])

				if requestSlice in ["q", "quit", "exit"]:
					Ichibot.sockets.pop(origin)
					embed = Embed(title="Ichibot connection has been closed.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					await message.channel.send(embed=embed)
			else:
				embed = Embed(title="Ichibot connection is not open.", description="You can initiate a connection with </ichibot login:930915616188166225>.", color=constants.colors["pink"])
				embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
				missingExchangeMessage = await message.channel.send(embed=embed)

		else:
			embed = Embed(title=":dart: You must have an Alpha.bot account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alpha.bot/signup). If you already signed up, [sign in](https://www.alpha.bot/login), connect your account with your Discord profile, and add an API key.", color=constants.colors["deep purple"])
			embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
			await message.channel.send(embed=embed)

	except CancelledError: pass
	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
		await unknown_error(message, commandRequest.authorId)
	return (sentMessages, len(sentMessages))


# -------------------------
# Slash command request
# -------------------------

async def create_request(ctx, autodelete=-1):
	start = time()
	authorId = ctx.author.id
	guildId = ctx.guild.id if ctx.guild is not None else -1
	channelId = ctx.channel.id if ctx.channel is not None else -1

	# Ignore if user if locked in a prompt, or banned
	if authorId in constants.blockedUsers or guildId in constants.blockedGuilds: return

	# Check if the bot has the permission to operate in this guild
	if bot.user.id not in constants.PRIMARY_BOTS and guildId not in constants.LICENSED_BOTS: return

	[accountId, user, guild] = await gather(
		accountProperties.match(authorId),
		accountProperties.get(str(authorId), {}),
		guildProperties.get(guildId, {})
	)
	databaseCheckpoint = time()

	request = CommandRequest(
		accountId=accountId,
		authorId=authorId,
		channelId=channelId,
		guildId=guildId,
		accountProperties=user,
		guildProperties=guild,
		autodelete=autodelete,
		origin="default" if bot.user.id in constants.PRIMARY_BOTS else bot.user.id
	)
	request.set_delay("database", databaseCheckpoint - start)

	if request.guildId != -1 and bot.user.id == 401328409499664394:
		branding = settings["nicknames"].get(str(request.guildId), {"allowed": True, "nickname": None})
		if branding["allowed"] == False and ctx.guild.me.nick == branding["nickname"]:
			embed = Embed(title="This Discord community guild was flagged for re-branding Alpha.bot and is therefore violating the Terms of Service.", description="Note that you are allowed to change the nickname of the bot as long as it is neutral. If you wish to present the bot with your own branding, you have to purchase a [Bot License](https://www.alpha.bot/pro/bot-license). Alpha.bot will continue to operate normally, if you remove the nickname.", color=0x000000)
			embed.add_field(name="Terms of service", value="[Read now](https://www.alpha.bot/terms-of-service)", inline=True)
			embed.add_field(name="Alpha.bot support Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
			try: await ctx.respond(embed=embed)
			except NotFound: pass
			return None
		elif not request.guildProperties["settings"]["setup"]["completed"]:
			forceFetch = await database.document(f"discord/properties/guilds/{request.guildId}").get()
			forcedFetch = CommandRequest.create_guild_settings(forceFetch.to_dict())
			if forcedFetch["settings"]["setup"]["completed"]:
				request.guildProperties = forcedFetch
				return request
			elif not ctx.bot and ctx.interaction.permissions.administrator:
				embed = Embed(title="Hello world!", description="Thanks for adding Alpha.bot to your Discord community, we're thrilled to have you onboard. We think you're going to love everything Alpha.bot can do. Before you start using it, you must complete a short setup process. Sign into your [Alpha.bot account](https://www.alpha.bot/communities) and visit your [Communities Dashboard](https://www.alpha.bot/communities) to begin.", color=constants.colors["pink"])
				try: await ctx.respond(embed=embed)
				except NotFound: pass
			else:
				embed = Embed(title="Hello world!", description="This is Alpha.bot, the most popular financial bot on Discord. A short setup process hasn't been completed in this Discord community yet. Ask administrators to complete it by signing into their [Alpha.bot account](https://www.alpha.bot/communities) and visiting their [Communities Dashboard](https://www.alpha.bot/communities).", color=constants.colors["pink"])
				try: await ctx.respond(embed=embed)
				except NotFound: pass
			return None

	return request


# -------------------------
# Slash commands
# -------------------------

bot.add_cog(AlertCommand(bot, create_request, database, logging))
bot.add_cog(ChartCommand(bot, create_request, database, logging))
bot.add_cog(ConvertCommand(bot, create_request, database, logging))
bot.add_cog(DepthCommand(bot, create_request, database, logging))
bot.add_cog(DetailsCommand(bot, create_request, database, logging))
# bot.add_cog(FlowCommand(bot, create_request, database, logging))
bot.add_cog(HeatmapCommand(bot, create_request, database, logging))
bot.add_cog(LayoutCommand(bot, create_request, database, logging))
bot.add_cog(LookupCommand(bot, create_request, database, logging))
bot.add_cog(PaperCommand(bot, create_request, database, logging))
bot.add_cog(PriceCommand(bot, create_request, database, logging))
bot.add_cog(ScheduleCommand(bot, create_request, database, logging))
bot.add_cog(VolumeCommand(bot, create_request, database, logging))

# -------------------------
# Special commands
# -------------------------

if botId == -1:
	bot.add_cog(IchibotCommand(bot, create_request, database, logging))


# -------------------------
# Error handling
# -------------------------

async def unknown_error(ctx, authorId):
	embed = Embed(title="Looks like something went wrong. The issue has been reported.", color=constants.colors["gray"])
	embed.set_author(name="Something went wrong", icon_url=static_storage.error_icon)
	try: await ctx.channel.send(embed=embed)
	except: return


# -------------------------
# Startup
# -------------------------

settings = {}
accountProperties = DatabaseConnector(mode="account")
guildProperties = DatabaseConnector(mode="guild")
Ichibot.logging = logging

discordSettingsLink = snapshots.document("discord/settings").on_snapshot(update_settings)
discordMessagesLink = snapshots.collection("discord/properties/messages").on_snapshot(process_messages)

@bot.event
async def on_ready():
	print(f"[Startup]: {bot.user.name} Bot ({bot.user.id}) is online")

	try:
		if bot.user.id == 401328409499664394:
			await bot.change_presence(status=Status.online, activity=CustomActivity(name="www.alpha.bot"))
		else:
			await bot.change_presence(status=Status.online, activity=None)
	except:
		print(format_exc())
		if environ["PRODUCTION"]: logging.report_exception()
		_exit(1)

	if not update_guild_count.is_running():
		update_guild_count.start()
	if not update_paid_guilds.is_running():
		update_paid_guilds.start()
	if not security_check.is_running():
		security_check.start()
	if not database_sanity_check.is_running():
		database_sanity_check.start()

	if not environ["PRODUCTION"] or botId == -1:
		print(f"[Startup]: {bot.user.name} Bot ({bot.user.id}) startup complete")
	else:
		print(f"[Startup]: licensed bot #{botId}: {bot.user.name} ({bot.user.id}) startup complete")


# -------------------------
# Login
# -------------------------

token = None
if not environ["PRODUCTION"]:
	token = environ["DISCORD_DEVELOPMENT_TOKEN"]
elif botId == -1:
	token = environ["DISCORD_PRODUCTION_TOKEN"]
elif botId == 0:
	token = environ["TOKEN_N8V1MEBUJFSVP4IQMUXYYIEDFYI1"]
elif botId == 1:
	token = environ["TOKEN_NI7GCMTB8LGCLNV7H2YEJ2VUFHI1"]
elif botId == 2:
	token = environ["TOKEN_LLZ0V7CAZXVSVC0M1MVQCKOXCJV2"]
elif botId == 3:
	token = environ["TOKEN_SHDNTSTH4TPFNG0CO1LBVDANLVO2"]
elif botId == 4:
	token = environ["TOKEN_LYSQMRSJONMYQI8KSGXCMLO54IE2"]
elif botId == 5:
	token = environ["TOKEN_UIVTZSUV8YD74TLPRGQBIGTWNQG2"]
elif botId == 6:
	token = environ["TOKEN_26FIYWEEZNHCMSIGFI81BMBBFER2"]
elif botId == 7:
	token = environ["TOKEN_018IAYNLAZRVJZM1BA44B7AKL872"]
elif botId == 8:
	token = environ["TOKEN_RWU79SZBNJUFMRPQBGJ3ZTNLMWA2"]
elif botId == 9:
	token = environ["TOKEN_WJLIPYYYUTZZLVHYZGXYJZ2KICD2"]
elif botId == 10:
	token = environ["TOKEN_A8VQZAU7BXTBTP27ISAJQJTCSFF1"]
elif botId == 11:
	token = environ["TOKEN_QWMT0OT4G0TFBW5N27F6VGKHWQ82"]
elif botId == 12:
	token = environ["TOKEN_NUJRDT6T3WTEQFTKMU2IAZY0RVL2"]

bot.loop.run_until_complete(bot.start(token))