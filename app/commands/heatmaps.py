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
from Processor import Processor
from DataRequest import HeatmapParameters

from commands.base import BaseCommand, ActionsView


def autocomplete_timeframe(ctx):
	timeframe = " ".join(ctx.options.get("timeframe", "").lower().split())
	showStockOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["stocks", ""]
	showCryptoOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["crypto", ""]
	options = []
	for option in HeatmapParameters["timeframes"]:
		if showStockOptions and option.parsed["TradingView Stock Heatmap"] is not None or showCryptoOptions and option.parsed["TradingView Crypto Heatmap"] is not None:
			for ph in option.parsablePhrases:
				if timeframe in ph:
					options.append(option.name)
					break
	return options

def autocomplete_market(ctx):
	market = " ".join(ctx.options.get("market", "").lower().split())
	showStockOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["stocks", ""]
	showCryptoOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["crypto", ""]
	options = []
	for option in HeatmapParameters["types"]:
		if option.id != "type": continue
		if showStockOptions and option.parsed["TradingView Stock Heatmap"] is not None or showCryptoOptions and option.parsed["TradingView Crypto Heatmap"] is not None:
			for ph in option.parsablePhrases:
				if market in ph:
					options.append(option.name)
					break
	return options

def autocomplete_category(ctx):
	category = " ".join(ctx.options.get("category", "").lower().split())
	showStockOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["stocks", ""]
	showCryptoOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["crypto", ""]
	options = []
	for option in HeatmapParameters["preferences"]:
		if option.id != "category": continue
		if showStockOptions and option.parsed["TradingView Stock Heatmap"] is not None or showCryptoOptions and option.parsed["TradingView Crypto Heatmap"] is not None:
			for ph in option.parsablePhrases:
				if category in ph:
					options.append(option.name)
					break
	return options

def autocomplete_color(ctx):
	color = " ".join(ctx.options.get("color", "").lower().split())
	showStockOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["stocks", ""]
	showCryptoOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["crypto", ""]
	options = []
	for option in HeatmapParameters["preferences"]:
		if option.id != "heatmap": continue
		if showStockOptions and option.parsed["TradingView Stock Heatmap"] is not None or showCryptoOptions and option.parsed["TradingView Crypto Heatmap"] is not None:
			for ph in option.parsablePhrases:
				if color in ph:
					options.append(option.name)
					break
	return options

def autocomplete_size(ctx):
	size = " ".join(ctx.options.get("size", "").lower().split())
	showStockOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["stocks", ""]
	showCryptoOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["crypto", ""]
	options = []
	for option in HeatmapParameters["preferences"]:
		if option.id != "size": continue
		if showStockOptions and option.parsed["TradingView Stock Heatmap"] is not None or showCryptoOptions and option.parsed["TradingView Crypto Heatmap"] is not None:
			for ph in option.parsablePhrases:
				if size in ph:
					options.append(option.name)
					break
	return options

def autocomplete_group(ctx):
	group = " ".join(ctx.options.get("group", "").lower().split())
	showStockOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["stocks", ""]
	showCryptoOptions = " ".join(ctx.options.get("type", "").lower().split()) in ["crypto", ""]
	options = []
	for option in HeatmapParameters["preferences"]:
		if option.id != "group": continue
		if showStockOptions and option.parsed["TradingView Stock Heatmap"] is not None or showCryptoOptions and option.parsed["TradingView Crypto Heatmap"] is not None:
			for ph in option.parsablePhrases:
				if group in ph:
					options.append(option.name)
					break
	return options

def autocomplete_theme(ctx):
	return ["light", "dark"]

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
				payload, heatmapText = await Processor.process_task("heatmap", request.authorId, task)

				if payload is None:
					errorMessage = "Requested heat map is not available." if heatmapText is None else heatmapText
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Heat map not available", icon_url=static_storage.icon_bw)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))
		
		actions = None
		if len(files) != 0:
			actions = ActionsView(user=ctx.author)

		await ctx.interaction.edit_original_message(embeds=embeds, files=files, view=actions)

		await self.database.document("discord/statistics").set({request.snapshot: {"hmap": Increment(len(tasks))}}, merge=True)
		await self.cleanup(ctx, request)

	@slash_command(name="hmap", description="Pull heatmaps from TradingView. Command for power users.")
	async def hmap(
		self,
		ctx,
		assetType: Option(str, "Heatmap asset class.", name="type", autocomplete=BaseCommand.get_types, required=False, default=""),
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

			defaultPlatforms = request.get_platform_order_for("hmap", assetType=assetType)
			preferredPlatforms = BaseCommand.sources["hmap"].get(assetType)
			platforms = [e for e in defaultPlatforms if preferredPlatforms is None or e in preferredPlatforms]

			arguments = [assetType, timeframe, market, category, color, size, group, theme]
			outputMessage, task = await Processor.process_heatmap_arguments(request, arguments, platforms)

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/heatmaps).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return
			elif autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, [task])

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /hmap assetType:{assetType} timeframe:{timeframe} market:{market} category:{category} color:{color} size:{size} group:{group} theme:{theme} autodelete:{autodelete}")
			await self.unknown_error(ctx)
