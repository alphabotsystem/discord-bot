from os import environ, _exit
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
from google.cloud.firestore import AsyncClient as FirestoreAsnycClient
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.firestore import Increment, DELETE_FIELD
from google.cloud.error_reporting import Client as ErrorReportingClient

from assets import static_storage
from helpers.utils import Utils
from helpers import constants

from TickerParser import TickerParser
from IchibotRelay import IchibotRelay
from Processor import Processor
from DatabaseConnector import DatabaseConnector
from engine.assistant import Assistant
from engine.presets import Presets
from engine.trader import PaperTrader

from MessageRequest import MessageRequest


database = FirestoreAsnycClient()
snapshots = FirestoreClient()

BETA_SERVERS = [
	414498292655980583, 849579081800482846, 779004662157934665, 707238867840925706, 493617351216857088, 642039300208459796, 704211103139233893, 710291265689878669, 614609141318680581, 719265732214390816, 788809517818445875, 834195584398524526, 771423228903030804, 778444625639374858, 813915848510537728, 816446013274718209, 807785366526230569, 817764642423177227, 618471986586189865, 663752459424104456, 697085377802010634, 719215888938827776, 726478017924169748, 748813732620009503, 814738213599445013, 856938896713580555, 793014166553755698, 838822602708353056, 837526018088239105, 700113101353123923, 732072413969383444, 784964427962777640, 828430973775511575, 838573421281411122, 625105491743473689, 469530035645317120, 814256366067253268, 848053870197473290, 802692756773273600, 782315810621882369, 597269708345180160, 821150986567548948, 737326609329291335, 746804569303941281, 825933090311503905, 804771454561681439, 827433009598038016, 830534974381752340, 824300337887576135, 747441663193907232, 832625164801802261, 530964559801090079, 831928179299844166, 812819897305399296, 460731020245991424, 829028161983348776, 299922493924311054, 608761795531767814, 336233207269687299, 805453662746968064, 379077201775296513, 785702300886499369, 690135278978859023
]
ICHIBOT_TESTING = [
	414498292655980583, 460731020245991424
]
COPE_CONSENSUS_VOTE_TESTING = [
	824445607585775646, 414498292655980583
]


class Alpha(discord.AutoShardedClient):
	botStatus = []

	assistant = Assistant()
	paperTrader = PaperTrader()
	ichibotRelay = IchibotRelay()

	alphaSettings = {}
	accountProperties = DatabaseConnector(mode="account")
	guildProperties = DatabaseConnector(mode="guild")

	rateLimited = {}
	lockedUsers = set()
	usedPresetsCache = {}
	maliciousUsers = {}

	discordSettingsLink = None
	discordMessagesLink = None

	ichibotSockets = {}


	# -------------------------
	# Startup
	# -------------------------

	def prepare(self):
		self.botStatus = [False, False]

		Processor.clientId = b"discord_alpha"
		self.logging = ErrorReportingClient(service="discord")

		self.discordSettingsLink = snapshots.document("discord/settings").on_snapshot(self.update_alpha_settings)
		self.discordMessagesLink = snapshots.collection("discord/properties/messages").on_snapshot(self.process_alpha_messages)

		print("[Startup]: database initialization complete")

	async def on_ready(self):
		print("[Startup]: Alpha Bot is online")

		try:
			await self.create_invite()

			while not await self.accountProperties.check_status() or not await self.guildProperties.check_status():
				await sleep(15)
			self.botStatus[0] = True
			await client.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="alphabotsystem.com"))
		except:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception()
			_exit(1)
		print("[Startup]: Alpha Bot startup complete")

	def is_bot_ready(self):
		return all(self.botStatus)


	# -------------------------
	# Guild count & management
	# -------------------------

	async def on_guild_join(self, guild):
		try:
			if guild.id in constants.bannedGuilds:
				await guild.leave()
				return
			properties = await self.guild_secure_fetch(guild.id)
			properties = MessageRequest.create_guild_settings(properties)
			await database.document("discord/properties/guilds/{}".format(guild.id)).set(properties)
			await self.update_guild_count()
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=str(guild.id))

	async def on_guild_remove(self, guild):
		try:
			await self.update_guild_count()
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=str(guild.id))

	async def update_guild_count(self):
		if environ["PRODUCTION_MODE"] and len(client.guilds) > 12000:
			t = datetime.now().astimezone(utc)
			await database.document("discord/statistics").set({"{}-{:02d}".format(t.year, t.month): {"servers": len(client.guilds)}}, merge=True)
			post("https://top.gg/api/bots/{}/stats".format(client.user.id), data={"server_count": len(client.guilds)}, headers={"Authorization": environ["TOPGG_KEY"]})


	# -------------------------
	# Job queue
	# -------------------------

	async def job_queue(self):
		while True:
			try:
				await sleep(Utils.seconds_until_cycle())
				if not self.is_bot_ready() or not await self.guildProperties.check_status() or not await self.accountProperties.check_status(): continue
				t = datetime.now().astimezone(utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "15m" in timeframes:
					await self.database_sanity_check()
					await self.update_guild_count()
				if "1H" in timeframes:
					await self.create_invite()
					await self.security_check()

			except CancelledError: return
			except Exception:
				print(format_exc())
				if environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Database management
	# -------------------------

	def update_alpha_settings(self, settings, changes, timestamp):
		self.alphaSettings = settings[0].to_dict()
		self.botStatus[1] = True


	# -------------------------
	# Message processing
	# -------------------------

	def process_alpha_messages(self, pendingMessages, changes, timestamp):
		if len(changes) == 0 or not environ["PRODUCTION_MODE"]: return
		try:
			for change in changes:
				message = change.document.to_dict()
				if change.type.name in ["ADDED", "MODIFIED"]:
					client.loop.create_task(self.send_alpha_messages(change.document.id, message))

		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def send_alpha_messages(self, messageId, message):
		try:
			while not self.botStatus[0]:
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
					destinationUser = client.get_user(int(message["user"]))
					if destinationUser is None:
						destinationUser = await client.fetch_user(int(message["user"]))
				except: pass
			if message.get("channel") is not None:
				try:
					destinationChannel = client.get_channel(int(message["channel"]))
					if destinationChannel is None:
						destinationChannel = await client.fetch_channel(int(message["channel"]))
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
			if environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def process_ichibot_messages(self, origin, author):
		try:
			socket = self.ichibotSockets.get(origin)

			while origin in self.ichibotSockets:
				try:
					messageContent = "```ruby"

					while True:
						try: [messenger, message] = await socket.recv_multipart(flags=NOBLOCK)
						except: break
						if messenger.decode() == "alpha":
							embed = discord.Embed(title=message.decode(), color=constants.colors["gray"])
							embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
							try: await author.send(embed=embed)
							except: pass
						else:
							message = message.decode()
							if len(message) + len(messageContent) + 4 >= 2000:
								messageContent = messageContent[:1997] + "```"
								try: await author.send(content=messageContent)
								except discord.errors.Forbidden: pass
								messageContent = "```ruby"
							messageContent += "\n" + message

					if messageContent != "```ruby":
						messageContent = messageContent[:1997] + "```"
						try: await author.send(content=messageContent)
						except discord.errors.Forbidden: pass
					await sleep(1)

				except:
					print(format_exc())
					if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=origin)

			socket.close()

		except:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=origin)

	# -------------------------
	# Job functions
	# -------------------------

	async def security_check(self):
		try:
			guildNames = [e.name for e in client.guilds]
			guildsToRemove = []
			for key in ["blacklist", "whitelist"]:
				for guild in self.alphaSettings["tosWatchlist"]["nicknames"][key]:
					if guild not in guildNames: guildsToRemove.append(guild)
				for guild in guildsToRemove:
					if guild in self.alphaSettings["tosWatchlist"]["nicknames"][key]: self.alphaSettings["tosWatchlist"]["nicknames"][key].pop(guild)

			botNicknames = []
			for guild in client.guilds:
				if guild.id in constants.bannedGuilds:
					await guild.leave()

				if guild.me is not None:
					isBlacklisted = guild.name in self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]
					isWhitelisted = guild.name in self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"]

					if guild.me.nick is not None:
						if isBlacklisted:
							if guild.me.nick == self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"][guild.name]:
								if guild.me.guild_permissions.change_nickname:
									try:
										await guild.me.edit(nick=None)
										self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
									except: pass
								continue
							else: self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
						if isWhitelisted:
							if guild.me.nick == self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"][guild.name]: continue
							else: self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"].pop(guild.name)

						for i in range(0, len(guild.me.nick.replace(" ", "")) - 2):
							nameSlice = guild.me.nick.lower().replace(" ", "")[i:i+3]
							if nameSlice in guild.name.lower() and nameSlice not in ["the"]:
								botNicknames.append("```{}: {}```".format(guild.name, guild.me.nick))
								break
					else:
						if isBlacklisted: self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
						if isWhitelisted: self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"].pop(guild.name)

			botNicknamesText = "No bot nicknames to review"
			if len(botNicknames) > 0: botNicknamesText = "These guilds might be rebranding Alpha Bot:{}".format("".join(botNicknames))

			if environ["PRODUCTION_MODE"]:
				usageReviewChannel = client.get_channel(571786092077121536)
				botNicknamesMessage = await usageReviewChannel.fetch_message(709335020174573620)
				await botNicknamesMessage.edit(content=botNicknamesText[:2000])

				await database.document("discord/settings").set({"tosWatchlist": self.alphaSettings["tosWatchlist"]}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def database_sanity_check(self):
		if not environ["PRODUCTION_MODE"]: return
		try:
			guilds = await self.guildProperties.keys()
			if guilds is None: return

			guildIds = [str(g.id) for g in client.guilds]

			for guildId in guilds:
				if guildId not in guildIds:
					await database.document("discord/properties/guilds/{}".format(guildId)).set({"stale": {"count": Increment(1), "timestamp": time()}}, merge=True)

			for guildId in guildIds:
				if guildId not in guilds:
					properties = await self.guild_secure_fetch(guildId)
					if not properties:
						await database.document("discord/properties/guilds/{}".format(guildId)).set(MessageRequest.create_guild_settings({}))

		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def guild_secure_fetch(self, guildId):
		properties = await self.guildProperties.get(guildId)

		if properties is None:
			properties = await database.document("discord/properties/guilds/{}".format(guildId)).get()
			properties = properties.to_dict()
			if properties is None: properties = {}

		return properties

	async def create_invite(self):
		try:
			channel = await client.fetch_channel(595515236878909441)
			self.invite = await channel.create_invite(max_age=86400)
		except: pass


	# -------------------------
	# Message handling
	# -------------------------

	async def on_message(self, message):
		try:
			# Skip messages with empty content field, messages from self, or all messages when in startup mode
			if message.clean_content == "" or message.type != discord.MessageType.default or message.author == client.user or not self.is_bot_ready(): return

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
			if _authorId in self.lockedUsers or _authorId in constants.blockedUsers or _guildId in constants.blockedGuilds: return

			_accountProperties = {}
			_guildProperties = await self.guildProperties.get(_guildId, {})
			_checkpoint2 = time() * 1000
			if not message.author.bot:
				if message.webhook_id is None: _accountId = await self.accountProperties.match(_authorId)
				if _accountId is None:
					_accountProperties = await self.accountProperties.get(str(_authorId), {})
				else:
					_accountProperties = await self.accountProperties.get(_accountId, {})
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

			_availablePermissions = None if messageRequest.guildId == -1 else message.guild.me.permissions_in(message.channel)
			hasPermissions = True if messageRequest.guildId == -1 else (_availablePermissions.send_messages and _availablePermissions.embed_links and _availablePermissions.attach_files and _availablePermissions.add_reactions and _availablePermissions.use_external_emojis and _availablePermissions.manage_messages)

			if not messageRequest.content.startswith("preset "):
				messageRequest.content, messageRequest.presetUsed, parsedPresets = Presets.process_presets(messageRequest.content, messageRequest.accountProperties)

				if not messageRequest.presetUsed and messageRequest.guildId in self.usedPresetsCache:
					for preset in self.usedPresetsCache[messageRequest.guildId]:
						if preset["phrase"] == messageRequest.content:
							if preset["phrase"] not in [p["phrase"] for p in parsedPresets]:
								parsedPresets = [preset]
								messageRequest.presetUsed = False
								break

				if messageRequest.presetUsed or len(parsedPresets) != 0:
					if messageRequest.command_presets_available():
						if messageRequest.presetUsed:
							if messageRequest.guildId != -1:
								if messageRequest.guildId not in self.usedPresetsCache: self.usedPresetsCache[messageRequest.guildId] = []
								for preset in parsedPresets:
									if preset not in self.usedPresetsCache[messageRequest.guildId]: self.usedPresetsCache[messageRequest.guildId].append(preset)
								self.usedPresetsCache[messageRequest.guildId] = self.usedPresetsCache[messageRequest.guildId][-3:]

							embed = discord.Embed(title="Running `{}` command from personal preset.".format(messageRequest.content), color=constants.colors["light blue"])
							sentMessages.append(await message.channel.send(embed=embed))
						elif len(parsedPresets) != 0:
							embed = discord.Embed(title="Do you want to add `{}` preset to your account?".format(parsedPresets[0]["phrase"]), description="`{}` → `{}`".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"]), color=constants.colors["light blue"])
							addPresetMessage = await message.channel.send(embed=embed)
							self.lockedUsers.add(messageRequest.authorId)

							def confirm_order(m):
								if m.author.id == messageRequest.authorId:
									response = ' '.join(m.clean_content.lower().split())
									if response in ["y", "yes", "sure", "confirm", "execute"]: return True
									elif response in ["n", "no", "cancel", "discard", "reject"]: raise Exception

							try:
								await client.wait_for('message', timeout=60.0, check=confirm_order)
							except:
								self.lockedUsers.discard(messageRequest.authorId)
								embed = discord.Embed(title="Prompt has been canceled.", description="~~Do you want to add `{}` preset to your account?~~".format(parsedPresets[0]["phrase"]), color=constants.colors["gray"])
								try: await addPresetMessage.edit(embed=embed)
								except: pass
								return
							else:
								self.lockedUsers.discard(messageRequest.authorId)
								messageRequest.content = "preset add {} {}".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"])

					elif messageRequest.is_pro():
						if not message.author.bot and message.author.permissions_in(message.channel).administrator:
							embed = discord.Embed(title=":pushpin: Command Presets are disabled.", description="You can enable Command Presets feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
							embed.set_author(name="Command Presets", icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)
						else:
							embed = discord.Embed(title=":pushpin: Command Presets are disabled.", description="You can enable Command Presets feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
							embed.set_author(name="Command Presets", icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)
						return

					elif messageRequest.is_registered():
						embed = discord.Embed(title=":gem: Command Presets are available to Alpha Pro users or communities for only $1.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
						embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
						await message.channel.send(embed=embed)
						return

			messageRequest.content = Utils.shortcuts(messageRequest.content)
			isCommand = messageRequest.content.startswith(tuple(constants.commandWakephrases))

			if messageRequest.guildId != -1:
				if messageRequest.guildId in self.maliciousUsers:
					if any([e.id in self.maliciousUsers[messageRequest.guildId][0] for e in message.guild.members]) and time() + 60 < self.maliciousUsers[messageRequest.guildId][1]:
						self.maliciousUsers[messageRequest.guildId][1] = time()
						embed = discord.Embed(title="This Discord guild has one or more members disguising as Alpha Bot or one of the team members. Guild admins are advised to take action.", description="Users flagged for impersonation are: {}".format(", ".join(["<@!{}>".format(e.id) for e in self.maliciousUsers])), color=0x000000)
						try: await message.channel.send(embed=embed)
						except: pass

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
					elif len(self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]) != 0 and message.guild.name in self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]:
						embed = discord.Embed(title="This Discord community guild was flagged for rebranding Alpha and is therefore violating the Terms of Service. Inability to comply will result in termination of all Alpha branded services.", color=0x000000)
						embed.add_field(name="Terms of service", value="[Read now](https://www.alphabotsystem.com/terms-of-service)", inline=True)
						embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
						await message.channel.send(embed=embed)
					elif not messageRequest.guildProperties["settings"]["setup"]["completed"]:
						forceFetch = await database.document("discord/properties/guilds/{}".format(messageRequest.guildId)).get()
						forcedFetch = MessageRequest.create_guild_settings(forceFetch.to_dict())
						if forcedFetch["settings"]["setup"]["completed"]:
							messageRequest.guildProperties = forcedFetch
						elif not message.author.bot and message.author.permissions_in(message.channel).administrator:
							embed = discord.Embed(title="Hello world!", description="Thanks for adding Alpha Bot to your Discord community, we're thrilled to have you onboard. We think you're going to love everything Alpha Bot can do. Before you start using it, you must complete a short setup process. Sign into your [Alpha Account](https://www.alphabotsystem.com/communities) and visit your [Communities Dashboard](https://www.alphabotsystem.com/communities) to begin.", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
						else:
							embed = discord.Embed(title="Hello world!", description="This is Alpha Bot, the most advanced financial bot on Discord. A short setup process hasn't been completed in this Discord community yet. Ask administrators to complete it by signing into their [Alpha Account](https://www.alphabotsystem.com/communities) and visiting their [Communities Dashboard](https://www.alphabotsystem.com/communities).", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
						return

			if messageRequest.content.startswith("a "):
				if message.author.bot: return

				command = messageRequest.content.split(" ", 1)[1]
				if message.author.id == 361916376069439490:
					if command == "user":
						await message.delete()
						settings = deepcopy(messageRequest.accountProperties)
						settings.pop("commandPresets", None)
						if "oauth" in settings: settings["oauth"]["discord"].pop("accessToken", None)
						settings.pop("paperTrader", None)
						await message.author.send(content="```json\n{}\n```".format(dumps(settings, option=OPT_INDENT_2).decode()))
					elif command == "guild":
						await message.delete()
						settings = deepcopy(messageRequest.guildProperties)
						settings["addons"]["satellites"].pop("added", None)
						await message.author.send(content="```json\n{}\n```".format(dumps(settings, option=OPT_INDENT_2).decode()))
					elif command == "cache":
						cacheMessage = "From {:%m/%d/%y %H:%M:%S:%f} to {:%m/%d/%y %H:%M:%S:%f}".format(client.cached_messages[0].created_at, client.cached_messages[-1].created_at)
						await message.channel.send(content=cacheMessage)
					elif command == "ping":
						try: outputMessage, _ = await Processor.process_quote_arguments(messageRequest, [], tickerId="BTCUSDT")
						except: outputMessage = "timeout"
						checkpoint4 = time() * 1000
						status4 = "ok" if outputMessage is None else "err"
						created = message.created_at.timestamp() * 1000
						checkpointMessage = "Message received: {}ms\nGuild fetched: {}ms\nUser fetched: {}ms\nParser: {}ms ({})".format(_checkpoint1 - created, _checkpoint2 - created, _checkpoint3 - created, checkpoint4 - created, status4)
						await message.channel.send(content=checkpointMessage)
					elif command.startswith("del"):
						if message.guild.me.guild_permissions.manage_messages:
							parameters = messageRequest.content.split("del ", 1)
							if len(parameters) == 2:
								await message.channel.purge(limit=int(parameters[1]) + 1, bulk=True)
					elif command.startswith("say"):
						say = message.content.split("say ", 1)
						await message.channel.send(content=say[1])

			elif isCommand:
				if messageRequest.content.startswith(("alpha ", "alpha, ", "@alpha ", "@alpha, ")):
					if messageRequest.content == messageRequest.raw.lower():
						rawCaps = messageRequest.raw.split(" ", 1)[1]
					else:
						rawCaps = messageRequest.content.split(" ", 1)[1]

					if len(rawCaps) > 500: return
					fallThrough, response = await client.loop.run_in_executor(None, self.assistant.process_reply, messageRequest.content, rawCaps, messageRequest.guildProperties["settings"]["assistant"]["enabled"])

					if fallThrough:
						if response == "help":
							await self.help(message, messageRequest)
						elif response == "ping":
							await message.channel.send(content="Pong")
						elif response == "pro":
							await message.channel.send(content="Visit https://www.alphabotsystem.com/pro to learn more about Alpha Pro and how to start your free trial.")
						elif response == "invite":
							await message.channel.send(content="https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=applications.commands%20bot&permissions=604372032")
					elif response is not None and response != "":
						await message.channel.send(content=response)

					await database.document("discord/statistics").set({_snapshot: {"alpha": Increment(1)}}, merge=True)

				elif messageRequest.content.startswith("preset "):
					if message.author.bot: return

					requestSlices = split(", preset | preset ", messageRequest.content.split(" ", 1)[1])
					if len(requestSlices) > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						await self.presets(message, messageRequest, requestSlice)

				elif messageRequest.content.startswith("c "):
					if messageRequest.content == "c help":
						embed = discord.Embed(title=":chart_with_upwards_trend: Charts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = split(", c | c |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								if requestSlice.startswith("am ") or requestSlice.startswith("wc ") or requestSlice.startswith("tl ") or requestSlice.startswith("tv ") or requestSlice.startswith("bm ") or requestSlice.startswith("gc ") or requestSlice.startswith("fv "):
									await message.channel.send(embed=discord.Embed(title="We're deprecating the old platform override syntax. Use `c {} {}` from now on instead.".format(requestSlice[3:], requestSlice[:2]), color=constants.colors["gray"]))
									return

								chartMessages, weight = await self.chart(message, messageRequest, requestSlice)
								sentMessages += chartMessages
								totalWeight += weight - 1

								self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

						await database.document("discord/statistics").set({_snapshot: {"c": Increment(totalWeight)}}, merge=True)
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("flow "):
					requestSlices = split(", flow | flow |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							chartMessages, weight = await self.flow(message, messageRequest, requestSlice)
							sentMessages += chartMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"flow": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("hmap "):
					requestSlices = split(", hmap | hmap |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							chartMessages, weight = await self.heatmap(message, messageRequest, requestSlice)
							sentMessages += chartMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"hmap": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("d "):
					requestSlices = split(", d | d |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							chartMessages, weight = await self.depth(message, messageRequest, requestSlice)
							sentMessages += chartMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"d": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith(("alert ", "alerts ")):
					if message.author.bot: return

					requestSlices = split(", alert | alert |, alerts | alerts |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							quoteMessages, weight = await self.alert(message, messageRequest, requestSlice)
							sentMessages += quoteMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"alerts": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("p "):
					requestSlices = split(", p | p |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							quoteMessages, weight = await self.price(message, messageRequest, requestSlice)
							sentMessages += quoteMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"p": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("v "):
					requestSlices = split(", v | v |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							volumeMessages, weight = await self.volume(message, messageRequest, requestSlice)
							sentMessages += volumeMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"v": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("convert "):
					requestSlices = split(", convert | convert |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							convertMessages, weight = await self.convert(message, messageRequest, requestSlice)
							sentMessages += convertMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"convert": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith(("m ", "info")):
					requestSlices = split(", m | m |, info | info |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							detailMessages, weight = await self.details(message, messageRequest, requestSlice)
							sentMessages += detailMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"mcap": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("top"):
					requestSlices = split(", t | t |, top | top |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							rankingsMessages, weight = await self.rankings(message, messageRequest, requestSlice)
							sentMessages += rankingsMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"t": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("mk "):
					requestSlices = split(", mk | mk |, ", messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					if totalWeight > messageRequest.get_limit() / 2:
						await self.hold_up(message, messageRequest)
						return
					for requestSlice in requestSlices:
						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

						if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
							await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
							self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
							totalWeight = messageRequest.get_limit()
							break
						else:
							marketsMessages, weight = await self.markets(message, messageRequest, requestSlice)
							sentMessages += marketsMessages
							totalWeight += weight - 1

							self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + weight - 2

					await database.document("discord/statistics").set({_snapshot: {"mk": Increment(totalWeight)}}, merge=True)
					await self.finish_request(message, messageRequest, totalWeight, sentMessages)

				elif messageRequest.content.startswith("x "):
					requestSlice = messageRequest.content.split(" ", 1)[1]
					forceDelete = False
					if messageRequest.content.startswith(("x ichibot", "x ichi", "x login")):
						await self.initiate_ichibot(message, messageRequest, requestSlice)
					elif messageRequest.guildId == -1 or messageRequest.marketBias == "crypto" or len(messageRequest.accountProperties.get("apiKeys", {}).keys()) != 0:
						await self.process_ichibot_command(message, messageRequest, requestSlice)
						forceDelete = True

					await database.document("discord/statistics").set({_snapshot: {"x": Increment(1)}}, merge=True)
					await self.finish_request(message, messageRequest, 0, [], force=forceDelete)

				elif messageRequest.content.startswith("paper "):
					requestSlices = split(', paper | paper |, ', messageRequest.content.split(" ", 1)[1])
					totalWeight = len(requestSlices)
					for requestSlice in requestSlices:
						if messageRequest.content == "paper balance":
							await self.fetch_paper_balance(message, messageRequest, requestSlice)
						elif messageRequest.content == "paper leaderboard":
							await self.fetch_paper_leaderboard(message, messageRequest, requestSlice)
						elif messageRequest.content == "paper history":
							await self.fetch_paper_orders(message, messageRequest, requestSlice, "history")
						elif messageRequest.content == "paper orders":
							await self.fetch_paper_orders(message, messageRequest, requestSlice, "openOrders")
						elif messageRequest.content == "paper reset":
							await self.reset_paper_balance(message, messageRequest, requestSlice)
						else:
							await self.process_paper_trade(message, messageRequest, requestSlice)

					await database.document("discord/statistics").set({_snapshot: {"paper": Increment(totalWeight)}}, merge=True)
				
				elif messageRequest.content.startswith("/vote ") and messageRequest.authorId in [361916376069439490, 362371656267595778, 430223866993049620]:
					requestSlice = messageRequest.content.split(" ", 1)[1]

					self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) + 2

					if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
						await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
						self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
					else:
						quoteMessages, weight = await self.vote(message, messageRequest, requestSlice)
						sentMessages += quoteMessages

						self.rateLimited[messageRequest.authorId] = self.rateLimited.get(messageRequest.authorId, 0) - 1

					await database.document("discord/statistics").set({_snapshot: {"vote": Increment(1)}}, merge=True)
					await self.finish_request(message, messageRequest, 1, sentMessages)

			elif not message.author.bot:
				if messageRequest.guildProperties["settings"]["assistant"]["enabled"]:
					response = self.assistant.funnyReplies(messageRequest.content)
					if response is not None:
						try: await message.channel.send(content=response)
						except: pass
						await database.document("discord/statistics").set({_snapshot: {"alpha": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Message actions
	# -------------------------

	async def on_raw_reaction_add(self, payload):
		if payload.user_id in [487714342301859854, 401328409499664394] or not hasattr(payload.emoji, "id"): return
		if payload.emoji.id == 875344892291846175 or payload.emoji.id == 875345212258529310:
			await self.ichibotRelay.submit_vote(payload.message_id, payload.channel_id, payload.user_id, int(payload.emoji.id == 875344892291846175))

	async def on_reaction_add(self, reaction, user):
		try:
			if user.id in [487714342301859854, 401328409499664394]: return
			if reaction.message.author.id in [487714342301859854, 401328409499664394]:
				try: users = await reaction.users().flatten()
				except: return
				if reaction.message.author in users:
					if reaction.emoji == "☑":
						if reaction.message.guild is not None:
							guildPermissions = user.permissions_in(reaction.message.channel).manage_messages or user.id in [361916376069439490, 243053168823369728]
							if len(reaction.message.attachments) == 0:
								try: await reaction.message.delete()
								except: pass
							elif str(user.id) in reaction.message.attachments[0].filename or guildPermissions:
								try: await reaction.message.delete()
								except: pass
							else:
								try: await reaction.remove(user)
								except: pass
						else:
							await reaction.message.delete()

					elif reaction.emoji == '❌' and len(reaction.message.embeds) == 1:
						titleText = reaction.message.embeds[0].title
						footerText = reaction.message.embeds[0].footer.text

						if footerText.startswith("Id: ") and titleText.startswith("Price alert"):
							accountId = await self.accountProperties.match(user.id)
							properties = await self.accountProperties.get(accountId)

							matchedId, alertId = footerText.lstrip("Id: ").split("/")
							if matchedId in [accountId, str(user.id)]:
								if matchedId == accountId:
									await database.document("details/marketAlerts/{}/{}".format(accountId, alertId)).delete()
								else:
									await database.document("details/marketAlerts/{}/{}".format(user.id, alertId)).delete()

								embed = discord.Embed(title="Alert deleted", color=constants.colors["gray"])
								embed.set_footer()
								try:
									await reaction.message.edit(embed=embed)
									await reaction.message.clear_reactions()
								except:
									pass
							else:
								try: await reaction.remove(user)
								except: pass

						elif footerText.startswith("Id: ") and titleText.startswith("Paper"):
							accountId = await self.accountProperties.match(user.id)
							properties = await self.accountProperties.get(accountId)

							matchedId, orderId = footerText.lstrip("Id: ").split("/")
							if accountId in [accountId, str(user.id)]:
								order = await database.document("details/openPaperOrders/{}/{}".format(accountId, orderId)).get()
								if order is None: return
								order = order.to_dict()

								currentPlatform = order["request"].get("currentPlatform")
								request = order["request"].get(currentPlatform)
								ticker = request.get("ticker")

								base = ticker.get("base")
								quote = ticker.get("quote")
								if base in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
									baseBalance = properties["paperTrader"]["balance"]
									base = "USD"
								else:
									baseBalance = properties["paperTrader"]["balance"][currentPlatform]
								if quote in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
									quoteBalance = properties["paperTrader"]["balance"]
									quote = "USD"
								else:
									quoteBalance = properties["paperTrader"]["balance"][currentPlatform]

								if order["orderType"] == "buy":
									quoteBalance[quote] += order["amount"] * order["price"]
								elif order["orderType"] == "sell":
									baseBalance[base] += order["amount"]

								await database.document("details/openPaperOrders/{}/{}".format(accountId, orderId)).delete()
								await database.document("accounts/{}".format(accountId)).set({"paperTrader": properties["paperTrader"]}, merge=True)
								
								embed = discord.Embed(title="Paper order has been canceled.", color=constants.colors["gray"])
								embed.set_footer()
								try:
									await reaction.message.edit(embed=embed)
									await reaction.message.clear_reactions()
								except:
									pass
							else:
								try: await reaction.remove(user)
								except: pass

						elif " → `" in titleText and titleText.endswith("`"):
							accountId = await self.accountProperties.match(user.id, user.id)
							properties = await self.accountProperties.get(accountId)

							properties, (titleMessage, _, _) = Presets.update_presets(properties, remove=titleText.split("`")[1])
							if titleMessage == "Preset removed":
								if not "customer" in properties:
									await database.document("discord/properties/users/{}".format(user.id)).set({"commandPresets": properties["commandPresets"]}, merge=True)
								else:
									await database.document("accounts/{}".format(accountId)).set({"commandPresets": properties["commandPresets"]}, merge=True)

								embed = discord.Embed(title="Preset deleted", color=constants.colors["gray"])
								embed.set_footer()
								try:
									await reaction.message.edit(embed=embed)
									await reaction.message.clear_reactions()
								except:
									pass
							else:
								try: await reaction.remove(user)
								except: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def finish_request(self, message, messageRequest, weight, sentMessages, force=False):
		await sleep(60)
		if weight != 0 and messageRequest.authorId in self.rateLimited:
			self.rateLimited[messageRequest.authorId] -= weight
			if self.rateLimited[messageRequest.authorId] < 1: self.rateLimited.pop(messageRequest.authorId, None)

		if (len(sentMessages) != 0 and messageRequest.autodelete) or force:
			try: await message.delete()
			except: pass

		for message in sentMessages:
			try:
				if messageRequest.autodelete: await message.delete()
				else: await message.remove_reaction("☑", message.channel.guild.me)
			except: pass


	# -------------------------
	# Help functionality
	# -------------------------

	async def help(self, message, messageRequest):
		embed = discord.Embed(title=":wave: Introduction", description="Alpha Bot is the world's most popular Discord bot for requesting charts, set price alerts, and more. Using Alpha Bot is as simple as typing a short command into any Discord channel the bot has access to.", color=constants.colors["light blue"])
		embed.add_field(name=":chart_with_upwards_trend: Charts", value="Easy access to on-demand TradingView, TradingLite, GoCharting, Finviz, and Bookmap charts. Learn more about [charting capabilities](https://www.alphabotsystem.com/guide/charting) on our website.", inline=False)
		embed.add_field(name=":dart: Ichibot integration", value="Trade cryptocurrencies with Ichibot, a best-in-class order execution client. Learn more about [Ichibot](https://www.alphabotsystem.com/guide/ichibot) on our website.", inline=False)
		embed.add_field(name=":bell: Price Alerts", value="Price alerts, right in your community. Learn more about [price alerts](https://www.alphabotsystem.com/pro/price-alerts) on our website.", inline=False)
		embed.add_field(name=":joystick: Paper Trader", value="Execute paper trades through Alpha Bot. Learn more about [paper trader](https://www.alphabotsystem.com/guide/paper-trader) on our website.", inline=False)
		embed.add_field(name=":ocean: Alpha Flow", value="Inform your stock options trading with aggregated BlackBox Stocks data. Learn more about [Alpha Flow](https://www.alphabotsystem.com/pro/flow) on our website.", inline=False)
		embed.add_field(name=":money_with_wings: Prices & Asset Details", value="Prices and details for tens of thousands of tickers. Learn more about [prices](https://www.alphabotsystem.com/guide/prices) and [asset details](https://www.alphabotsystem.com/guide/asset-details) on our website.", inline=False)
		embed.add_field(name=":fire: There's more!", value="A [full guide](https://www.alphabotsystem.com/guide) is available on our website.", inline=False)
		embed.add_field(name=":tada: Official Alpha channels", value="[Join our Discord community](https://discord.gg/GQeDE85) or [Follow us on Twitter @AlphaBotSystem](https://twitter.com/AlphaBotSystem).", inline=False)
		embed.set_footer(text="Use \"alpha help\" to pull up this list again.")
		await message.channel.send(embed=embed)


	# -------------------------
	# Command Presets
	# -------------------------

	async def presets(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.replace("`", "").split(" ", 2)
			method = arguments[0]

			if method in ["set", "create", "add"]:
				if len(arguments) == 3:
					if messageRequest.command_presets_available():
						async with message.channel.typing():
							title = arguments[1]
							shortcut = arguments[2]

							if len(title) > 20:
								embed = discord.Embed(title="Shortcut title can be only up to 20 characters long.", color=constants.colors["gray"])
								embed.set_author(name="Shortcut title is too long", icon_url=static_storage.icon_bw)
								sentMessages.append(await message.channel.send(embed=embed))
								return (sentMessages, len(sentMessages))
							elif len(shortcut) > 200:
								embed = discord.Embed(title="Shortcut command can be only up to 200 characters long.", color=constants.colors["gray"])
								embed.set_author(name="Shortcut command is too long.", icon_url=static_storage.icon_bw)
								sentMessages.append(await message.channel.send(embed=embed))
								return (sentMessages, len(sentMessages))

							properties, statusParts = Presets.update_presets(messageRequest.accountProperties, add=title, shortcut=shortcut, messageRequest=messageRequest)
							statusTitle, statusMessage, statusColor = statusParts

							if not messageRequest.is_registered():
								await database.document("discord/properties/users/{}".format(messageRequest.authorId)).set({"commandPresets": properties["commandPresets"]}, merge=True)
							elif messageRequest.serverwide_command_presets_available():
								await database.document("accounts/{}".format(messageRequest.accountId)).set({"commandPresets": properties["commandPresets"]}, merge=True)
							elif messageRequest.personal_command_presets_available():
								await database.document("accounts/{}".format(messageRequest.accountId)).set({"commandPresets": properties["commandPresets"], "customer": {"addons": {"commandPresets": 1}}}, merge=True)

							embed = discord.Embed(title=statusMessage, color=constants.colors[statusColor])
							embed.set_author(name=statusTitle, icon_url=static_storage.icon)
							sentMessages.append(await message.channel.send(embed=embed))

					elif messageRequest.is_pro():
						if not message.author.bot and message.author.permissions_in(message.channel).administrator:
							embed = discord.Embed(title=":pushpin: Command Presets are disabled.", description="You can enable Command Presets feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
							embed.set_author(name="Command Presets", icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)
						else:
							embed = discord.Embed(title=":pushpin: Command Presets are disabled.", description="You can enable Command Presets feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
							embed.set_author(name="Command Presets", icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)

					else:
						embed = discord.Embed(title=":gem: Command Presets are available to Alpha Pro users or communities for only $1.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
						embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
						await message.channel.send(embed=embed)

			elif method in ["list", "all"]:
				if len(arguments) == 1:
					await message.channel.trigger_typing()
					
					if "commandPresets" in messageRequest.accountProperties and len(messageRequest.accountProperties["commandPresets"]) > 0:
						allPresets = {}
						numberOfPresets = len(messageRequest.accountProperties["commandPresets"])
						for preset in messageRequest.accountProperties["commandPresets"]:
							allPresets[preset["phrase"]] = preset["shortcut"]

						for i, phrase in enumerate(sorted(allPresets.keys())):
							embed = discord.Embed(title="`{}` → `{}`".format(phrase, allPresets[phrase]), color=constants.colors["deep purple"])
							embed.set_footer(text="Preset {}/{}".format(i + 1, numberOfPresets))
							presetMessage = await message.channel.send(embed=embed)
							sentMessages.append(presetMessage)
							try: await presetMessage.add_reaction('❌')
							except: pass
					else:
						embed = discord.Embed(title="You don't have any presets.", color=constants.colors["gray"])
						embed.set_author(name="Command Presets", icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))

			else:
				embed = discord.Embed(title="`{}` is not a valid argument.".format(method), description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/pro/command-presets).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Charting
	# -------------------------

	async def chart(self, message, messageRequest, requestSlice):
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
				ichibotRelaySubmitions = []
				autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
				messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
				if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

				timeframes = request.pop("timeframes")
				for i in range(request.get("requestCount")):
					for p, t in timeframes.items(): request[p]["currentTimeframe"] = t[i]
					payload, chartText = await Processor.process_request("chart", messageRequest.authorId, request)

					if payload is None:
						ichibotRelaySubmitions.append({})
						errorMessage = "Requested chart for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if chartText is None else chartText
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					else:
						currentRequest = request.get(payload.get("platform"))
						ichibotRelaySubmitions.append(deepcopy(currentRequest))
						sentMessages.append(await message.channel.send(content=chartText, file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, messageRequest.authorId, randint(1000, 9999)))))

			for chartMessage, currentRequest in zip(sentMessages, ichibotRelaySubmitions):
				if currentRequest.get("ticker", {}).get("isTradable") and messageRequest.guildId in ICHIBOT_TESTING:
					try: await chartMessage.add_reaction("<:ichibot_buy:875344892291846175>")
					except: pass
					try: await chartMessage.add_reaction("<:ichibot_sell:875345212258529310>")
					except: pass
					await self.ichibotRelay.submit_image(chartMessage.id, currentRequest)
				try: await chartMessage.add_reaction("☑")
				except: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def flow(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			embed = discord.Embed(title="Flow command is being updated, and is currently unavailable.", description="All Alpha Pro subscribers using Alpha Flow will receive reimbursment in form of credit, or a refund if requested.", color=constants.colors["gray"])
			sentMessages.append(await message.channel.send(embed=embed))
			return (sentMessages, len(sentMessages))

			arguments = requestSlice.split(" ")

			if messageRequest.flow_available():
				async with message.channel.typing():
					outputMessage, request = await Processor.process_chart_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["Alpha Flow"])
				
					if outputMessage is not None:
						if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
							embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/pro/flow).", color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					currentRequest = request.get(request.get("currentPlatform"))
					autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
					messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
					if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

					timeframes = request.pop("timeframes")
					for i in range(request.get("requestCount")):
						for p, t in timeframes.items(): request[p]["currentTimeframe"] = t[i]
						payload, chartText = await Processor.process_request("chart", messageRequest.authorId, request)

						if payload is None:
							errorMessage = "Requested orderflow data for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if chartText is None else chartText
							embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
							embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
						else:
							currentRequest = request.get(payload.get("platform"))
							sentMessages.append(await message.channel.send(content=chartText, file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, messageRequest.authorId, randint(1000, 9999)))))

				for chartMessage in sentMessages:
					try: await chartMessage.add_reaction("☑")
					except: pass

			elif messageRequest.is_pro():
				if not message.author.bot and message.author.permissions_in(message.channel).administrator:
					embed = discord.Embed(title=":microscope: Alpha Flow is disabled.", description="You can enable Alpha Flow feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
					embed.set_author(name="Alpha Flow", icon_url=static_storage.icon_bw)
					await message.channel.send(embed=embed)
				else:
					embed = discord.Embed(title=":microscope: Alpha Flow is disabled.", description="You can enable Alpha Flow feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
					embed.set_author(name="Alpha Flow", icon_url=static_storage.icon_bw)
					await message.channel.send(embed=embed)

			else:
				embed = discord.Embed(title=":gem: Alpha Flow is available to Alpha Pro users or communities for only $15.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
				embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
				await message.channel.send(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def heatmap(self, message, messageRequest, requestSlice):
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
				autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
				messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
				if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

				timeframes = request.pop("timeframes")
				for i in range(request.get("requestCount")):
					for p, t in timeframes.items(): request[p]["currentTimeframe"] = t[i]
					payload, chartText = await Processor.process_request("heatmap", messageRequest.authorId, request)

					if payload is None:
						errorMessage = "Requested heat map is not available." if chartText is None else chartText
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Heat map not available", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					else:
						currentRequest = request.get(payload.get("platform"))
						sentMessages.append(await message.channel.send(content=chartText, file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, messageRequest.authorId, randint(1000, 9999)))))

			for chartMessage in sentMessages:
				try: await chartMessage.add_reaction("☑")
				except: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def depth(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			async with message.channel.typing():
				outputMessage, request = await Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), excluded=["CoinGecko", "LLD"])

				if outputMessage is not None:
					if not messageRequest.is_muted() and outputMessage != "":
						embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/orderbook-visualizations).", color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))

				currentRequest = request.get(request.get("currentPlatform"))
				autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
				messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
				if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

				payload, chartText = await Processor.process_request("depth", messageRequest.authorId, request)

				if payload is None:
					embed = discord.Embed(title="Requested orderbook visualization for `{}` is not available.".format(currentRequest.get("ticker").get("name")), color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					currentRequest = request.get(payload.get("platform"))
					sentMessages.append(await message.channel.send(content=chartText, file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, messageRequest.authorId, randint(1000, 9999)))))

			for chartMessage in sentMessages:
				try: await chartMessage.add_reaction("☑")
				except: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Quotes
	# -------------------------

	async def alert(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")
			method = arguments[0]

			if method in ["set", "create", "add"] and len(arguments) >= 2:
				if messageRequest.price_alerts_available():
					async with message.channel.typing():
						outputMessage, request = await Processor.process_quote_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), isMarketAlert=True, excluded=["CoinGecko", "LLD"])

						if outputMessage is not None:
							if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
								embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/pro/price-alerts).", color=constants.colors["gray"])
								embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
								sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))

						currentPlatform = request.get("currentPlatform")
						currentRequest = request.get(currentPlatform)

						response1, response2 = [], []
						if messageRequest.is_registered():
							response1 = await database.collection("details/marketAlerts/{}".format(messageRequest.accountId)).get()
						response2 = await database.collection("details/marketAlerts/{}".format(messageRequest.authorId)).get()
						marketAlerts = [e.to_dict() for e in response1] + [e.to_dict() for e in response2]

						if len(marketAlerts) >= 50:
							embed = discord.Embed(title="You can only create up to 50 price alerts.", color=constants.colors["gray"])
							embed.set_author(name="Maximum number of price alerts reached", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))

						payload, quoteText = await Processor.process_request("candle", messageRequest.authorId, request)

					if payload is None or len(payload.get("candles", [])) == 0:
						errorMessage = "Requested price alert for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if quoteText is None else quoteText
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
						quoteMessage = await message.channel.send(embed=embed)
						sentMessages.append(quoteMessage)
						try: await quoteMessage.add_reaction("☑")
						except: pass
					else:
						currentPlatform = payload.get("platform")
						currentRequest = request.get(currentPlatform)
						ticker = currentRequest.get("ticker")
						exchange = ticker.get("exchange")

						level = currentRequest.get("numericalParameters")[0]
						levelText = "{:,.10f}".format(level).rstrip('0').rstrip('.')

						for platform in request.get("platforms"): request[platform]["ticker"].pop("tree")
						newAlert = {
							"timestamp": time(),
							"channel": str(message.channel.id),
							"service": "Discord",
							"request": request,
							"level": level,
							"levelText": levelText,
							"version": 4
						}
						alertId = str(uuid4())
						hashName = hash(dumps(ticker, option=OPT_SORT_KEYS))

						for alert in marketAlerts:
							currentAlertPlatform = alert["request"].get("currentPlatform")
							currentAlertRequest = alert["request"].get(currentAlertPlatform)
							alertTicker = currentAlertRequest.get("ticker")

							if currentAlertPlatform == currentPlatform and hash(dumps(alertTicker, option=OPT_SORT_KEYS)) == hashName:
								if alert["level"] == newAlert["level"]:
									embed = discord.Embed(title="Price alert for {}{} at {}{} already exists.".format(ticker.get("name"), "" if not bool(exchange) else " ({})".format(exchange.get("name")), levelText, "" if ticker.get("quote") is None else " " + ticker.get("quote")), color=constants.colors["gray"])
									embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
									sentMessages.append(await message.channel.send(embed=embed))
									return (sentMessages, len(sentMessages))
								elif alert["level"] * 0.999 < newAlert["level"] < alert["level"] * 1.001:
									embed = discord.Embed(title="Price alert within 0.1% already exists.", color=constants.colors["gray"])
									embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
									sentMessages.append(await message.channel.send(embed=embed))
									return (sentMessages, len(sentMessages))

						async with message.channel.typing():
							currentLevel = payload["candles"][-1][4]
							currentLevelText = "{:,.10f}".format(currentLevel).rstrip('0').rstrip('.')
							if currentLevel * 0.5 > newAlert["level"] or currentLevel * 2 < newAlert["level"]:
								embed = discord.Embed(title="Your desired alert trigger level at {} {} is too far from the current price of {} {}.".format(levelText, ticker.get("quote"), currentLevelText, ticker.get("quote")), color=constants.colors["gray"])
								embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
								embed.set_footer(text=payload.get("sourceText"))
								sentMessages.append(await message.channel.send(embed=embed))
								return (sentMessages, len(sentMessages))

							newAlert["placement"] = "above" if newAlert["level"] > currentLevel else "below"

							addTriggerMessageOption = {"id": "message", "value": "message"} in currentRequest.get("preferences")
							if addTriggerMessageOption:
								embed = discord.Embed(title="Reply with a trigger message for the price alert.", color=constants.colors["light blue"])
								addTriggerMessage = await message.channel.send(embed=embed)
								self.lockedUsers.add(messageRequest.authorId)

								def confirm_order(m):
									if m.author.id == messageRequest.authorId:
										return True

								try:
									triggerMessage = await client.wait_for('message', timeout=60.0, check=confirm_order)
								except:
									self.lockedUsers.discard(messageRequest.authorId)
									embed = discord.Embed(title="Prompt has been canceled.", description="~~Reply with a trigger message for the price alert.~~", color=constants.colors["gray"])
									try: await addTriggerMessage.edit(embed=embed)
									except: pass
									return (sentMessages, len(sentMessages))
								else:
									self.lockedUsers.discard(messageRequest.authorId)
									newAlert["triggerMessage"] = triggerMessage.content

							embed = discord.Embed(title="Price alert set for {}{} at {}{}.".format(ticker.get("name"), "" if not bool(exchange) else " ({})".format(exchange.get("name")), levelText, "" if ticker.get("quote") is None else " " + ticker.get("quote")), color=constants.colors["deep purple"])
							if currentPlatform in ["IEXC"]: embed.description = "The alert might trigger with up to 15-minute delay due to data licencing requirements on different exchanges."
							embed.set_author(name="Alert successfully set", icon_url=static_storage.icon)
							sentMessages.append(await message.channel.send(embed=embed))

							if not messageRequest.is_registered():
								await database.document("details/marketAlerts/{}/{}".format(messageRequest.authorId, alertId)).set(newAlert)
							elif messageRequest.serverwide_price_alerts_available():
								await database.document("details/marketAlerts/{}/{}".format(messageRequest.accountId, alertId)).set(newAlert)
							elif messageRequest.personal_price_alerts_available():
								await database.document("details/marketAlerts/{}/{}".format(messageRequest.accountId, alertId)).set(newAlert)
								await database.document("accounts/{}".format(messageRequest.accountId)).set({"customer": {"addons": {"marketAlerts": 1}}}, merge=True)

				elif messageRequest.is_pro():
					if not message.author.bot and message.author.permissions_in(message.channel).administrator:
						embed = discord.Embed(title=":bell: Price Alerts are disabled.", description="You can enable Price Alerts feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
						embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
						await message.channel.send(embed=embed)
					else:
						embed = discord.Embed(title=":bell: Price Alerts are disabled.", description="You can enable Price Alerts feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
						embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
						await message.channel.send(embed=embed)

				else:
					embed = discord.Embed(title=":gem: Price Alerts are available to Alpha Pro users or communities for only $2.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
					embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
					await message.channel.send(embed=embed)

			elif method in ["list", "all"] and len(arguments) == 1:
				await message.channel.trigger_typing()

				response1, response2 = [], []
				if messageRequest.is_registered():
					response1 = await database.collection("details/marketAlerts/{}".format(messageRequest.accountId)).get()
				response2 = await database.collection("details/marketAlerts/{}".format(messageRequest.authorId)).get()
				marketAlerts = [(e.id, e.to_dict(), messageRequest.accountId) for e in response1] + [(e.id, e.to_dict(), messageRequest.authorId) for e in response2]
				totalAlertCount = len(marketAlerts)

				for key, alert, matchedId in marketAlerts:
					currentPlatform = alert["request"].get("currentPlatform")
					currentRequest = alert["request"].get(currentPlatform)
					ticker = currentRequest.get("ticker")

					embed = discord.Embed(title="Price alert set for {}{} at {}{}.".format(ticker.get("name"), "" if bool(ticker.get("exchange")) else " ({})".format(ticker.get("exchange").get("name")), alert.get("levelText", alert["level"]), "" if ticker.get("quote") is None else " " + ticker.get("quote")), color=constants.colors["deep purple"])
					embed.set_footer(text="Id: {}/{}".format(matchedId, key))
					alertMessage = await message.channel.send(embed=embed)
					sentMessages.append(alertMessage)
					try: await alertMessage.add_reaction('❌')
					except: pass

				if totalAlertCount == 0:
					embed = discord.Embed(title="You haven't set any alerts yet.", color=constants.colors["gray"])
					embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))

			else:
				embed = discord.Embed(title="Invalid command usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/pro/price-alerts).", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def price(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
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
				autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
				messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
				if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

				payload, quoteText = await Processor.process_request("quote", messageRequest.authorId, request)

				if payload is None or "quotePrice" not in payload:
					errorMessage = "Requested price for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if quoteText is None else quoteText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					quoteMessage = await message.channel.send(embed=embed)
					sentMessages.append(quoteMessage)
					try: await quoteMessage.add_reaction("☑")
					except: pass
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
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def volume(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			async with message.channel.typing():
				outputMessage, request = await Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper())

				if outputMessage is not None:
					if not messageRequest.is_muted() and outputMessage != "":
						embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/volume).", color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))

				currentRequest = request.get(request.get("currentPlatform"))
				autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
				messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
				if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

				payload, quoteText = await Processor.process_request("quote", messageRequest.authorId, request)

				if payload is None or "quoteVolume" not in payload:
					errorMessage = "Requested volume for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if quoteText is None else quoteText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					quoteMessage = await message.channel.send(embed=embed)
					sentMessages.append(quoteMessage)
					try: await quoteMessage.add_reaction("☑")
					except: pass
				else:
					currentRequest = request.get(payload.get("platform"))
					embed = discord.Embed(title=payload["quoteVolume"], description=payload.get("quoteConvertedVolume", discord.embeds.EmptyEmbed), color=constants.colors["orange"])
					embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
					embed.set_footer(text=payload["sourceText"])
					sentMessages.append(await message.channel.send(embed=embed))

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def convert(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			async with message.channel.typing():
				requestSlices = split(" into | in to | in | to ", requestSlice)
				if len(requestSlices) != 2 or len(requestSlices[0].split(" ")) != 2 or len(requestSlices[1].split(" ")) != 1:
					if not messageRequest.is_muted():
						embed = discord.Embed(title="Incorrect currency conversion usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/conversions).", color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))
				arguments1 = requestSlices[0].split(" ")
				arguments2 = requestSlices[1].split(" ")

				payload, quoteText = await Processor.process_conversion(messageRequest, arguments1[1].upper(), arguments2[0].upper(), arguments1[0])

				if payload is None:
					errorMessage = "Requested conversion is not available." if quoteText is None else quoteText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Conversion not available", icon_url=static_storage.icon_bw)
					quoteMessage = await message.channel.send(embed=embed)
					sentMessages.append(quoteMessage)
					try: await quoteMessage.add_reaction("☑")
					except: pass
				else:
					embed = discord.Embed(title="{} ≈ {}".format(payload["quotePrice"], payload["quoteConvertedPrice"]), color=constants.colors[payload["messageColor"]])
					embed.set_author(name="Conversion", icon_url=static_storage.icon)
					sentMessages.append(await message.channel.send(embed=embed))

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Details
	# -------------------------

	async def details(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			async with message.channel.typing():
				outputMessage, request = await Processor.process_detail_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper())

				if outputMessage is not None:
					if not messageRequest.is_muted() and outputMessage != "":
						embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/asset-details).", color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))

				currentRequest = request.get(request.get("currentPlatform"))
				autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
				messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
				if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

				payload, detailText = await Processor.process_request("detail", messageRequest.authorId, request)

				if payload is None:
					errorMessage = "Requested details for `{}` are not available.".format(currentRequest.get("ticker").get("name")) if detailText is None else detailText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					quoteMessage = await message.channel.send(embed=embed)
					sentMessages.append(quoteMessage)
					try: await quoteMessage.add_reaction("☑")
					except: pass
				else:
					currentRequest = request.get(payload.get("platform"))
					ticker = currentRequest.get("ticker")

					embed = discord.Embed(title=payload["name"], description=payload.get("description", discord.embeds.EmptyEmbed), url=payload.get("url", discord.embeds.EmptyEmbed), color=constants.colors["lime"])
					if payload.get("image") is not None:
						embed.set_thumbnail(url=payload["image"])

					assetFundementals = ""
					assetInfo = ""
					assetSupply = ""
					assetScore = ""
					if payload.get("marketcap") is not None:
						assetFundementals += "\nMarket cap: {:,.0f} {}{}".format(payload["marketcap"], "USD", "" if payload.get("rank") is None else " (ranked #{})".format(payload["rank"]))
					if payload.get("volume") is not None:
						assetFundementals += "\nTotal volume: {:,.0f} {}".format(payload["volume"], "USD")
					if payload.get("industry") is not None:
						assetFundementals += "\nIndustry: {}".format(payload["industry"])
					if payload.get("info") is not None:
						if payload["info"].get("location") is not None:
							assetInfo += "\nLocation: {}".format(payload["info"]["location"])
						if payload["info"].get("employees") is not None:
							assetInfo += "\nEmployees: {}".format(payload["info"]["employees"])
					if payload.get("supply") is not None:
						if payload["supply"].get("total") is not None:
							assetSupply += "\nTotal supply: {:,.0f} {}".format(payload["supply"]["total"], ticker.get("base"))
						if payload["supply"].get("circulating") is not None:
							assetSupply += "\nCirculating supply: {:,.0f} {}".format(payload["supply"]["circulating"], ticker.get("base"))
					if payload.get("score") is not None:
						if payload["score"].get("developer") is not None:
							assetScore += "\nDeveloper score: {:,.1f}/100".format(payload["score"]["developer"])
						if payload["score"].get("community") is not None:
							assetScore += "\nCommunity score: {:,.1f}/100".format(payload["score"]["community"])
						if payload["score"].get("liquidity") is not None:
							assetScore += "\nLiquidity score: {:,.1f}/100".format(payload["score"]["liquidity"])
						if payload["score"].get("public interest") is not None:
							assetScore += "\nPublic interest: {:,.3f}".format(payload["score"]["public interest"])
					detailsText = assetFundementals[1:] + assetInfo + assetSupply + assetScore
					if detailsText != "":
						embed.add_field(name="Details", value=detailsText, inline=False)

					assetPriceDetails = ""
					if payload["price"].get("current") is not None:
						assetPriceDetails += ("\nCurrent: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["current"])).format(payload["price"]["current"])
					if payload["price"].get("ath") is not None:
						assetPriceDetails += ("\nAll-time high: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["ath"])).format(payload["price"]["ath"])
					if payload["price"].get("atl") is not None:
						assetPriceDetails += ("\nAll-time low: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["atl"])).format(payload["price"]["atl"])
					if payload["price"].get("1y high") is not None:
						assetPriceDetails += ("\n1-year high: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["1y high"])).format(payload["price"]["1y high"])
					if payload["price"].get("1y low") is not None:
						assetPriceDetails += ("\n1-year low: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["1y low"])).format(payload["price"]["1y low"])
					if payload["price"].get("per") is not None:
						assetPriceDetails += "\nPrice-to-earnings ratio: {:,.2f}".format(payload["price"]["per"])
					if assetPriceDetails != "":
						embed.add_field(name="Price", value=assetPriceDetails[1:], inline=True)

					change24h = "Past day: no data"
					change30d = ""
					change1y = ""
					if payload["change"].get("past day") is not None:
						change24h = "\nPast day: *{:+,.2f} %*".format(payload["change"]["past day"])
					if payload["change"].get("past month") is not None:
						change30d = "\nPast month: *{:+,.2f} %*".format(payload["change"]["past month"])
					if payload["change"].get("past year") is not None:
						change1y = "\nPast year: *{:+,.2f} %*".format(payload["change"]["past year"])
					embed.add_field(name="Price change", value=(change24h + change30d + change1y), inline=True)
					embed.set_footer(text=payload["sourceText"])

					sentMessages.append(await message.channel.send(embed=embed))

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def rankings(self, message, messageRequest, requestSlice):
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
					if not message.author.bot and message.author.permissions_in(message.channel).administrator:
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
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def markets(self, message, messageRequest, requestSlice):
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
				autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
				messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
				if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

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
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Cope consensus voting
	# -------------------------

	async def vote(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			if messageRequest.guildId == -1:
				embed = discord.Embed(title=":dart: You can't hold a vote outside of a community.", color=constants.colors["gray"])
				embed.set_author(name="Alpha", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

			elif all([str(role.id) not in messageRequest.guildProperties["settings"].get("cope", {}).get("holding", []) for role in message.author.roles]):
				embed = discord.Embed(title=":dart: You don't have the permission to hold a vote.", color=constants.colors["gray"])
				embed.set_author(name="Alpha", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

			elif messageRequest.guildId not in COPE_CONSENSUS_VOTE_TESTING:
				embed = discord.Embed(title=":dart: Cope consensus voting is not available to the public just yet. How did you find this prompt anyway ...", color=constants.colors["gray"])
				embed.set_author(name="Alpha", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

			else:
				async with message.channel.typing():
					outputMessage, request = await Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["Ichibot"])

					if outputMessage is not None:
						if not messageRequest.is_muted() and outputMessage != "":
							embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					currentRequest = request.get(request.get("currentPlatform"))
					ticker = currentRequest.get("ticker")
					copePoolAccountId = "TPeMv6ZJvRZ1QLw0ivCRkCzoirU2" if environ["PRODUCTION_MODE"] else "ebOX1w1N2DgMtXVN978fnL0FKCP2"

					if ticker.get("exchange").get("id") != "ftx":
						embed = discord.Embed(title="Cope consensus trading is only available on FTX.", color=constants.colors["gray"])
						sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					origin = "{}_{}_ichibot".format(copePoolAccountId, messageRequest.authorId)

					if origin in self.ichibotSockets:
						socket = self.ichibotSockets.get(origin)
					else:
						socket = Processor.get_direct_ichibot_socket(origin)
						self.ichibotSockets[origin] = socket
						client.loop.create_task(self.process_ichibot_messages(origin, message.author))

					await socket.send_multipart([copePoolAccountId.encode(), b"ftx", b"init"])

					embed = discord.Embed(title="Ichibot connection to FTX is being initiated.", color=constants.colors["deep purple"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					await message.author.send(embed=embed)

				# Vote parameters
				votePeriod = 60.0
				voteMajority = messageRequest.guildProperties["settings"]["cope"].get("majority", 70)
				voteMinimum = messageRequest.guildProperties["settings"]["cope"].get("minimum", 20)
				allowedVoters = messageRequest.guildProperties["settings"]["cope"].get("voting", [])
				logChannelId = messageRequest.guildProperties["settings"]["channels"].get("private")
				logChannel = None if logChannelId is None else client.get_channel(int(logChannelId))

				embed = discord.Embed(title="For how many minutes do you want to hold the vote?", description="Participants will be voting for a directional bet on {}. A consensus will be reached if {:,.1f} % of votes agree and at least {} votes are cast.".format(ticker.get("id"), voteMajority, voteMinimum), color=constants.colors["light blue"])
				embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
				addTriggerMessage = await message.channel.send(embed=embed)
				self.lockedUsers.add(messageRequest.authorId)

				def set_duration(m):
					if m.author.id == messageRequest.authorId:
						if m.clean_content.lower() == "cancel": raise Exception
						try:
							duration = float(m.clean_content.split()[0])
						except:
							pass
						else:
							if duration > 60:
								client.loop.create_task(message.channel.send(embed=discord.Embed(title="Vote can only be held for up to an hour.", color=constants.colors["gray"])))
							elif duration < 1:
								client.loop.create_task(message.channel.send(embed=discord.Embed(title="Vote has to be held for at least a minute.", color=constants.colors["gray"])))
							else:
								return True

				try:
					triggerMessage = await client.wait_for('message', timeout=60.0, check=set_duration)
				except:
					self.lockedUsers.discard(messageRequest.authorId)
					embed = discord.Embed(title="Prompt has been canceled.", description="~~How many minutes until vote concludes?~~", color=constants.colors["gray"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					try: await addTriggerMessage.edit(embed=embed)
					except: pass
					return (sentMessages, len(sentMessages))
				else:
					votePeriod = float(triggerMessage.clean_content.split()[0]) * 60.0
					await triggerMessage.delete()
					await addTriggerMessage.delete()

				# Long command
				longCommand = ""
				embed = discord.Embed(title="Which command do you want to execute, if a vote to open a long wins?", description="Response must start with `x` followed by a valid Ichibot command.", color=constants.colors["light blue"])
				embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
				addTriggerMessage = await message.channel.send(embed=embed)

				def set_command(m):
					if m.author.id == messageRequest.authorId:
						if m.clean_content.lower() == "cancel": raise Exception
						return m.clean_content.startswith("x ")

				try:
					triggerMessage = await client.wait_for('message', timeout=60.0, check=set_command)
				except:
					self.lockedUsers.discard(messageRequest.authorId)
					embed = discord.Embed(title="Prompt has been canceled.", description="~~Which command do you want to execute, if a vote to open a long wins?~~", color=constants.colors["gray"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					try: await addTriggerMessage.edit(embed=embed)
					except: pass
					return (sentMessages, len(sentMessages))
				else:
					longCommand = triggerMessage.clean_content.split(" ", 1)[1]
					await triggerMessage.delete()
					await addTriggerMessage.delete()

				# Short command
				shortCommand = ""
				embed = discord.Embed(title="Which command do you want to execute, if a vote to open a short wins?", description="Response must start with `x` followed by a valid Ichibot command.", color=constants.colors["light blue"])
				embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
				addTriggerMessage = await message.channel.send(embed=embed)

				try:
					triggerMessage = await client.wait_for('message', timeout=60.0, check=set_command)
				except:
					self.lockedUsers.discard(messageRequest.authorId)
					embed = discord.Embed(title="Prompt has been canceled.", description="~~Which command do you want to execute, if a vote to open a short wins?~~", color=constants.colors["gray"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					try: await addTriggerMessage.edit(embed=embed)
					except: pass
					return (sentMessages, len(sentMessages))
				else:
					self.lockedUsers.discard(messageRequest.authorId)
					shortCommand = triggerMessage.clean_content.split(" ", 1)[1]
					await triggerMessage.delete()
					await addTriggerMessage.delete()

				# Change instrument
				await socket.send_multipart([copePoolAccountId.encode(), b"", "instrument {}".format(ticker.get("symbol")).encode()])

				# Voting
				startTimestamp = time()
				allVotes = []
				longVoters = []
				shortVoters = []
				skipVoters = []

				embed = discord.Embed(title="Vote on the next trade for {} ({})".format(ticker.get("id"), ticker.get("exchange").get("name")), description="No votes have been received yet.", color=constants.colors["light blue"])
				embed.add_field(name="Vote concludes in {:,.1f} minutes. Reaction is removed when your vote is received.".format(votePeriod / 60), value="If consensus is reached, `{}` or `{}` will be executed via Ichibot to long or short respectively. ".format(longCommand, shortCommand), inline=False)
				embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
				voteMessage = await message.channel.send(embed=embed)

				async def send_vote_confirmation(_user, _side, _isChange):
					if _isChange:
						if logChannel is not None:
							try: await logChannel.send(content="{} voted to {}".format(_user.mention, _side))
							except: pass
						votesSummaryText = ", ".join(allVotes[-20:])
						if len(allVotes) == 21:
							votesSummaryText = ", ".join(allVotes[-21:-1]) + " and " + allVotes[-1]
						elif len(allVotes) > 21:
							votesSummaryText += " and {} others".format(len(allVotes) - 20)
						votesSummaryText += " voted so far."
						embed.description = votesSummaryText
						try: await voteMessage.edit(embed=embed)
						except: pass
					else:
						if logChannel is not None:
							try: await logChannel.send(content="{} changed their vote to {}".format(_user.mention, _side))
							except: pass

				def count_votes(reaction, user):
					if reaction.message.id == voteMessage.id and not user.bot:
						if hasattr(reaction.emoji, "id") and any([str(role.id) in allowedVoters for role in user.roles]):
							_side = None
							if reaction.emoji.id == 861570114616688681:
								if user.mention not in longVoters:
									_side = "long"
									longVoters.append(user.mention)
								if user.mention in shortVoters: shortVoters.remove(user.mention)
								if user.mention in skipVoters: skipVoters.remove(user.mention)
							elif reaction.emoji.id == 861570190357954590:
								if user.mention in longVoters: longVoters.remove(user.mention)
								if user.mention not in shortVoters:
									_side = "short"
									shortVoters.append(user.mention)
								if user.mention in skipVoters: skipVoters.remove(user.mention)
							elif reaction.emoji.id == 876103292504137799:
								if user.mention in longVoters: longVoters.remove(user.mention)
								if user.mention in shortVoters: shortVoters.remove(user.mention)
								if user.mention not in skipVoters:
									_side = "skip"
									skipVoters.append(user.mention)
							else:
								return False
							if user.mention not in allVotes:
								allVotes.append(user.mention)
								client.loop.create_task(send_vote_confirmation(user, _side, True))
							elif _side is not None:
								client.loop.create_task(send_vote_confirmation(user, _side, False))
						client.loop.create_task(reaction.remove(user))
					return False

				await voteMessage.add_reaction("<:bullish:861570114616688681>")
				await voteMessage.add_reaction("<:bearish:861570190357954590>")
				await voteMessage.add_reaction("<:skip:876103292504137799>")

				async def check_for_cancelation():
					try: await client.wait_for('message', timeout=votePeriod, check=lambda m: m.author.id == messageRequest.authorId and m.clean_content.lower() == "cancel")
					except: return False
					else: return True

				cancelationListenerTask = client.loop.create_task(check_for_cancelation())
				try: await client.wait_for('reaction_add', timeout=votePeriod, check=count_votes)
				except: pass
				shouldCancel = await cancelationListenerTask
				await voteMessage.delete()

				# Handle cancelation
				if shouldCancel:
					embed = discord.Embed(title="Vote has been canceled.", description="No command has been executed via Ichibot.", color=constants.colors["gray"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))

				# Count votes
				totalLong = float(len(longVoters))
				totalShort = float(len(shortVoters))
				totalSkip = float(len(skipVoters))
				totalVotes = int(totalLong + totalShort + totalSkip)

				if totalVotes == 0:
					embed = discord.Embed(title="No consensus has been reached.", description="There were no participants in the vote. No command has been executed via Ichibot.".format(totalVotes, totalLong / totalVotes * 100, totalShort / totalVotes * 100, totalSkip / totalVotes * 100), color=constants.colors["deep purple"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					sentMessages.append(await message.channel.send(embed=embed))
				elif totalVotes >= voteMinimum and totalLong / totalVotes >= voteMajority / 100.0:
					await socket.send_multipart([copePoolAccountId.encode(), b"", longCommand.encode()])
					embed = discord.Embed(title="Consensus has been reached, community voted to go long on {}!".format(ticker.get("id")), description="{:,.1f} % out of {} participants voted to go long. `{}` is being executed via Ichibot.".format(totalLong / totalVotes * 100, totalVotes, longCommand), color=constants.colors["deep purple"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					sentMessages.append(await message.channel.send(embed=embed))
				elif totalVotes >= voteMinimum and totalShort / totalVotes >= voteMajority / 100.0:
					await socket.send_multipart([copePoolAccountId.encode(), b"", shortCommand.encode()])
					embed = discord.Embed(title="Consensus has been reached, community voted to go short on {}!".format(ticker.get("id")), description="{:,.1f} % out of {} participants voted to go short. `{}` is being executed via Ichibot.".format(totalShort / totalVotes * 100, totalVotes, shortCommand), color=constants.colors["deep purple"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					sentMessages.append(await message.channel.send(embed=embed))
				elif totalVotes >= voteMinimum and totalSkip / totalVotes >= voteMajority / 100.0:
					embed = discord.Embed(title="Consensus has been reached, community voted to skip this trade on {}!".format(ticker.get("id")), description="{:,.1f} % out of {} participants voted to skip. No command has been executed via Ichibot.".format(len(totalSkip) / totalVotes * 100, totalVotes), color=constants.colors["deep purple"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="No consensus has been reached.", description="{} participants voted, {:,.1f} % of which to go long, {:,.1f} % to go short and {:,.1f} % to skip. No command has been executed via Ichibot.".format(totalVotes, totalLong / totalVotes * 100, totalShort / totalVotes * 100, totalSkip / totalVotes * 100), color=constants.colors["deep purple"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					sentMessages.append(await message.channel.send(embed=embed))

				try:
					await logChannel.send(content="Voted to long: {}".format(", ".join(longVoters)))
					await logChannel.send(content="Voted to short: {}".format(", ".join(shortVoters)))
					await logChannel.send(content="Votes to skip: {}".format(", ".join(skipVoters)))
				except: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	# -------------------------
	# Trading
	# -------------------------

	async def initiate_ichibot(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")
			method = arguments[0]

			if method in ["ichibot", "ichi", "login"]:
				if messageRequest.is_registered():
					outputMessage, request = await Processor.process_trade_arguments(messageRequest, arguments[1:], platformQueue=["Ichibot"])
					if outputMessage is not None:
						if not messageRequest.is_muted() and outputMessage != "":
							embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/ichibot).", color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=static_storage.ichibot)
							sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					currentRequest = request.get(request.get("currentPlatform"))

					exchange = currentRequest.get("exchange")
					origin = "{}_{}_ichibot".format(messageRequest.accountId, messageRequest.authorId)

					if origin in self.ichibotSockets:
						socket = self.ichibotSockets.get(origin)
					else:
						socket = Processor.get_direct_ichibot_socket(origin)
						self.ichibotSockets[origin] = socket
						client.loop.create_task(self.process_ichibot_messages(origin, message.author))

					await socket.send_multipart([messageRequest.accountId.encode(), exchange.get("id").encode(), b"init"])

					try:
						embed = discord.Embed(title="Ichibot connection to {} is being initiated.".format(exchange.get("name")), color=constants.colors["deep purple"])
						embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
						await message.author.send(embed=embed)

						if not isinstance(message.channel, discord.channel.DMChannel):
							embed = discord.Embed(title="Ichibot connection to {} is being initiated.".format(exchange.get("name")), color=constants.colors["deep purple"])
							embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
							await message.channel.send(embed=embed)

					except discord.errors.Forbidden:
						embed = discord.Embed(title="Ichibot connection to {} is being initiated, however the bot cannot DM you.".format(exchange.get("name")), description="A common reason for this is when the bot is blocked, or when your DMs are disabled. Before you can start trading you must enable open your Direct Messages with Alpha Bot.", color=constants.colors["deep purple"])
						embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
						await message.channel.send(embed=embed)

				else:
					embed = discord.Embed(title=":dart: You must have an Alpha Account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), connect your account with your Discord profile, and add an API key.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					await message.channel.send(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def process_ichibot_command(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			if requestSlice == "login":
				embed = discord.Embed(title=":dart: API key preferences are available in your Alpha Account settings.", description="[Sign into you Alpha Account](https://www.alphabotsystem.com/sign-in) and visit [Ichibot preferences](https://www.alphabotsystem.com/account/ichibot) to update your API keys.", color=constants.colors["deep purple"])
				embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
				await message.channel.send(embed=embed)
			
			elif messageRequest.is_registered():
				origin = "{}_{}_ichibot".format(messageRequest.accountId, messageRequest.authorId)

				if origin in self.ichibotSockets:
					socket = self.ichibotSockets.get(origin)
					await socket.send_multipart([messageRequest.accountId.encode(), b"", messageRequest.raw.split(" ", 1)[1].encode()])
					try: await message.add_reaction("✅")
					except: pass

					if requestSlice in ["q", "quit", "exit", "logout"]:
						self.ichibotSockets.pop(origin)
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
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Paper Trading
	# -------------------------

	async def fetch_paper_leaderboard(self, message, messageRequest, requestSlice):
		sentMessages = []
		return (sentMessages, len(sentMessages))
		try:
			async with message.channel.typing():
				paperTraders = await database.collection("accounts").where("paperTrader.balance", "!=", "").get()
				topBalances = []

				for account in paperTraders:
					properties = account.to_dict()
					balance = properties["paperTrader"]["balance"]
					totalValue = balance.get("USD", 10000)

					for platform, balances in balance.items():
						if platform == "USD": continue
						for asset, holding in balances.items():
							if holding == 0: continue
							payload, quoteText = await Processor.process_conversion(messageRequest, asset, "USD", holding)
							totalValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0

					paperOrders = await database.collection("details/openPaperOrders/{}".format(account.id)).get()
					for element in paperOrders:
						order = element.to_dict()
						if order["orderType"] in ["buy", "sell"]:
							currentPlatform = order["request"].get("currentPlatform")
							paperRequest = order["request"].get(currentPlatform)
							ticker = paperRequest.get("ticker")
							payload, quoteText = await Processor.process_conversion(messageRequest, ticker.get("quote") if order["orderType"] == "buy" else ticker.get("base"), "USD", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1))
							totalValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0

					topBalances.append((totalValue, properties["paperTrader"]["globalLastReset"], properties["oauth"]["discord"]["userId"]))

				topBalances.sort(reverse=True)

				embed = discord.Embed(title="Paper trading leaderboard:", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

				for index, (balance, lastReset, authorId) in enumerate(topBalances[:10]):
					embed.add_field(name="#{}: <@!{}> with {} USD".format(index + 1, authorId, balance), value="Since {}".format(Utils.timestamp_to_date(lastReset)), inline=False)

				sentMessages.append(await message.channel.send(embed=embed))

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def fetch_paper_balance(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			if not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				paperOrders = await database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)).get()
				paperBalances = messageRequest.accountProperties["paperTrader"].get("balance", {})

				embed = discord.Embed(title="Paper balance:", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

				holdingAssets = set()
				totalValue = 0

				for platform, balances in paperBalances.items():
					if platform == "USD": continue
					for asset, holding in balances.items():
						if holding == 0: continue
						ticker, error = await TickerParser.match_ticker(asset, None, platform, "traditional")

						balanceText = ""
						valueText = "No conversion"

						balanceText = "{:,.4f} {}".format(holding, asset)
						payload, quoteText = await Processor.process_conversion(messageRequest, asset, "USD", holding)
						convertedValue = payload["raw"]["quotePrice"][0] if payload is not None else 0
						valueText = "≈ {:,.4f} {}".format(convertedValue, "USD") if payload is not None else "Unavailable"
						totalValue += convertedValue

						embed.add_field(name=balanceText, value=valueText, inline=True)
						holdingAssets.add(platform + "_" +  asset)

				usdBalance = paperBalances.get("USD", 10000)
				balanceText = "{:,.4f} USD".format(usdBalance)
				totalValue += usdBalance
				embed.add_field(name=balanceText, value="Stable in fiat value", inline=True)
				if usdBalance != 0:
					holdingAssets.add("USD")

				lastResetTimestamp = messageRequest.accountProperties["paperTrader"]["globalLastReset"]
				resetCount = messageRequest.accountProperties["paperTrader"]["globalResetCount"]

				openOrdersValue = 0
				for element in paperOrders:
					order = element.to_dict()
					if order["orderType"] in ["buy", "sell"]:
						currentPlatform = order["request"].get("currentPlatform")
						paperRequest = order["request"].get(currentPlatform)
						ticker = paperRequest.get("ticker")
						payload, quoteText = await Processor.process_conversion(messageRequest, ticker.get("quote") if order["orderType"] == "buy" else ticker.get("base"), "USD", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1))
						openOrdersValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0
						holdingAssets.add(currentPlatform + "_" + ticker.get("base"))

				if openOrdersValue > 0:
					totalValue += openOrdersValue
					valueText = "{:,.4f} USD".format(openOrdersValue)
					embed.add_field(name="Locked up in open orders:", value=valueText, inline=True)

				embed.description = "Holding {} asset{} with estimated total value of {:,.2f} USD and {:+,.2f} % ROI.{}".format(len(holdingAssets), "" if len(holdingAssets) == 1 else "s", totalValue, (totalValue / 10000 - 1) * 100, " Trading since {} with {} balance reset{}.".format(Utils.timestamp_to_date(lastResetTimestamp), resetCount, "" if resetCount == 1 else "s") if resetCount != 0 else "")
				sentMessages.append(await message.channel.send(embed=embed))

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def fetch_paper_orders(self, message, messageRequest, requestSlice, mathod):
		sentMessages = []
		try:
			if not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				if mathod == "history":
					paperHistory = await database.collection("details/paperOrderHistory/{}".format(messageRequest.accountId)).limit(50).get()
					if len(paperHistory) == 0:
						embed = discord.Embed(title="No paper trading history.", color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))
					else:
						embed = discord.Embed(title="Paper trading history:", color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

						for element in paperHistory:
							order = element.to_dict()
							currentPlatform = order["request"].get("currentPlatform")
							paperRequest = order["request"].get(currentPlatform)
							ticker = paperRequest.get("ticker")

							side = ""
							if order["orderType"] == "buy": side = "Bought"
							elif order["orderType"] == "sell": side = "Sold"
							elif order["orderType"].startswith("stop"): side = "Stop sold"
							embed.add_field(name="{} {} {} at {} {}".format(side, order["amountText"], ticker.get("base"), order["priceText"], ticker.get("quote")), value="{} ● id: {}".format(Utils.timestamp_to_date(order["timestamp"] / 1000), element.id), inline=False)

						sentMessages.append(await message.channel.send(embed=embed))

				else:
					paperOrders = await database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)).get()
					if len(paperOrders) == 0:
						embed = discord.Embed(title="No open paper orders.", color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))
					else:
						numberOfOrders = len(paperOrders)
						destination = message.channel if numberOfOrders < 10 else message.author
						for i, element in enumerate(paperOrders):
							order = element.to_dict()
							currentPlatform = order["request"].get("currentPlatform")
							paperRequest = order["request"].get(currentPlatform)
							ticker = paperRequest.get("ticker")

							quoteText = ticker.get("quote")
							side = order["orderType"].replace("-", " ")

							embed = discord.Embed(title="Paper {} {} {} at {} {}".format(side, order["amountText"], ticker.get("base"), order["priceText"], quoteText), color=constants.colors["deep purple"])
							embed.set_footer(text="Id: {}/{}".format(messageRequest.accountId, element.id))
							orderMessage = await message.channel.send(embed=embed)
							sentMessages.append(orderMessage)
							await orderMessage.add_reaction('❌')

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def process_paper_trade(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = self.paperTrader.argument_cleanup(requestSlice).split(" ")
			orderType = arguments[0]

			if orderType in ["buy", "sell", "stop-sell"] and 2 <= len(arguments) <= 8:
				if messageRequest.is_registered():
					async with message.channel.typing():
						outputMessage, request = await Processor.process_quote_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), isPaperTrade=True, excluded=["CoinGecko", "Serum", "LLD"])
						if outputMessage is not None:
							if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
								embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/paper-trader).", color=constants.colors["gray"])
								embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
								sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))

						currentRequest = request.get(request.get("currentPlatform"))

						payload, quoteText = await Processor.process_request("candle", messageRequest.authorId, request)

					if payload is None or len(payload.get("candles", [])) == 0:
						errorMessage = "Requested paper {} order for `{}` could not be executed.".format(orderType.replace("-", " "), currentRequest.get("ticker").get("name")) if quoteText is None else quoteText
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
						tradeMessage = await message.channel.send(embed=embed)
						sentMessages.append(tradeMessage)
						try: await tradeMessage.add_reaction("☑")
						except: pass
					else:
						currentPlatform = payload.get("platform")
						currentRequest = request.get(currentPlatform)
						ticker = currentRequest.get("ticker")
						exchange = ticker.get("exchange")

						outputTitle, outputMessage, paper, pendingOrder = await self.paperTrader.process_trade(messageRequest.accountProperties["paperTrader"], orderType, currentPlatform, currentRequest, payload)

						if pendingOrder is None:
							embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
							embed.set_author(name=outputTitle, icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)
							return

						confirmationText = "Do you want to place a paper {} order of {} {} at {}?".format(orderType.replace("-", " "), pendingOrder.amountText, ticker.get("base"), pendingOrder.priceText)
						embed = discord.Embed(title=confirmationText, description=pendingOrder.conversionText, color=constants.colors["pink"])
						embed.set_author(name="Paper order confirmation", icon_url=pendingOrder.parameters.get("thumbnailUrl"))
						orderConfirmationMessage = await message.channel.send(embed=embed)
						self.lockedUsers.add(messageRequest.authorId)

						def confirm_order(m):
							if m.author.id == messageRequest.authorId:
								response = ' '.join(m.clean_content.lower().split())
								if response in ["y", "yes", "sure", "confirm", "execute"]: return True
								elif response in ["n", "no", "cancel", "discard", "reject"]: raise Exception

						try:
							await client.wait_for('message', timeout=60.0, check=confirm_order)
						except:
							self.lockedUsers.discard(messageRequest.authorId)
							embed = discord.Embed(title="Paper order has been canceled.", description="~~{}~~".format(confirmationText), color=constants.colors["gray"])
							embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
							try: await orderConfirmationMessage.edit(embed=embed)
							except: pass
						else:
							self.lockedUsers.discard(messageRequest.authorId)
							async with message.channel.typing():
								for platform in request.get("platforms"): request[platform]["ticker"].pop("tree")
								paper = self.paperTrader.post_trade(paper, orderType, currentPlatform, currentRequest, payload, pendingOrder)

								pendingOrder.parameters["request"] = request
								if paper["globalLastReset"] == 0: paper["globalLastReset"] = int(time())
								await database.document("accounts/{}".format(messageRequest.accountId)).set({"paperTrader": paper}, merge=True)
								if pendingOrder.parameters["parameters"][1]:
									openOrders = await database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)).get()
									if len(openOrders) >= 50:
										embed = discord.Embed(title="You can only create up to 50 pending paper trades.", color=constants.colors["gray"])
										embed.set_author(name="Maximum number of open paper orders reached", icon_url=static_storage.icon_bw)
										sentMessages.append(await message.channel.send(embed=embed))
										return (sentMessages, len(sentMessages))
									await database.document("details/openPaperOrders/{}/{}".format(messageRequest.accountId, str(uuid4()))).set(pendingOrder.parameters)
								else:
									await database.document("details/paperOrderHistory/{}/{}".format(messageRequest.accountId, str(uuid4()))).set(pendingOrder.parameters)

							successMessage = "Paper {} order of {} {} at {} was successfully {}.".format(orderType.replace("-", " "), pendingOrder.amountText, ticker.get("base"), pendingOrder.priceText, "executed" if pendingOrder.parameters["parameters"][0] else "placed")
							embed = discord.Embed(title=successMessage, color=constants.colors["deep purple"])
							embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
							await message.channel.send(embed=embed)

				else:
					embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await message.channel.send(embed=embed)

			else:
				embed = discord.Embed(title="Invalid command usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/paper-trader).", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))

	async def reset_paper_balance(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			if not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)

			elif messageRequest.accountProperties["paperTrader"]["globalLastReset"] == 0 and messageRequest.accountProperties["paperTrader"]["globalResetCount"] == 0:
				embed = discord.Embed(title="You have to start trading before you can reset your paper balance.", color=constants.colors["gray"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

			elif messageRequest.accountProperties["paperTrader"]["globalLastReset"] + 604800 < time():
				embed = discord.Embed(title="Do you really want to reset your paper balance? This cannot be undone.", description="Paper balance can only be reset once every seven days. Your last public reset date will be publicly visible.", color=constants.colors["pink"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				resetBalanceMessage = await message.channel.send(embed=embed)
				self.lockedUsers.add(messageRequest.authorId)

				def confirm_order(m):
					if m.author.id == messageRequest.authorId:
						response = ' '.join(m.clean_content.lower().split())
						if response in ["y", "yes", "sure", "confirm", "execute"]: return True
						elif response in ["n", "no", "cancel", "discard", "reject"]: raise Exception

				try:
					await client.wait_for('message', timeout=60.0, check=confirm_order)
				except:
					self.lockedUsers.discard(messageRequest.authorId)
					embed = discord.Embed(title="Paper balance reset canceled.", description="~~Do you really want to reset your paper balance? This cannot be undone.~~", color=constants.colors["gray"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
					await resetBalanceMessage.edit(embed=embed)
				else:
					self.lockedUsers.discard(messageRequest.authorId)

					async def delete_collection(collectionRef, batchSize):
						docs = await collectionRef.limit(batchSize).get()
						deleted = 0

						for doc in docs:
							await doc.reference.delete()
							deleted += 1

						if deleted >= batchSize:
							return await delete_collection(collectionRef, batchSize)

					async with message.channel.typing():
						await delete_collection(database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)), 300)
						await delete_collection(database.collection("details/paperOrderHistory/{}".format(messageRequest.accountId)), 300)

					paper = {
						"globalResetCount": messageRequest.accountProperties["paperTrader"]["globalResetCount"] + 1,
						"globalLastReset": int(time()),
						"balance": DELETE_FIELD
					}
					await database.document("accounts/{}".format(messageRequest.accountId)).set({"paperTrader": paper}, merge=True)

					embed = discord.Embed(title="Paper balance has been reset successfully.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					sentMessages.append(await message.channel.send(embed=embed))

			else:
				embed = discord.Embed(title="Paper balance can only be reset once every seven days.", color=constants.colors["gray"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Error handling
	# -------------------------

	async def unknown_error(self, message, authorId):
		embed = discord.Embed(title="Looks like something went wrong. The issue has been reported.", color=constants.colors["gray"])
		embed.set_author(name="Something went wrong", icon_url=static_storage.icon_bw)
		try: await message.channel.send(embed=embed)
		except: return

	async def hold_up(self, message, messageRequest):
		embed = discord.Embed(title="Only up to {:d} requests are allowed per command.".format(int(messageRequest.get_limit() / 2)), color=constants.colors["gray"])
		embed.set_author(name="Too many requests", icon_url=static_storage.icon_bw)
		await message.channel.send(embed=embed)


# -------------------------
# Initialization
# -------------------------

def handle_exit(sleepDuration=0):
	print("\n[Shutdown]: closing tasks")
	try: client.loop.run_until_complete(client.close())
	except: pass
	for t in all_tasks(loop=client.loop):
		if t.done():
			try: t.exception()
			except InvalidStateError: pass
			except TimeoutError: pass
			except CancelledError: pass
			continue
		t.cancel()
		try:
			client.loop.run_until_complete(wait_for(t, 5, loop=client.loop))
			t.exception()
		except InvalidStateError: pass
		except TimeoutError: pass
		except CancelledError: pass
	from time import sleep as ssleep
	ssleep(sleepDuration)

if __name__ == "__main__":
	environ["PRODUCTION_MODE"] = environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in environ and environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Alpha Bot is in startup, running in {} mode.".format("production" if environ["PRODUCTION_MODE"] else "development"))

	intents = discord.Intents.all()
	intents.bans = False
	intents.invites = False
	intents.voice_states = False
	intents.typing = False
	intents.presences = False

	client = Alpha(intents=intents, chunk_guilds_at_startup=False, max_messages=10000, status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.playing, name="a reboot, brb!"))
	print("[Startup]: object initialization complete")

	while True:
		client.prepare()
		client.loop.create_task(client.job_queue())
		try:
			token = environ["DISCORD_PRODUCTION_TOKEN" if environ["PRODUCTION_MODE"] else "DISCORD_DEVELOPMENT_TOKEN"]
			client.loop.run_until_complete(client.start(token))
		except (KeyboardInterrupt, SystemExit):
			handle_exit()
			client.loop.close()
			break
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: client.logging.report_exception()
			handle_exit(sleepDuration=15)

		client = Alpha(loop=client.loop, intents=intents, chunk_guilds_at_startup=False, max_messages=10000, status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.playing, name="a reboot, brb!"))
