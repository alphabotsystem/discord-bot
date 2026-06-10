from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError
from aiohttp import ClientSession
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction, File
from discord.commands import SlashCommandGroup, Option
from discord.ui import View, button, Button
from discord.errors import NotFound
from google.cloud.firestore import Increment
from pycoingecko import CoinGeckoAPI

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage
from Processor import process_quote_arguments, get_listings

from commands.base import BaseCommand, ActionsView, autocomplete_fgi_type, autocomplete_movers_categories, MARKET_MOVERS_OPTIONS, files_from_posts


class LookupCommand(BaseCommand):
	lookupGroup = SlashCommandGroup("lookup", "Look up or screen the market for various properties.")

	@lookupGroup.command(name="listings", description="Look up exchange listings for a particular asset.")
	async def listings(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			platforms = request.get_platform_order_for("lookup")
			responseMessage, task = await process_quote_arguments([], platforms, tickerId=tickerId)

			if responseMessage is not None:
				embed = Embed(title=responseMessage, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features"), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.respond(embed=embed)
				except NotFound: pass
				return

			currentPlatform = task.get("currentPlatform")
			currentTask = task.get(currentPlatform)
			ticker = currentTask.get("ticker")
			listings, total = await get_listings(ticker, currentPlatform)

			if total != 0:
				embed = Embed(title=f"{ticker.get('name')} listings", color=constants.colors["deep purple"])
				for quote, exchanges in listings[:25]:
					if len(exchanges) == 0: continue
					embed.add_field(name=f"{quote} markets found on {len(exchanges)} exchanges", value=", ".join(exchanges), inline=False)
				try: await ctx.respond(embed=embed)
				except NotFound: pass
			else:
				embed = Embed(title=f"`{ticker.get('name')}` is not listed on any crypto exchange.", color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=static_storage.error_icon)
				try: await ctx.respond(embed=embed)
				except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"mk": Increment(1)}}, merge=True)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /lookup listings {tickerId}")
			await self.unknown_error(ctx)

	@lookupGroup.command(name="market-movers", description="Look up top gainers or losers in the market.")
	async def top(
		self,
		ctx,
		category: Option(str, "Ranking type.", name="category", autocomplete=autocomplete_movers_categories),
		limit: Option(int, "Asset count limit. Defaults to top 250 by market cap, maximum is 1000.", name="limit", required=False, default=250)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			await ctx.defer()

			category = " ".join(category.lower().split()).replace("etf", "ETF")
			if category not in MARKET_MOVERS_OPTIONS:
				embed = Embed(title="The specified category is invalid.", description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/lookup"), color=constants.colors["deep purple"])
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			parts = category.split(" ")
			direction = parts.pop().lower()
			market = " ".join(parts).lower()
			embed = Embed(title=f"Top {category}", color=constants.colors["deep purple"])

			if market == "crypto":
				rawData = []
				cg = CoinGeckoAPI(api_key=environ["COINGECKO_API_KEY"])
				page = 1
				while True:
					rawData += cg.get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, page=page, price_change_percentage="24h")
					page += 1
					if page > 4: break

				response = []
				for e in rawData[:max(10, limit)]:
					if e.get("price_change_percentage_24h_in_currency", None) is not None:
						response.append({"name": e["name"], "symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})

				if direction == "gainers":
					response = sorted(response, key=lambda k: k["change"], reverse=True)
				elif direction == "losers":
					response = sorted(response, key=lambda k: k["change"])

				for token in response[:9]:
					embed.add_field(name=f"{token['name']} (`{token['symbol'].replace('/', '')}`)", value="{:+,.2f}%".format(token["change"]), inline=True)

				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			else:
				async with ClientSession() as session:
					url = f"https://api.twelvedata.com/market_movers/{market.replace(' ', '_')}?apikey={environ['TWELVEDATA_KEY']}&direction={direction}&outputsize=50"
					async with session.get(url) as resp:
						response = await resp.json()
						assets = filter(
							lambda e: not e['name'].lower().startswith("test") and "testfund" not in e['name'].lower().replace(" ", ""),
							response["values"]
						)
						for asset in list(assets)[:9]:
							embed.add_field(name=f"{asset['name']} (`{asset['symbol'].replace('/', '')}`)", value="{:+,.2f}%".format(asset["percent_change"]), inline=True)

				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"t": Increment(1)}}, merge=True)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /lookup market-movers category:{category} limit:{limit}")
			await self.unknown_error(ctx)

	@lookupGroup.command(name="fgi", description="Look up the current and historic fear & greed index.")
	async def fgi(
		self,
		ctx,
		assetType: Option(str, "Fear & greed market type", name="market", autocomplete=autocomplete_fgi_type, required=False, default=""),
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			# v2's fgi grammar requires a market; default to crypto when unspecified.
			market = "crypto"
			if assetType != "":
				if assetType.lower() in ("crypto", "stocks"):
					market = assetType.lower()
				else:
					embed = Embed(title="Asset type is invalid. Only stocks and crypto markets are supported.", color=constants.colors["gray"])
					embed.set_author(name="Invalid market", icon_url=static_storage.error_icon)
					try: await ctx.respond(embed=embed)
					except NotFound: pass
					return

			await ctx.defer()
			response = await self.render_via_v2("fgi " + market, request)

			files, embeds = [], []
			if not response.get("ok"):
				message = response.get("error") or "Requested chart is not available."
				embed = Embed(title=message, color=constants.colors["gray"])
				embed.set_author(name="Chart not available", icon_url=static_storage.error_icon)
				embeds.append(embed)
			else:
				files = files_from_posts(response.get("posts", []), request.authorId)

			actions = ActionsView(user=ctx.author, command=ctx.command.mention)
			try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=actions)
			except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(1)}}, merge=True)
			await self.log_request_v2("charts", request, response.get("meta", {}))
			await self.cleanup(ctx, request, removeView=True)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /lookup fgi {assetType}")
			await self.unknown_error(ctx)