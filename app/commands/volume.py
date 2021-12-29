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


class VolumeCommand(commands.Cog):
	def __init__(self, bot, create_request, database):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	volumeGroup = SlashCommandGroup("volume", "Fetch stock and crypto 24-hour volume.")

	async def volume(
		self,
		ctx,
		request,
		task
	):
		currentRequest = task.get(task.get("currentPlatform"))
		autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
		request.autodelete = request.autodelete or autodeleteOverride

		payload, quoteText = await Processor.process_task("quote", request.authorId, task)

		if payload is None or "quoteVolume" not in payload:
			errorMessage = "Requested volume for `{}` is not available.".format(currentRequest.get("ticker").get("name")) if quoteText is None else quoteText
			embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
			embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
			quoteMessage = await ctx.interaction.edit_original_message(embed=embed)
			try: await quoteMessage.add_reaction("☑")
			except: pass
		else:
			currentRequest = task.get(payload.get("platform"))
			embed = discord.Embed(title=payload["quoteVolume"], description=payload.get("quoteConvertedVolume", discord.embeds.EmptyEmbed), color=constants.colors["orange"])
			embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
			embed.set_footer(text=payload["sourceText"])
			await ctx.interaction.edit_original_message(embed=embed)
		
		await self.database.document("discord/statistics").set({request.snapshot: {"v": Increment(1)}}, merge=True)

	@slash_command(name="v", description="Fetch stock and crypto 24-hour volume. Command for power users.")
	async def v(
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
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/volume).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.volume(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /v {arguments}")

	@volumeGroup.command(name="crypto", description="Fetch 24-hour volume of crypto assets.")
	async def volume_crypto(
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
			outputMessage, task = await Processor.process_quote_arguments(request, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["CoinGecko", "CCXT"])

			if outputMessage is not None:
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/volume).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.volume(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /volume crypto {ticker} {arguments}")

	@volumeGroup.command(name="stocks", description="Fetch 24-hour volume of stocks.")
	async def volume_stocks(
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

			await self.volume(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /volume stocks {ticker} {arguments}")
