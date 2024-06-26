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
from Processor import process_heatmap_arguments, process_task, autocomplete_hmap_timeframe, autocomplete_market, autocomplete_category, autocomplete_size, autocomplete_group

from commands.base import BaseCommand, ActionsView, autocomplete_hmap_type


async def autocomplete_theme(ctx):
	options = ["light", "dark"]
	currentInput = " ".join(ctx.options.get("theme", "").lower().split())
	return [e for e in options if e.startswith(currentInput)]


class HeatmapCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		tasks
	):
		start = time()
		files, embeds = [], []
		for task in tasks:
			currentTask = task.get(task.get("currentPlatform"))
			timeframes = task.pop("timeframes")
			for i in range(task.get("requestCount")):
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
				payload, responseMessage = await process_task(task, "heatmap", origin=request.origin)

				if payload is None:
					errorMessage = "Requested heatmap is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Heatmap not available", icon_url=static_storage.error_icon)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

		actions = None
		if len(files) != 0:
			if self.bot.user.id not in DISABLE_DELETE_BUTTON:
				actions = ActionsView(user=ctx.author, command=ctx.command.mention)

		requestCheckpoint = time()
		request.set_delay("request", (requestCheckpoint - start) / (len(files) + len(embeds)))
		try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=actions)
		except NotFound: pass
		request.set_delay("response", time() - requestCheckpoint)

		await self.database.document("discord/statistics").set({request.snapshot: {"hmap": Increment(len(tasks))}}, merge=True)
		await self.log_request("hmap", request, tasks, telemetry=request.telemetry)
		await self.cleanup(ctx, request)

	@slash_command(name="hmap", description="Pull market heatmaps from TradingView.")
	async def hmap(
		self,
		ctx,
		assetType: Option(str, "Heatmap asset class.", name="type", autocomplete=autocomplete_hmap_type, required=False, default=""),
		timeframe: Option(str, "Timeframe and coloring method for the heatmap.", name="color", autocomplete=autocomplete_hmap_timeframe, required=False, default=""),
		market: Option(str, "Heatmap market.", name="market", autocomplete=autocomplete_market, required=False, default=""),
		category: Option(str, "Specific asset category.", name="category", autocomplete=autocomplete_category, required=False, default=""),
		size: Option(str, "Method used to determine heatmap's block sizes.", name="size", autocomplete=autocomplete_size, required=False, default=""),
		group: Option(str, "Asset grouping method.", name="group", autocomplete=autocomplete_group, required=False, default=""),
		theme: Option(str, "Heatmap color theme.", name="theme", autocomplete=autocomplete_theme, required=False, default=""),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			platforms = request.get_platform_order_for("hmap", assetType=assetType)

			prelightCheckpoint = time()
			request.set_delay("prelight", prelightCheckpoint - request.start)

			arguments = [assetType, timeframe, market, category, size, group, theme]
			[(responseMessage, task), _] = await gather(
				process_heatmap_arguments(arguments, platforms),
				ctx.defer()
			)

			if responseMessage is not None:
				embed = Embed(title=responseMessage, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/heatmaps"), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return
			elif autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			request.set_delay("parser", time() - prelightCheckpoint)
			await self.respond(ctx, request, [task])

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /hmap assetType:{assetType} color:{timeframe} market:{market} category:{category} size:{size} group:{group} theme:{theme} autodelete:{autodelete}")
			await self.unknown_error(ctx)


DISABLE_DELETE_BUTTON = []