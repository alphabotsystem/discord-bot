from asyncio import sleep
from re import sub

from discord import Embed, ButtonStyle, Interaction, PartialEmoji
from discord.ext.commands import Cog
from discord.ui import View, button, Button

from helpers import constants
from assets import static_storage
from Processor import Processor
from TickerParser import TickerParser

from DataRequest import ChartParameters


class BaseCommand(Cog):
	commandMap = {
		"chart": "c",
		"price": "p",
		"volume": "v",
		"depth": "d"
	}

	sources = {
		"alert": {
			"stocks": ["IEXC"],
			"crypto": ["CCXT"]
		},
		"c": {
			"stocks": ["TradingView", "GoCharting", "Finviz"],
			"forex": ["TradingView", "Finviz"],
			"other": ["TradingView", "Finviz"],
			"crypto": ["TradingView", "TradingLite", "GoCharting", "Bookmap"]
		},
		"hmap": {
			"stocks": ["TradingView Stock Heatmap"],
			"crypto": ["TradingView Crypto Heatmap"]
		},
		"flow": {
			"stocks": ["Alpha Flow"]
		},
		"p": {
			"stocks": ["IEXC"],
			"forex": ["IEXC", "CoinGecko"],
			"crypto": ["CCXT", "CoinGecko", "Serum"]
		},
		"v": {
			"stocks": ["IEXC"],
			"crypto": ["CoinGecko", "CCXT"]
		},
		"d": {
			"stocks": ["IEXC"],
			"crypto": ["CCXT", "Serum"]
		},
		"info": {
			"stocks": ["IEXC"],
			"crypto": ["CoinGecko"]
		},
		"paper": {
			"stocks": ["IEXC"],
			"crypto": ["CCXT"]
		},
		"ichibot": {
			"crypto": ["Ichibot"]
		}
	}

	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	async def cleanup(self, ctx, request, removeView=False):
		if request.autodelete is not None:
			await ctx.interaction.delete_original_message(delay=request.autodelete * 60)
		if removeView:
			await sleep(600)
			try: await ctx.interaction.edit_original_message(view=None)
			except: pass

	async def unknown_error(self, ctx):
		embed = Embed(title="Looks like something went wrong. The issue has been reported.", color=constants.colors["gray"])
		embed.set_author(name="Something went wrong", icon_url=static_storage.icon_bw)
		try: await ctx.interaction.edit_original_message(content=None, embed=embed, files=[])
		except: return

	async def get_types(cls, ctx):
		_commandName = ctx.command.name if ctx.command.parent is None else ctx.command.parent.name
		command = cls.commandMap.get(_commandName, _commandName)
		assetType = " ".join(ctx.options.get("type", "").lower().split())
		venue = " ".join(ctx.options.get("venue", "").lower().split())

		if venue != "":
			venues = await TickerParser.get_venues("")
			venueType = [v for v in venues if v.lower().startswith(venue)]

		return sorted([s for s in cls.sources.get(command) if s.lower().startswith(assetType) and (venue == "" or s in venueType)])

	async def get_venues(cls, ctx):
		if ctx.options.get("ticker", "") is None: return []

		_commandName = ctx.command.name if ctx.command.parent is None else ctx.command.parent.name
		command = cls.commandMap.get(_commandName, _commandName)
		tickerId = " ".join(ctx.options.get("ticker", "").lower().split())
		assetType = " ".join(ctx.options.get("type", "").lower().split())
		venue = " ".join(ctx.options.get("venue", "").lower().split())

		if assetType == "" and command == "ichibot": assetType = "crypto"
		elif tickerId == "": return []

		types = cls.sources.get(command)
		if assetType not in types:
			platforms = list(set([e for v in types.values() for e in v]))
		else:
			platforms = types.get(assetType, [])

		venues = await TickerParser.get_venues(",".join(platforms))
		return sorted([v for v in venues if v.lower().startswith(venue)])

	async def get_platforms(cls, ctx):
		if ctx.options.get("ticker", "") is None: return []
		_commandName = ctx.command.name if ctx.command.parent is None else ctx.command.parent.name
		command = cls.commandMap.get(_commandName, _commandName)
		assetType = " ".join(ctx.options.get("type", "").lower().split())
		platform = " ".join(ctx.options.get("platform", "").lower().split())

		platforms = set()
		for t, p in BaseCommand.sources.get(command).items():
			if assetType != "" and assetType != t: continue
			if platform == "": platforms.update(p)
			else: platforms.update([e for e in p if e.lower().startswith(platform)])

		return sorted(list(platforms))

	async def get_timeframes(cls, ctx):
		platform = ctx.options.get("platform", "")

		timeframes = []
		for t in ChartParameters["timeframes"]:
			if platform == "" or t.supports(platform):
				timeframes.append(t.name)

		return timeframes

	async def get_indicators(cls, ctx):
		platform = ctx.options.get("platform", "")
		indicators = sub(" +", " ", ctx.options.get("indicators", "").replace(",", ", ").strip())

		existing = indicators.split(", ")
		added = ", ".join(existing[:-1])

		indicatorList = []
		for t in ChartParameters["indicators"]:
			if (platform == "" or t.supports(platform)) and t.name.lower() not in indicators.lower() and all([p not in indicators.lower() for p in t.parsablePhrases]):
				if indicators.endswith(","):
					newSuggestion = indicators + " " + t.name + ", "
					if len(newSuggestion) <= 100:
						indicatorList.append(newSuggestion)
				elif t.name.lower().startswith(existing[-1].lower()):
					newSuggestion = added + " " + t.name + ", "
					if len(newSuggestion) <= 100:
						indicatorList.append(newSuggestion)

		return indicatorList

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