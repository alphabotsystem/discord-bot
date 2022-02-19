from os import environ
from time import time
from uuid import uuid4
from aiohttp import ClientSession
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction
from discord.commands import SlashCommandGroup, Option
from discord.ui import View, button, Button

from google.cloud.firestore import Increment
from pycoingecko import CoinGeckoAPI

from helpers import constants
from assets import static_storage
from helpers.utils import Utils
from Processor import Processor
from TickerParser import TickerParser

from commands.base import BaseCommand, Confirm

async def get_categories(ctx):
	options = ["gainers", "losers"]
	currentInput = " ".join(ctx.options.get("category", "").lower().split())
	return [e for e in options if e.startswith(currentInput)]


class LookupCommand(BaseCommand):
	lookupGroup = SlashCommandGroup("lookup", "Lookup or screen the market for various properties.")

	@lookupGroup.command(name="markets", description="Lookup available markets for a particular crypto asset.")
	async def markets(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			outputMessage, task = await Processor.process_quote_arguments(request, [], tickerId=tickerId.upper(), platformQueue=["CCXT"])

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			currentTask = task.get(task.get("currentPlatform"))
			ticker = currentTask.get("ticker")
			listings, total = await TickerParser.get_listings(ticker.get("base"), ticker.get("quote"))

			if total != 0:
				embed = Embed(color=constants.colors["deep purple"])
				embed.set_author(name="{} listings".format(ticker.get("base")))
				for quote, exchanges in listings:
					embed.add_field(name="{} pair found on {} exchanges".format(quote, len(exchanges)), value="{}".format(", ".join(exchanges)), inline=False)
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = Embed(title="`{}` is not listed on any crypto exchange.".format(currentTask.get("ticker").get("name")), color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

			await self.database.document("discord/statistics").set({request.snapshot: {"mk": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /lookup markets {}".format(ctx.author.id, tickerId))
			self.unknown_error(ctx)

	@lookupGroup.command(name="top", description="Lookup top ganers and losers in the crypto space.")
	async def markets(
		self,
		ctx,
		category: Option(str, "Ranking type.", name="category", autocomplete=get_categories)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if category.lower() in ["gainers", "gain", "gains"]:
				rawData = CoinGeckoAPI().get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, price_change_percentage="24h")
				response = []
				for e in rawData:
					if e.get("price_change_percentage_24h_in_currency", None) is not None:
						response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
				response = sorted(response, key=lambda k: k["change"], reverse=True)[:10]
				
				embed = Embed(title="Top gainers", color=constants.colors["deep purple"])
				for token in response:
					embed.add_field(name=token["symbol"], value="Gained {:,.2f} %".format(token["change"]), inline=True)
				await ctx.interaction.edit_original_message(embed=embed)

			elif category.lower() in ["losers", "loosers", "loss", "losses"]:
				rawData = CoinGeckoAPI().get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, price_change_percentage="24h")
				response = []
				for e in rawData:
					if e.get("price_change_percentage_24h_in_currency", None) is not None:
						response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
				response = sorted(response, key=lambda k: k["change"])[:10]
				
				embed = Embed(title="Top losers", color=constants.colors["deep purple"])
				for token in response:
					embed.add_field(name=token["symbol"], value="Lost {:,.2f} %".format(token["change"]), inline=True)
				await ctx.interaction.edit_original_message(embed=embed)

			await self.database.document("discord/statistics").set({request.snapshot: {"t": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /lookup top {}".format(ctx.author.id, category))
			self.unknown_error(ctx)