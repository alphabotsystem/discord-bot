from os import environ
from asyncio import sleep
from re import sub
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction, PartialEmoji
from discord.ext.commands import Cog
from discord.ui import View, button, Button
from influxdb_client import Point
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from helpers import constants
from assets import static_storage
from Processor import autocomplete_ticker, autocomplete_venues


async def autocomplete_type(ctx):
	options = ["crypto", "stocks"]
	currentInput = " ".join(ctx.options.get("type", "").lower().split())
	return [e for e in options if e.startswith(currentInput)]


class BaseCommand(Cog):
	commandMap = {
		"chart": "c",
		"price": "p"
	}

	sources = {
		"alert": ["IEXC", "CCXT"],
		"c": ["TradingView", "TradingView Premium", "TradingLite", "Bookmap"],
		"hmap": ["TradingView Stock Heatmap", "TradingView Crypto Heatmap"],
		"flow": ["Alpha Flow"],
		"p": ["IEXC", "CCXT", "CoinGecko"],
		"convert": ["IEXC", "CCXT", "CoinGecko"],
		"volume": ["IEXC", "CoinGecko", "CCXT"],
		"depth": ["IEXC", "CCXT"],
		"info": ["IEXC", "CoinGecko"],
		"lookup": ["IEXC", "CCXT", "CoinGecko", "TradingView", "TradingView Premium", "TradingLite", "Bookmap"],
		"paper": ["IEXC", "CCXT"],
		"ichibot": ["Ichibot"]
	}

	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	async def log_request(self, command, request, tasks):
		async with InfluxDBClientAsync(url="http://influxdb.default", port=6902, token=environ["INFLUXDB_TOKEN"], org="Alpha Bot System") as client:
			writeApi = client.write_api()

			points = []
			if command in ["charts", "heatmaps", "prices", "volume", "details", "depth"]:
				for task in tasks:
					currentTask = task.get(task.get("currentPlatform"))
					base = currentTask.get("ticker").get("base")
					if base is None: base = currentTask.get("ticker").get("id")
					point = Point("discord").tag("command", command).tag("user", request.authorId).tag("guild", request.guildId).tag("channel", request.channelId).tag("base", base).tag("platform", task.get("currentPlatform")).field("count", task.get("requestCount", 1))
					points.append(point)

			try: await writeApi.write(bucket="requests", record=points)
			except: print(format_exc())

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
		embed.set_author(name="Something went wrong", icon_url=static_storage.icon_bw)
		try: await ctx.interaction.edit_original_response(content=None, embed=embed, files=[])
		except: return

	async def autocomplete_from_ticker(cls, ctx):
		return await cls._autocomplete_ticker(ctx, "from")

	async def autocomplete_to_ticker(cls, ctx):
		return await cls._autocomplete_ticker(ctx, "to")

	async def autocomplete_ticker(cls, ctx):
		return await cls._autocomplete_ticker(ctx, "ticker")

	async def _autocomplete_ticker(cls, ctx, mode):
		_commandName = ctx.command.name if ctx.command.parent is None else ctx.command.parent.name
		command = cls.commandMap.get(_commandName, _commandName)
		tickerId = " ".join(ctx.options.get(mode, "").lower().split()).split("|")[0]

		if tickerId == "": return []

		platforms = cls.sources[command]
		tickers = await autocomplete_ticker(tickerId, ",".join(platforms))
		return tickers

	async def autocomplete_venues(cls, ctx):
		_commandName = ctx.command.name if ctx.command.parent is None else ctx.command.parent.name
		command = cls.commandMap.get(_commandName, _commandName)
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