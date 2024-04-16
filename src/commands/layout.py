from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError, sleep
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Increment
from google.cloud.firestore_v1.base_query import FieldFilter

from helpers import constants
from assets import static_storage
from Processor import autocomplete_layout_timeframe, process_chart_arguments, process_task

from commands.base import BaseCommand, ActionsView, autocomplete_layouts
from commands.ichibot import Ichibot


class LayoutCommand(BaseCommand):
	@slash_command(name="layout", description="Pull charts from TradingView, TradingLite and more.", guild_only=True)
	async def layout(
		self,
		ctx,
		name: Option(str, "Name of the layout to pull.", name="name", autocomplete=autocomplete_layouts),
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
		timeframe: Option(str, "Preferred chart timeframe to use.", name="timeframe", autocomplete=autocomplete_layout_timeframe, required=False, default=""),
		venue: Option(str, "Venue to pull the chart from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			prelightCheckpoint = time()
			request.set_delay("prelight", prelightCheckpoint - request.start)

			[layout, _] = await gather(
				self.database.collection(f"discord/properties/layouts").where(filter=FieldFilter("label", "==", name)).where(filter=FieldFilter("guildId", "==", str(request.guildId))).get(),
				ctx.defer()
			)

			if len(layout) == 0:
				description = "Detailed guide with examples is available on [our website](https://www.alpha.bot/features/layouts)."
				embed = Embed(title="Layout not found", description=description, color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			layout = layout[0].to_dict()
			theme = layout.get("theme")
			isWide = layout.get("isWide", False)

			arguments = [timeframe, venue] + ([] if theme is None else [theme]) + ([] if not isWide else ["wide"])
			(responseMessage, task) = await process_chart_arguments(arguments, ["TradingView Relay"], tickerId=tickerId.upper(), defaults=request.guildProperties["charting"])

			if responseMessage is not None:
				description = "Detailed guide with examples is available on [our website](https://www.alpha.bot/features/layouts)."
				embed = Embed(title=responseMessage, description=description, color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			request.set_delay("parser", time() - prelightCheckpoint)
			await self.respond(ctx, layout["url"], request, task)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /layout {layout['url']} {tickerId} timeframe:{timeframe} venue:{venue}")
			await self.unknown_error(ctx)

	async def respond(
		self,
		ctx,
		url,
		request,
		task
	):
		if request.tradingview_layouts_available():
			start = time()
			files, embeds = [], []

			task["TradingView Relay"]["url"] = url

			currentTask = task.get(task.get("currentPlatform"))
			timeframes = task.pop("timeframes")
			for i in range(task.get("requestCount")):
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
				payload, responseMessage = await process_task(task, "chart", origin=request.origin)

				if payload is None:
					errorMessage = f"Requested chart for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.error_icon)
					embeds.append(embed)
				else:
					task["currentPlatform"] = payload.get("platform")
					currentTask = task.get(task.get("currentPlatform"))
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

			actions = None
			if len(files) != 0:
				actions = ActionsView(user=ctx.author, command=ctx.command.mention)

			requestCheckpoint = time()
			request.set_delay("request", (requestCheckpoint - start) / (len(files) + len(embeds)))
			try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=actions)
			except NotFound: pass
			request.set_delay("response", time() - requestCheckpoint)

			await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(1)}}, merge=True)
			await self.log_request("layouts", request, [task], telemetry=request.telemetry)
			await self.cleanup(ctx, request, removeView=True)

		else:
			embed = Embed(title=":gem: TradingView Layouts are available for $10.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/tradingview-layouts).", color=constants.colors["deep purple"])
			try: await ctx.interaction.edit_original_response(embed=embed)
			except NotFound: pass

