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

from commands.base import BaseCommand


class HeatmapCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		task,
		send_reply
	):
		currentTask = task.get(task.get("currentPlatform"))
		timeframes = task.pop("timeframes")
		for i in range(task.get("requestCount")):
			for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
			payload, heatmapText = await Processor.process_task("heatmap", request.authorId, task)

			if payload is None:
				errorMessage = "Requested heat map for `{}` is not available.".format(currentTask.get("ticker").get("name")) if heatmapText is None else heatmapText
				embed = Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Heat map not available", icon_url=static_storage.icon_bw)
				await send_reply(embed=embed)
			else:
				await send_reply(content=heatmapText, file=File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

		await self.database.document("discord/statistics").set({request.snapshot: {"hmap": Increment(1)}}, merge=True)
		await self.cleanup(ctx, request)

	@slash_command(name="hmap", description="Pull heatmaps from Bitgur and Finviz. Command for power users.")
	async def hmap(
		self,
		ctx,
		arguments: Option(str, "Request arguments.", name="arguments", required=False, default=""),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			parts = arguments.split(",")
			send_reply = ctx.interaction.edit_original_message if len(parts) == 1 else ctx.followup.send

			for part in parts:
				partArguments = part.lower().split()
				outputMessage, task = await Processor.process_heatmap_arguments(request, partArguments[1:], tickerId=partArguments[0].upper())

				if outputMessage is not None:
					embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/heat-maps).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await send_reply(embed=embed)
					return
				elif autodelete is not None and (autodelete < 1 or autodelete > 10):
					embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
					await send_reply(embed=embed)
					return

				await self.respond(ctx, request, task, send_reply)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /p {} autodelete:{}".format(ctx.author.id, " ".join(arguments), autodelete))
