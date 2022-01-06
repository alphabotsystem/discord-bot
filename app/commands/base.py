from asyncio import sleep
from discord.ext.commands import Cog

from Processor import Processor
from TickerParser import TickerParser


class BaseCommand(Cog):
	commandMap = {
		"volume": "v",
		"price": "p"
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
		"p": {
			"stocks": ["IEXC"],
			"forex": ["IEXC", "CoinGecko"],
			"crypto": ["CoinGecko", "CCXT"]
		},
		"v": {
			"stocks": ["IEXC"],
			"crypto": ["CoinGecko", "CCXT"]
		},
		"info": {
			"stocks": ["IEXC"],
			"crypto": ["CoinGecko"]
		},
		"paper": {
			"stocks": ["IEXC"],
			"crypto": ["CCXT"]
		}
	}

	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	async def cleanup(self, ctx, request):
		if request.autodelete is not None:
			await ctx.interaction.delete_original_message(delay=request.autodelete * 60)

	async def get_types(cls, ctx):
		command = cls.commandMap.get(ctx.command.name, ctx.command.name)
		assetType = " ".join(ctx.options.get("type", "").lower().split())
		venue = " ".join(ctx.options.get("venue", "").lower().split())

		venues = await TickerParser.get_venues("", "")
		venueType = [v for v in venues if v.lower().startswith(venue)]

		return sorted([s for s in cls.sources.get(command) if s.lower().startswith(assetType) and (venue == "" or s in venueType)])

	async def get_venues(cls, ctx):
		command = cls.commandMap.get(ctx.command.name, ctx.command.name)
		tickerId = " ".join(ctx.options.get("ticker", "").lower().split())
		assetType = " ".join(ctx.options.get("type", "").lower().split())
		venue = " ".join(ctx.options.get("venue", "").lower().split())

		if assetType == "" or tickerId == "": return []
		platforms = cls.sources.get(command).get(assetType, [])
		if len(platforms) == 0: return []
		venues = await TickerParser.get_venues(",".join(platforms), tickerId)

		return sorted([v for v in venues if v.lower().startswith(venue)])
