from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError
from traceback import format_exc

from discord import Embed, File
from discord.commands import slash_command, Option
from discord.errors import NotFound

from google.cloud.firestore import Increment

from helpers.utils import get_incorrect_usage_description
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
			embed.set_author(name="Chart not available", icon_url=static_storage.error_icon)
			try: await ctx.interaction.edit_original_response(embed=embed)
			except NotFound: pass
		else:
			try: await ctx.interaction.edit_original_response(file=File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))
			except NotFound: pass

		await self.database.document("discord/statistics").set({request.snapshot: {"d": Increment(1)}}, merge=True)
		await self.log_request("depth", request, [task])

	@slash_command(name="depth", description="Pull orderbook visualization snapshots of stocks and cryptocurrencies.")
	async def depth(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
		venue: Option(str, "Venue to pull the orderbook from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			platforms = request.get_platform_order_for("d")
			[(responseMessage, task), _] = await gather(
				process_quote_arguments([venue], platforms, tickerId=tickerId),
				ctx.defer()
			)

			if responseMessage is not None:
				embed = Embed(title=responseMessage, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/orderbook-visualizations"), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /depth {tickerId} venue:{venue}")
			await self.unknown_error(ctx)