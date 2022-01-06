from os import environ
from time import time
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, File
from discord.commands import slash_command, SlashCommandGroup, Option

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand


class ChartCommand(BaseCommand):
	# chartGroup = SlashCommandGroup("chart", "Pull charts from TradingView, TradingLite, GoCharting, and more.")

	async def respond(
		self,
		ctx,
		request,
		task
	):
		currentTask = task.get(task.get("currentPlatform"))
		timeframes = task.pop("timeframes")
		for i in range(task.get("requestCount")):
			for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
			payload, chartText = await Processor.process_task("chart", request.authorId, task)

			if payload is None:
				errorMessage = "Requested chart for `{}` is not available.".format(currentTask.get("ticker").get("name")) if chartText is None else chartText
				embed = Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				await ctx.interaction.edit_original_message(content=chartText, file=File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

		await self.cleanup(ctx, request)

	@slash_command(name="c", description="Pull charts from TradingView, TradingLite, GoCharting, and more. Command for power users.")
	async def c(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			arguments = arguments.lower().split()
			outputMessage, task = await Processor.process_chart_arguments(request, arguments[1:], tickerId=arguments[0].upper())

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/charting).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return
			elif autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /p {arguments} autodelete:{autodelete}")
