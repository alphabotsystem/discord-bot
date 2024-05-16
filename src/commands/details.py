from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed
from discord.commands import slash_command, Option
from discord.errors import NotFound

from google.cloud.firestore import Increment

from helpers.utils import get_incorrect_usage_description, add_decimal_zeros
from helpers import constants
from assets import static_storage
from Processor import process_quote_arguments, process_task

from commands.base import BaseCommand


class DetailsCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		task
	):
		currentTask = task.get(task.get("currentPlatform"))
		payload, responseMessage = await process_task(task, "detail", origin=request.origin)

		if payload is None:
			errorMessage = f"Requested details for `{currentTask.get('ticker').get('name')}` are not available." if responseMessage is None else responseMessage
			embed = Embed(title=errorMessage, color=constants.colors["gray"])
			embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
			try: await ctx.respond(embed=embed)
			except NotFound: pass
		else:
			currentTask = task.get(payload.get("platform"))
			ticker = currentTask.get("ticker")

			embed = Embed(title=payload["name"], description=payload.get("description"), url=payload.get("url"), color=constants.colors["lime"])
			if payload.get("image") is not None:
				embed.set_thumbnail(url=payload["image"])

			assetFundamentals = ""
			assetInfo = ""
			assetSupply = ""
			assetScore = ""
			if payload.get("marketcap") is not None:
				assetFundamentals += "\nMarket cap: {:,.0f} {}{}".format(payload["marketcap"], ticker.get("quote"), "" if payload.get("rank") is None else " (ranked #{})".format(payload["rank"]))
			if payload.get("volume") is not None:
				assetFundamentals += "\nTotal volume: {:,.0f} {}".format(payload["volume"], ticker.get("quote"))
			if payload.get("industry") is not None:
				assetFundamentals += f"\nIndustry: {payload['industry']}"
			if payload.get("info") is not None:
				if payload["info"].get("location") is not None:
					assetInfo += f"\nLocation: {payload['info']['location']}"
				if payload["info"].get("employees") is not None:
					assetInfo += f"\nEmployees: {payload['info']['employees']}"
			if payload.get("supply") is not None:
				if payload["supply"].get("total") is not None:
					assetSupply += "\nTotal supply: {:,.0f} {}".format(payload["supply"]["total"], ticker.get("base"))
				if payload["supply"].get("circulating") is not None:
					assetSupply += "\nCirculating supply: {:,.0f} {}".format(payload["supply"]["circulating"], ticker.get("base"))
			detailsText = assetFundamentals[1:] + assetInfo + assetSupply + assetScore
			if detailsText != "":
				embed.add_field(name="Details", value=detailsText, inline=False)

			assetPriceDetails = ""
			if payload["price"].get("current") is not None:
				assetPriceDetails += ("\nCurrent: {:,.%df} {}" % add_decimal_zeros(payload["price"]["current"])).format(payload["price"]["current"], ticker.get("quote"))
			if payload["price"].get("ath") is not None:
				assetPriceDetails += ("\nAll-time high: {:,.%df} {}" % add_decimal_zeros(payload["price"]["ath"])).format(payload["price"]["ath"], ticker.get("quote"))
			if payload["price"].get("atl") is not None:
				assetPriceDetails += ("\nAll-time low: {:,.%df} {}" % add_decimal_zeros(payload["price"]["atl"])).format(payload["price"]["atl"], ticker.get("quote"))
			if payload["price"].get("1y high") is not None:
				assetPriceDetails += ("\n1-year high: {:,.%df} {}" % add_decimal_zeros(payload["price"]["1y high"])).format(payload["price"]["1y high"], ticker.get("quote"))
			if payload["price"].get("1y low") is not None:
				assetPriceDetails += ("\n1-year low: {:,.%df} {}" % add_decimal_zeros(payload["price"]["1y low"])).format(payload["price"]["1y low"], ticker.get("quote"))
			if payload["price"].get("52w high") is not None:
				assetPriceDetails += ("\n52-week high: {:,.%df} {}" % add_decimal_zeros(payload["price"]["52w high"])).format(payload["price"]["52w high"], ticker.get("quote"))
			if payload["price"].get("52w low") is not None:
				assetPriceDetails += ("\n52-week low: {:,.%df} {}" % add_decimal_zeros(payload["price"]["52w low"])).format(payload["price"]["52w low"], ticker.get("quote"))
			if assetPriceDetails != "":
				embed.add_field(name="Price", value=assetPriceDetails[1:], inline=True)

			change24h = "Past day: no data"
			change30d = ""
			change1y = ""
			if payload["change"].get("past day") is not None:
				change24h = "\nPast day: {:+,.2f} %".format(payload["change"]["past day"])
			if payload["change"].get("past month") is not None:
				change30d = "\nPast month: {:+,.2f} %".format(payload["change"]["past month"])
			if payload["change"].get("past year") is not None:
				change1y = "\nPast year: {:+,.2f} %".format(payload["change"]["past year"])
			if payload["change"].get("past 52w") is not None:
				change1y = "\nPast 52 weeks: {:+,.2f} %".format(payload["change"]["past 52w"])
			embed.add_field(name="Price change", value=(change24h + change30d + change1y), inline=True)
			embed.set_footer(text=payload["sourceText"])

			try: await ctx.respond(embed=embed)
			except NotFound: pass

		await self.database.document("discord/statistics").set({request.snapshot: {"info": Increment(1)}}, merge=True)
		await self.log_request("details", request, [task])

	@slash_command(name="info", description="Pull up asset information of stocks and cryptocurrencies.")
	async def info(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			platforms = request.get_platform_order_for("info")
			responseMessage, task = await process_quote_arguments([], platforms, tickerId=tickerId)

			if responseMessage is not None:
				embed = Embed(title=responseMessage, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/asset-details"), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.respond(embed=embed)
				except NotFound: pass
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /info {tickerId}")
			await self.unknown_error(ctx)