from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed
from discord.embeds import EmptyEmbed
from discord.commands import slash_command, Option

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand


class PriceCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		task
	):
		currentTask = task.get(task.get("currentPlatform"))
		payload, quoteText = await Processor.process_task("quote", request.authorId, task)

		if payload is None or "quotePrice" not in payload:
			errorMessage = "Requested price for `{}` is not available.".format(currentTask.get("ticker").get("name")) if quoteText is None else quoteText
			embed = Embed(title=errorMessage, color=constants.colors["gray"])
			embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
		else:
			currentTask = task.get(payload.get("platform"))
			if payload.get("platform") in ["Alternative.me"]:
				embed = Embed(title="{} *({})*".format(payload["quotePrice"], payload["change"]), description=payload.get("quoteConvertedPrice", EmptyEmbed), color=constants.colors[payload["messageColor"]])
				embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
				embed.set_footer(text=payload["sourceText"])
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = Embed(title="{}{}".format(payload["quotePrice"], " *({})*".format(payload["change"]) if "change" in payload else ""), description=payload.get("quoteConvertedPrice", EmptyEmbed), color=constants.colors[payload["messageColor"]])
				embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
				embed.set_footer(text=payload["sourceText"])
				await ctx.interaction.edit_original_message(embed=embed)

		await self.database.document("discord/statistics").set({request.snapshot: {"p": Increment(1)}}, merge=True)

	@slash_command(name="p", description="Fetch stock and crypto prices, forex rates, and other instrument data. Command for power users.")
	async def p(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments")
	):
		try:
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			arguments = arguments.lower().split()
			outputMessage, task = await Processor.process_quote_arguments(request, arguments[1:], tickerId=arguments[0].upper())

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.price(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /p {arguments}")

	@slash_command(name="price", description="Fetch stock, crypto and forex quotes.")
	async def price(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default=""),
		venue: Option(str, "Venue to pull the volume from.", name="venue", autocomplete=BaseCommand.get_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			arguments = " ".join([venue]).lower().split()
			outputMessage, task = await Processor.process_quote_arguments(request, arguments, tickerId=tickerId.upper())

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /v {arguments}")