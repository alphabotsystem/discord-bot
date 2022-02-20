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

from commands.base import BaseCommand, ActionsView


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
					errorMessage = "Requested heat map for `{}` is not available.".format(currentTask.get("ticker").get("name")) if heatmapText is None else heatmapText
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Heat map not available", icon_url=static_storage.icon_bw)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))
		
		actions = None
		if len(files) != 0:
			actions = ActionsView(userId=request.authorId)

		await ctx.interaction.edit_original_message(embeds=embeds, files=files, view=actions)

		await self.database.document("discord/statistics").set({request.snapshot: {"hmap": Increment(len(tasks))}}, merge=True)
		await self.cleanup(ctx, request)

	@slash_command(name="hmap", description="Pull heatmaps from Bitgur and Finviz. Command for power users.")
	async def hmap(
		self,
		ctx,
		arguments: Option(str, "Request arguments.", name="arguments", required=False, default="change"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			parts = arguments.split(",")
			tasks = []

			if len(parts) > 5:
				embed = Embed(title="Only up to 5 requests are allowed per command.", color=constants.colors["gray"])
				embed.set_author(name="Too many requests", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			for part in parts:
				partArguments = part.lower().split()
				if len(partArguments) == 0: continue

				outputMessage, task = await Processor.process_heatmap_arguments(request, partArguments[1:], tickerId=partArguments[0].upper())

				if outputMessage is not None:
					embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/heat-maps).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return
				elif autodelete is not None and (autodelete < 1 or autodelete > 10):
					embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
					await ctx.interaction.edit_original_message(embed=embed)
					return

				tasks.append(task)

			await self.respond(ctx, request, tasks)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /hmap {} autodelete:{}".format(ctx.author.id, " ".join(arguments), autodelete))
			await self.unknown_error(ctx)
