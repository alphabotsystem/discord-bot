from os import environ
from asyncio import CancelledError
from traceback import format_exc

import discord
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ext import commands

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor


class PriceCommand(commands.Cog):
	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	priceGroup = SlashCommandGroup("price", "Fetch stock and crypto prices, forex rates, and other instrument data.")

	async def price(
		self,
		ctx,
		request,
		task
	):
		currentRequest = task.get(task.get("currentPlatform"))
		autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
		request.autodelete = request.autodelete or autodeleteOverride

		payload, quoteText = await Processor.process_task("quote", request.authorId, task)

		if payload is None or "quotePrice" not in payload:
			errorMessage = "Requested price for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if quoteText is None else quoteText
			embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
			embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
		else:
			currentRequest = task.get(payload.get("platform"))
			if payload.get("platform") in ["Alternative.me"]:
				embed = discord.Embed(title="{} *({})*".format(payload["quotePrice"], payload["change"]), description=payload.get("quoteConvertedPrice", discord.embeds.EmptyEmbed), color=constants.colors[payload["messageColor"]])
				embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
				embed.set_footer(text=payload["sourceText"])
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = discord.Embed(title="{}{}".format(payload["quotePrice"], " *({})*".format(payload["change"]) if "change" in payload else ""), description=payload.get("quoteConvertedPrice", discord.embeds.EmptyEmbed), color=constants.colors[payload["messageColor"]])
				embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
				embed.set_footer(text=payload["sourceText"])
				await ctx.interaction.edit_original_message(embed=embed)
		
		await self.database.document("discord/statistics").set({request.snapshot: {"p": Increment(1)}}, merge=True)

	@slash_command(name="p", description="Fetch stock, crypto and forex quotes. Command for power users.")
	async def p(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			arguments = arguments.lower().split()
			outputMessage, task = await Processor.process_quote_arguments(request, arguments[1:], tickerId=arguments[0].upper())

			if outputMessage is not None:
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.price(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /p {arguments}")

	@priceGroup.command(name="crypto", description="Fetch crypto prices.")
	async def price_crypto(
		self,
		ctx,
		ticker: Option(str, "Ticker id of a crypto asset.", name="ticker"),
		source: Option(str, "Source name to pull the quote from.", name="source", choices=["CoinGecko", "Exchange"], required=False, default=""),
		exchange: Option(str, "Exchange name to pull the quote from.", name="exchange", required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			arguments = " ".join([ticker, source, exchange]).lower().split()
			outputMessage, task = await Processor.process_quote_arguments(request, arguments[1:], tickerId=arguments[0].upper())

			if outputMessage is not None:
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.price(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /v {arguments}")

	@priceGroup.command(name="stocks", description="Fetch stock prices.")
	async def price_stocks(
		self,
		ctx,
		ticker: Option(str, "Ticker id of a stock.", name="ticker"),
		exchange: Option(str, "Exchange name to pull the quote from.", name="exchange", required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			arguments = " ".join([ticker, exchange]).lower().split()
			outputMessage, task = await Processor.process_quote_arguments(request, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["IEXC"])

			if outputMessage is not None:
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/volume).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.price(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /price stocks {ticker} {arguments}")