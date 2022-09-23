from asyncio import sleep
from re import sub

from discord import Embed, ButtonStyle, Interaction, PartialEmoji
from discord.ext.commands import Cog
from discord.ui import View, button, Button

from helpers import constants
from assets import static_storage
from Processor import get_venues


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
			"crypto": ["CCXT", "CoinGecko"]
		},
		"v": {
			"stocks": ["IEXC"],
			"crypto": ["CoinGecko", "CCXT"]
		},
		"d": {
			"stocks": ["IEXC"],
			"crypto": ["CCXT"]
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

	async def autocomplete_types(cls, ctx):
		_commandName = ctx.command.name if ctx.command.parent is None else ctx.command.parent.name
		command = cls.commandMap.get(_commandName, _commandName)
		assetType = " ".join(ctx.options.get("type", "").lower().split())
		venue = " ".join(ctx.options.get("venue", "").lower().split())

		if venue != "":
			venues = await get_venues("", "")
			# print(venues)
			venueType = [v for v in venues if v.lower().startswith(venue)]
			# print(venueType)
		# print(cls.sources.get(command), assetType)

		return sorted([s for s in cls.sources.get(command) if s.lower().startswith(assetType) and (venue == "" or s in venueType)])

	async def autocomplete_venues(cls, ctx):
		if ctx.options.get("ticker", "") is None: return []

		_commandName = ctx.command.name if ctx.command.parent is None else ctx.command.parent.name
		command = cls.commandMap.get(_commandName, _commandName)
		tickerId = " ".join(ctx.options.get("ticker", "").lower().split())
		assetType = " ".join(ctx.options.get("type", "").lower().split())
		venue = " ".join(ctx.options.get("venue", "").lower().split())

		if command == "ichibot" and assetType == "": assetType = "crypto"
		elif tickerId == "": return []

		types = cls.sources.get(command)
		if assetType not in types:
			platforms = list(set([e for v in types.values() for e in v]))
		else:
			platforms = types.get(assetType, [])

		venues = await get_venues(tickerId, ",".join(platforms))
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