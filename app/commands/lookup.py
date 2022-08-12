from os import environ
from time import time
from uuid import uuid4
from aiohttp import ClientSession
from asyncio import CancelledError, sleep
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

			outputMessage, task = await Processor.process_quote_arguments(request, [], ["CCXT"], tickerId=tickerId.upper())

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			currentTask = task.get(task.get("currentPlatform"))
			ticker = currentTask.get("ticker")
			listings, total = await TickerParser.get_listings(ticker.get("base"), ticker.get("quote"))

			if total != 0:
				embed = Embed(color=constants.colors["deep purple"])
				embed.set_author(name=f"{ticker.get('base')} listings")
				for quote, exchanges in listings:
					if len(exchanges) == 0: continue
					embed.add_field(name=f"{quote} pair found on {len(exchanges)} exchanges", value=", ".join(exchanges), inline=False)
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = Embed(title=f"`{currentTask.get('ticker').get('name')}` is not listed on any crypto exchange.", color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

			await self.database.document("discord/statistics").set({request.snapshot: {"mk": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /lookup markets {tickerId}")
			await self.unknown_error(ctx)

	@lookupGroup.command(name="top", description="Lookup top ganers and losers in the crypto space.")
	async def markets(
		self,
		ctx,
		category: Option(str, "Ranking type.", name="category", autocomplete=get_categories),
		limit: Option(int, "Asset count limit. Defaults to top 250 by market cap, maximum is 1000.", name="limit", required=False, default=250)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if category.lower() in ["gainers", "gain", "gains"]:
				rawData = []
				cg = CoinGeckoAPI()
				page = 1
				while True:
					try:
						rawData += cg.get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, page=page, price_change_percentage="24h")
						page += 1
						if page > 4: break
						await sleep(0.6)
					except: await sleep(5)

				response = []
				for e in rawData[:max(10, limit)]:
					if e.get("price_change_percentage_24h_in_currency", None) is not None:
						response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
				response = sorted(response, key=lambda k: k["change"], reverse=True)[:10]
				
				embed = Embed(title="Top gainers", color=constants.colors["deep purple"])
				for token in response:
					embed.add_field(name=token["symbol"], value="Gained {:,.2f} %".format(token["change"]), inline=True)
				await ctx.interaction.edit_original_message(embed=embed)

			elif category.lower() in ["losers", "loosers", "loss", "losses"]:
				rawData = []
				cg = CoinGeckoAPI()
				page = 1
				while True:
					try:
						rawData += cg.get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, page=page, price_change_percentage="24h")
						page += 1
						if page > 4: break
						await sleep(0.6)
					except: await sleep(5)

				response = []
				for e in rawData[:max(10, limit)]:
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
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /lookup top {category} limit: {limit}")
			await self.unknown_error(ctx)