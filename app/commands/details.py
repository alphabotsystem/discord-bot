from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed
from discord.embeds import EmptyEmbed
from discord.commands import slash_command, Option

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from helpers.utils import add_decimal_zeros
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
		payload, responseMessage = await process_task(task, "detail")

		if payload is None:
			errorMessage = f"Requested details for `{currentTask.get('ticker').get('name')}` are not available." if responseMessage is None else responseMessage
			embed = Embed(title=errorMessage, color=constants.colors["gray"])
			embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
		else:
			currentTask = task.get(payload.get("platform"))
			ticker = currentTask.get("ticker")

			embed = Embed(title=payload["name"], description=payload.get("description", EmptyEmbed), url=payload.get("url", EmptyEmbed), color=constants.colors["lime"])
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
				assetFundementals += f"\nIndustry: {payload['industry']}"
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
				assetPriceDetails += ("\nCurrent: ${:,.%df}" % add_decimal_zeros(payload["price"]["current"])).format(payload["price"]["current"])
			if payload["price"].get("ath") is not None:
				assetPriceDetails += ("\nAll-time high: ${:,.%df}" % add_decimal_zeros(payload["price"]["ath"])).format(payload["price"]["ath"])
			if payload["price"].get("atl") is not None:
				assetPriceDetails += ("\nAll-time low: ${:,.%df}" % add_decimal_zeros(payload["price"]["atl"])).format(payload["price"]["atl"])
			if payload["price"].get("1y high") is not None:
				assetPriceDetails += ("\n1-year high: ${:,.%df}" % add_decimal_zeros(payload["price"]["1y high"])).format(payload["price"]["1y high"])
			if payload["price"].get("1y low") is not None:
				assetPriceDetails += ("\n1-year low: ${:,.%df}" % add_decimal_zeros(payload["price"]["1y low"])).format(payload["price"]["1y low"])
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
			responseMessage, task = await process_quote_arguments([], platforms, tickerId=tickerId.upper())

			if responseMessage is not None:
				embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/asset-details).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /info {tickerId} venue:{venue}")
			await self.unknown_error(ctx)