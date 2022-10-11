from os import environ
from time import time
from random import randint
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, File
from discord.commands import slash_command, Option
from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import process_heatmap_arguments, process_task, autocomplete_timeframe, autocomplete_market, autocomplete_category, autocomplete_color, autocomplete_size, autocomplete_group

from commands.base import BaseCommand, ActionsView

async def autocomplete_type(ctx):
	options = ["crypto", "stocks"]
	currentInput = " ".join(ctx.options.get("type", "").lower().split())
	return [e for e in options if e.startswith(currentInput)]

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
		files, embeds = [], []
		for task in tasks:
			currentTask = task.get(task.get("currentPlatform"))
			timeframes = task.pop("timeframes")
			for i in range(task.get("requestCount")):
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
				payload, responseMessage = await process_task(task, "heatmap")

				if payload is None:
					errorMessage = "Requested heatmap is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Heatmap not available", icon_url=static_storage.icon_bw)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))
		
		actions = None
		if len(files) != 0:
			actions = ActionsView(user=ctx.author)

		await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=actions)

		await self.database.document("discord/statistics").set({request.snapshot: {"hmap": Increment(len(tasks))}}, merge=True)
		await self.cleanup(ctx, request)

	@slash_command(name="hmap", description="Pull market heatmaps from TradingView.")
	async def hmap(
		self,
		ctx,
		assetType: Option(str, "Heatmap asset class.", name="type", autocomplete=autocomplete_type, required=False, default=""),
		timeframe: Option(str, "Timeframe for the heatmap.", name="timeframe", autocomplete=autocomplete_timeframe, required=False, default=""),
		market: Option(str, "Heatmap market.", name="market", autocomplete=autocomplete_market, required=False, default=""),
		category: Option(str, "Specific asset category.", name="category", autocomplete=autocomplete_category, required=False, default=""),
		color: Option(str, "Method used to color the heatmap by.", name="color", autocomplete=autocomplete_color, required=False, default=""),
		size: Option(str, "Method used to determine heatmap's block sizes.", name="size", autocomplete=autocomplete_size, required=False, default=""),
		group: Option(str, "Asset grouping method.", name="group", autocomplete=autocomplete_group, required=False, default=""),
		theme: Option(str, "Heatmap color theme.", name="theme", autocomplete=autocomplete_theme, required=False, default=""),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			platforms = request.get_platform_order_for("hmap", assetType=assetType)

			arguments = [assetType, timeframe, market, category, color, size, group, theme]
			responseMessage, task = await process_heatmap_arguments(arguments, platforms)

			if responseMessage is not None:
				embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/heatmaps).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_response(embed=embed)
				return
			elif autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				await ctx.interaction.edit_original_response(embed=embed)
				return

			await self.respond(ctx, request, [task])

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /hmap assetType:{assetType} timeframe:{timeframe} market:{market} category:{category} color:{color} size:{size} group:{group} theme:{theme} autodelete:{autodelete}")
			await self.unknown_error(ctx)
