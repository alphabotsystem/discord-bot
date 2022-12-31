from os import environ
from time import time
from asyncio import sleep
from re import sub
from orjson import dumps
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction, PartialEmoji
from discord.ext.commands import Cog
from discord.ui import View, button, Button
from google.cloud import pubsub_v1

from helpers import constants
from assets import static_storage
from Processor import autocomplete_ticker, autocomplete_venues

publisher = pubsub_v1.PublisherClient()
REQUESTS_TOPIC_NAME = "projects/nlc-bot-36685/topics/discord-requests"
TELEMETRY_TOPIC_NAME = "projects/nlc-bot-36685/topics/discord-telemetry"


async def autocomplete_type(ctx):
	options = ["crypto", "stocks"]
	currentInput = " ".join(ctx.options.get("type", "").lower().split())
	return [e for e in options if e.startswith(currentInput)]


class BaseCommand(Cog):
	commandMap = {
		"chart": "c",
		"price": "p",
		"schedule price": "p",
	}

	sources = {
		"alert set": ["IEXC", "CCXT"],
		"c": ["TradingView", "TradingView Premium", "TradingLite", "Bookmap"],
		"hmap": ["TradingView Stock Heatmap", "TradingView Crypto Heatmap"],
		"flow": ["Alpha Flow"],
		"p": ["IEXC", "CCXT", "CoinGecko"],
		"convert": ["IEXC", "CCXT", "CoinGecko"],
		"volume": ["IEXC", "CoinGecko", "CCXT"],
		"depth": ["IEXC", "CCXT"],
		"info": ["IEXC", "CoinGecko"],
		"lookup markets": ["IEXC", "CCXT", "CoinGecko", "TradingView", "TradingView Premium", "TradingLite", "Bookmap"],
		"paper buy": ["IEXC", "CCXT"],
		"paper sell": ["IEXC", "CCXT"],
		"ichibot": ["Ichibot"]
	}

	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	async def log_request(self, command, request, tasks, telemetry=None):
		timestamp = int(time())
		if command in ["charts", "heatmaps", "prices", "volume", "details", "depth"]:
			for task in tasks:
				currentTask = task.get(task.get("currentPlatform"))
				base = currentTask.get("ticker").get("base")
				if base is None: base = currentTask.get("ticker").get("id")
				publisher.publish(REQUESTS_TOPIC_NAME, dumps({
					"timestamp": timestamp,
					"command": command,
					"user": str(request.authorId),
					"guild": str(request.guildId),
					"channel": str(request.channelId),
					"base": base,
					"platform": task.get("currentPlatform"),
					"count": task.get("requestCount", 1)
				}))
			if telemetry is not None:
				publisher.publish(TELEMETRY_TOPIC_NAME, dumps({
					"timestamp": timestamp,
					"command": command,
					"database": telemetry["database"],
					"prelight": telemetry["prelight"],
					"parser": telemetry["parser"],
					"request": telemetry["request"],
					"response": telemetry["response"],
					"count": task.get("requestCount", 1)
				}))

	async def cleanup(self, ctx, request, removeView=False):
		if request.autodelete is not None:
			try: await ctx.interaction.delete_original_response(delay=request.autodelete * 60)
			except: pass
		if removeView:
			await sleep(600)
			try: await ctx.interaction.edit_original_response(view=None)
			except: pass

	async def unknown_error(self, ctx):
		embed = Embed(title="Looks like something went wrong. The issue has been reported.", color=constants.colors["gray"])
		embed.set_author(name="Something went wrong", icon_url=static_storage.error_icon)
		try: await ctx.interaction.edit_original_response(content=None, embed=embed, files=[])
		except: return

	async def autocomplete_from_ticker(cls, ctx):
		return await cls._autocomplete_ticker(ctx, "from")

	async def autocomplete_to_ticker(cls, ctx):
		return await cls._autocomplete_ticker(ctx, "to")

	async def autocomplete_ticker(cls, ctx):
		return await cls._autocomplete_ticker(ctx, "ticker")

	async def _autocomplete_ticker(cls, ctx, mode):
		command = cls.commandMap.get(ctx.command.qualified_name, ctx.command.qualified_name)
		tickerId = " ".join(ctx.options.get(mode, "").lower().split()).split("|")[0]

		if tickerId == "": return []

		platforms = cls.sources[command]
		tickers = await autocomplete_ticker(tickerId, ",".join(platforms))
		return tickers

	async def autocomplete_venues(cls, ctx):
		command = cls.commandMap.get(ctx.command.qualified_name, ctx.command.qualified_name)
		tickerId = ctx.options.get("ticker", "")
		venue = " ".join(ctx.options.get("venue", "").lower().split())

		if command == "ichibot": tickerId = "btc"
		elif tickerId == "" or tickerId is None: return []
		else: tickerId = " ".join(tickerId.lower().split()).split("|")[0]

		platforms = cls.sources.get(command)
		venues = await autocomplete_venues(tickerId, ",".join(platforms))
		return sorted([v for v in venues if v.lower().startswith(venue)])

class Confirm(View):
	def __init__(self, user=None):
		super().__init__(timeout=None)
		self.user = user
		self.value = None

	@button(label="Confirm", style=ButtonStyle.primary)
	async def confirm(self, button: Button, interaction: Interaction):
		if self.user.id != interaction.user.id: return
		self.value = True
		self.stop()

	@button(label="Cancel", style=ButtonStyle.secondary)
	async def cancel(self, button: Button, interaction: Interaction):
		if self.user.id != interaction.user.id: return
		self.value = False
		self.stop()


class ActionsView(View):
	def __init__(self, user=None):
		super().__init__(timeout=None)
		self.user = user

	@button(emoji=PartialEmoji.from_str("<:remove_response:929342678976565298>"), style=ButtonStyle.gray)
	async def delete(self, button: Button, interaction: Interaction):
		if self.user.id != interaction.user.id:
			if not interaction.permissions.manage_messages: return
			embed = Embed(title="Chart has been removed by a moderator.", description=f"{interaction.user.mention} has removed the chart requested by {self.user.mention}.", color=constants.colors["pink"])
			await interaction.response.send_message(embed=embed)
		try: await interaction.message.delete()
		except: return