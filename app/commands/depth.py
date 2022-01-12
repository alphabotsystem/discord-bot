from os import environ
from time import time
from random import randint
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, File
from discord.embeds import EmptyEmbed
from discord.commands import slash_command, Option

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand


class DepthCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		task
	):
		currentTask = task.get(task.get("currentPlatform"))
		payload, chartText = await Processor.process_task("depth", request.authorId, task)

		if payload is None:
			embed = Embed(title="Requested orderbook visualization for `{}` is not available.".format(currentTask.get("ticker").get("name")), color=constants.colors["gray"])
			embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
		else:
			await ctx.interaction.edit_original_message(content=chartText, file=File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

		await self.database.document("discord/statistics").set({request.snapshot: {"d": Increment(1)}}, merge=True)

	@slash_command(name="d", description="Pull orderbook visualization snapshots of stocks and cryptocurrencies. Command for power users.")
	async def d(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			arguments = arguments.lower().split()
			outputMessage, task = await Processor.process_quote_arguments(request, arguments[1:], tickerId=arguments[0].upper(), excluded=["CoinGecko", "LLD"])

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/orderbook-visualizations).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /d {}".format(ctx.author.id, " ".join(arguments)))

	@slash_command(name="depth", description="Pull orderbook visualization snapshots of stocks and cryptocurrencies.")
	async def depth(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default=""),
		venue: Option(str, "Venue to pull the orderbook from.", name="venue", autocomplete=BaseCommand.get_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			arguments = [venue]
			outputMessage, task = await Processor.process_quote_arguments(request, arguments, tickerId=tickerId.upper())

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/orderbook-visualizations).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /depth {} type:{} venue:{}".format(ctx.author.id, tickerId, assetType, venue))