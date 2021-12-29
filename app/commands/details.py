from os import environ
from asyncio import CancelledError
from traceback import format_exc

import discord
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ext import commands

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from helpers.utils import Utils
from Processor import Processor


class DetailsCommand(commands.Cog):
	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	infoGroup = SlashCommandGroup("info", "Pull up asset information of cryptocurrencies and stocks.")

	async def info(
		self,
		ctx,
		request,
		task
	):
		currentRequest = task.get(task.get("currentPlatform"))
		autodeleteOverride = {"id": "autoDeleteOverride", "value": "autodelete"} in currentRequest.get("preferences")
		request.autodelete = request.autodelete or autodeleteOverride
		if {"id": "hideRequest", "value": "hide"} in currentRequest.get("preferences"): await message.delete()

		payload, detailText = await Processor.process_task("detail", request.authorId, task)

		if payload is None:
			errorMessage = "Requested details for `{}` are not available.".format(currentRequest.get("ticker").get("name")) if detailText is None else detailText
			embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
			embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
		else:
			currentRequest = task.get(payload.get("platform"))
			ticker = currentRequest.get("ticker")

			embed = discord.Embed(title=payload["name"], description=payload.get("description", discord.embeds.EmptyEmbed), url=payload.get("url", discord.embeds.EmptyEmbed), color=constants.colors["lime"])
			if payload.get("image") is not None:
				embed.set_thumbnail(url=payload["image"])

			assetFundementals = ""
			assetInfo = ""
			assetSupply = ""
			assetScore = ""
			if payload.get("marketcap") is not None:
				assetFundementals += "\nMarket cap: {:,.0f} {}{}".format(payload["marketcap"], "USD", "" if payload.get("rank") is None else " (ranked #{})".format(payload["rank"]))
			if payload.get("volume") is not None:
				assetFundementals += "\nTotal volume: {:,.0f} {}".format(payload["volume"], "USD")
			if payload.get("industry") is not None:
				assetFundementals += "\nIndustry: {}".format(payload["industry"])
			if payload.get("info") is not None:
				if payload["info"].get("location") is not None:
					assetInfo += "\nLocation: {}".format(payload["info"]["location"])
				if payload["info"].get("employees") is not None:
					assetInfo += "\nEmployees: {}".format(payload["info"]["employees"])
			if payload.get("supply") is not None:
				if payload["supply"].get("total") is not None:
					assetSupply += "\nTotal supply: {:,.0f} {}".format(payload["supply"]["total"], ticker.get("base"))
				if payload["supply"].get("circulating") is not None:
					assetSupply += "\nCirculating supply: {:,.0f} {}".format(payload["supply"]["circulating"], ticker.get("base"))
			if payload.get("score") is not None:
				if payload["score"].get("developer") is not None:
					assetScore += "\nDeveloper score: {:,.1f}/100".format(payload["score"]["developer"])
				if payload["score"].get("community") is not None:
					assetScore += "\nCommunity score: {:,.1f}/100".format(payload["score"]["community"])
				if payload["score"].get("liquidity") is not None:
					assetScore += "\nLiquidity score: {:,.1f}/100".format(payload["score"]["liquidity"])
				if payload["score"].get("public interest") is not None:
					assetScore += "\nPublic interest: {:,.3f}".format(payload["score"]["public interest"])
			detailsText = assetFundementals[1:] + assetInfo + assetSupply + assetScore
			if detailsText != "":
				embed.add_field(name="Details", value=detailsText, inline=False)

			assetPriceDetails = ""
			if payload["price"].get("current") is not None:
				assetPriceDetails += ("\nCurrent: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["current"])).format(payload["price"]["current"])
			if payload["price"].get("ath") is not None:
				assetPriceDetails += ("\nAll-time high: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["ath"])).format(payload["price"]["ath"])
			if payload["price"].get("atl") is not None:
				assetPriceDetails += ("\nAll-time low: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["atl"])).format(payload["price"]["atl"])
			if payload["price"].get("1y high") is not None:
				assetPriceDetails += ("\n1-year high: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["1y high"])).format(payload["price"]["1y high"])
			if payload["price"].get("1y low") is not None:
				assetPriceDetails += ("\n1-year low: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["1y low"])).format(payload["price"]["1y low"])
			if payload["price"].get("per") is not None:
				assetPriceDetails += "\nPrice-to-earnings ratio: {:,.2f}".format(payload["price"]["per"])
			if assetPriceDetails != "":
				embed.add_field(name="Price", value=assetPriceDetails[1:], inline=True)

			change24h = "Past day: no data"
			change30d = ""
			change1y = ""
			if payload["change"].get("past day") is not None:
				change24h = "\nPast day: *{:+,.2f} %*".format(payload["change"]["past day"])
			if payload["change"].get("past month") is not None:
				change30d = "\nPast month: *{:+,.2f} %*".format(payload["change"]["past month"])
			if payload["change"].get("past year") is not None:
				change1y = "\nPast year: *{:+,.2f} %*".format(payload["change"]["past year"])
			embed.add_field(name="Price change", value=(change24h + change30d + change1y), inline=True)
			embed.set_footer(text=payload["sourceText"])

			await ctx.interaction.edit_original_message(embed=embed)
		
		await self.database.document("discord/statistics").set({request.snapshot: {"info": Increment(1)}}, merge=True)

	@slash_command(name="i", description="Pull up asset information of cryptocurrencies and stocks. Command for power users.")
	async def i(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			arguments = arguments.lower().split()
			outputMessage, task = await Processor.process_detail_arguments(request, arguments[1:], tickerId=arguments[0].upper())

			if outputMessage is not None:
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/asset-details).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.info(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /v {arguments}")

	@infoGroup.command(name="crypto", description="Pull up asset information of cryptocurrencies.")
	async def info_crypto(
		self,
		ctx,
		ticker: Option(str, "Ticker id of a crypto asset.", name="ticker")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			arguments = " ".join([ticker]).lower().split()
			outputMessage, task = await Processor.process_detail_arguments(request, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["CoinGecko"])

			if outputMessage is not None:
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/asset-details).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.info(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /volume crypto {ticker} {arguments}")

	@infoGroup.command(name="stocks", description="Pull up asset information of stocks.")
	async def info_stocks(
		self,
		ctx,
		ticker: Option(str, "Ticker id of a stock.", name="ticker"),
		exchange: Option(str, "Exchange name to pull the quote from.", name="exchange", required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			arguments = " ".join([ticker, exchange]).lower().split()
			outputMessage, task = await Processor.process_detail_arguments(request, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["IEXC"])

			if outputMessage is not None:
				embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/asset-details).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.info(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /volume stocks {ticker} {arguments}")
