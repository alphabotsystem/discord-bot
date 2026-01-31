from os import environ
from time import time
from asyncio import sleep
from re import sub
from orjson import dumps
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction, PartialEmoji
from discord.ext.commands import Cog
from discord.ui import View, button, Button
from google.cloud.firestore import AsyncClient as FirestoreAsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import pubsub_v1

from helpers import constants
from assets import static_storage
from Processor import autocomplete_ticker, autocomplete_venues

database = FirestoreAsyncClient()
publisher = pubsub_v1.PublisherClient()
REQUESTS_TOPIC_NAME = "projects/nlc-bot-36685/topics/discord-requests"
TELEMETRY_TOPIC_NAME = "projects/nlc-bot-36685/topics/discord-telemetry"


MARKET_MOVERS_OPTIONS = []
for m in ["crypto", "stocks", "ETF", "forex", "mutual funds"]:
	MARKET_MOVERS_OPTIONS.extend([f"{m} gainers", f"{m} losers"])
MARKET_MOVERS_OPTIONS.sort()


async def autocomplete_fgi_type(ctx):
	options = ["crypto", "stocks"]
	currentInput = " ".join(ctx.options.get("type", "").lower().split())
	return [e for e in options if e.lower().startswith(currentInput)]

async def autocomplete_hmap_type(ctx):
	options = ["crypto", "stocks", "etf"]
	currentInput = " ".join(ctx.options.get("type", "").lower().split())
	return [e for e in options if e.lower().startswith(currentInput)]

async def autocomplete_movers_categories(ctx):
	currentInput = " ".join(ctx.options.get("category", "").lower().split())
	return [e for e in MARKET_MOVERS_OPTIONS if currentInput in e.lower()]

async def autocomplete_layouts(ctx):
	layouts = await database.collection(f"discord/properties/layouts").where(filter=FieldFilter("guildId", "==", str(ctx.interaction.guild_id))).get()
	layouts = [e.to_dict()["label"] for e in layouts]
	currentInput = " ".join(ctx.options.get("name", "").lower().split())
	return [e for e in layouts if currentInput in e.lower()]


class BaseCommand(Cog):
	commandMap = {
		"chart": "c",
		"price": "p",
		"schedule price": "p",
		"schedule volume": "volume",
		"schedule layout": "layout"
	}

	sources = {
		"alert set": ["Twelvedata", "CCXT"],
		"c": ["TradingView", "TradingView Premium"],
		"layout": ["TradingView Relay"],
		"hmap": ["TradingView Stock Heatmap", "TradingView ETF Heatmap", "TradingView Crypto Heatmap"],
		"flow": ["Alpha Flow"],
		"p": ["Twelvedata", "CCXT", "CoinGecko", "On-Chain"],
		"convert": ["Twelvedata", "CCXT", "CoinGecko", "On-Chain"],
		"volume": ["Twelvedata", "CoinGecko", "CCXT", "On-Chain"],
		"depth": ["CCXT"],
		"info": ["Twelvedata", "CoinGecko"],
		"lookup listings": ["Twelvedata", "CCXT", "CoinGecko", "TradingView", "TradingView Premium"],
		"paper buy": ["Twelvedata", "CCXT"],
		"paper sell": ["Twelvedata", "CCXT"],
		"ichibot": ["Ichibot"]
	}

	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	async def log_request(self, command, request, tasks, telemetry=None):
		if not environ["PRODUCTION"]: return
		timestamp = int(time())
		for task in tasks:
			currentTask = task.get(task.get("currentPlatform"))
			base = currentTask.get("ticker", {}).get("base")
			if command == "layouts": command += " " + task["TradingView Relay"]["url"]
			if base is None: base = currentTask.get("ticker", {}).get("id", "")
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
			await sleep(request.autodelete * 60)
			try: await ctx.interaction.edit_original_response(embeds=[], attachments=[], view=None, content=f"The response has been removed. You can make a new request using {ctx.command.mention}")
			except: pass
		elif removeView:
			await sleep(600)
			try: await ctx.interaction.edit_original_response(view=None)
			except: pass

	async def unknown_error(self, ctx):
		embed = Embed(title="Looks like something went wrong. The issue has been reported.", color=constants.colors["gray"])
		embed.set_author(name="Something went wrong", icon_url=static_storage.error_icon)
		try: await ctx.interaction.edit_original_response(content=None, embed=embed, files=[])
		except: return

	@staticmethod
	async def autocomplete_from_ticker(ctx):
		return await BaseCommand._autocomplete_ticker(ctx, "from")

	@staticmethod
	async def autocomplete_to_ticker(ctx):
		return await BaseCommand._autocomplete_ticker(ctx, "to")

	@staticmethod
	async def autocomplete_ticker(ctx):
		return await BaseCommand._autocomplete_ticker(ctx, "ticker")

	@staticmethod
	async def _autocomplete_ticker(ctx, mode):
		command = BaseCommand.commandMap.get(ctx.command.qualified_name, BaseCommand.commandMap.get(ctx.command.full_parent_name, ctx.command.qualified_name))
		tickerId = " ".join(ctx.options.get(mode, "").lower().split()).split("|")[0].strip()

		if tickerId == "": return []

		platforms = BaseCommand.sources[command]
		tickers = await autocomplete_ticker(tickerId, ",".join(platforms))
		return tickers

	@staticmethod
	async def autocomplete_venues(ctx):
		command = BaseCommand.commandMap.get(ctx.command.qualified_name, BaseCommand.commandMap.get(ctx.command.full_parent_name, ctx.command.qualified_name))
		tickerId = ctx.options.get("ticker", "")
		venue = " ".join(ctx.options.get("venue", "").lower().split())

		if command == "ichibot": tickerId = "btc"
		elif tickerId == "" or tickerId is None: return []
		else: tickerId = " ".join(tickerId.lower().split()).split("|")[0].strip()

		platforms = BaseCommand.sources.get(command)
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
	def __init__(self, user=None, command=None):
		super().__init__(timeout=None)
		self.user = user
		self.command = command

	@button(emoji=PartialEmoji.from_str("<:remove_response:929342678976565298>"), style=ButtonStyle.gray)
	async def delete(self, button: Button, interaction: Interaction):
		if self.user.id != interaction.user.id:
			if not interaction.permissions.manage_messages: return
			embed = Embed(title="Chart has been removed by a moderator.", description=f"{interaction.user.mention} has removed the chart requested by {self.user.mention}.", color=constants.colors["pink"])
			await interaction.response.send_message(embed=embed)

		try:
			if self.command is None:
				await interaction.message.edit(embeds=[], attachments=[], view=None, content="Nothing to see here! The response has been removed.")
			else:
				await interaction.message.edit(embeds=[], attachments=[], view=None, content=f"The response has been removed. You can make a new request using {self.command}")
		except: return

class AuthView(View):
	def __init__(self, redirect="account/success"):
		super().__init__()
		self.add_item(Button(label="Authorize", url=f"https://www.alpha.bot/account/action?mode=authorizeDiscord&continueUrl={redirect}", style=ButtonStyle.link))

class RedirectView(View):
	def __init__(self, url):
		super().__init__()
		self.add_item(Button(label="Open dashboard", url=url, style=ButtonStyle.link))