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
from Processor import process_quote_arguments, process_task

from commands.base import BaseCommand


class DepthCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		task
	):
		currentTask = task.get(task.get("currentPlatform"))
		payload, responseMessage = await process_task(task, "depth")

		if payload is None:
			errorMessage = f"Requested orderbook visualization for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
			embed = Embed(title=errorMessage, color=constants.colors["gray"])
			embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
		else:
			await ctx.interaction.edit_original_message(file=File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

		await self.database.document("discord/statistics").set({request.snapshot: {"d": Increment(1)}}, merge=True)

	@slash_command(name="depth", description="Pull orderbook visualization snapshots of stocks and cryptocurrencies.")
	async def depth(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.autocomplete_types, required=False, default=""),
		venue: Option(str, "Venue to pull the orderbook from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			defaultPlatforms = request.get_platform_order_for("d", assetType=assetType)
			preferredPlatforms = BaseCommand.sources["d"].get(assetType)
			platforms = [e for e in defaultPlatforms if preferredPlatforms is None or e in preferredPlatforms]

			arguments = [venue]
			responseMessage, task = await process_quote_arguments(request, arguments, platforms, tickerId=tickerId.upper())

			if responseMessage is not None:
				embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/orderbook-visualizations).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /depth {tickerId} type:{assetType} venue:{venue}")
			await self.unknown_error(ctx)