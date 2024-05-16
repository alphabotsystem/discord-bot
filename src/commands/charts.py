from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError, sleep
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Increment

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage
from Processor import process_chart_arguments, process_task
from DatabaseConnector import DatabaseConnector

from commands.base import BaseCommand, ActionsView


class ChartCommand(BaseCommand):
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
				payload, responseMessage = await process_task(task, "chart", origin=request.origin)

				if responseMessage == "requires pro":
					embed = Embed(title=f"The requested chart for `{currentTask.get('ticker').get('name')}` is only available on TradingView Premium.", description="All TradingView Premium charts are bundled with the [Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting).", color=constants.colors["gray"])
					embed.set_author(name="TradingView Premium", icon_url=static_storage.error_icon)
					embeds.append(embed)
				elif payload is None:
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
			ticker = currentTask.get("ticker", {})
			if len(tasks) == 1 and (self.bot.user.id in constants.REFERRALS or not request.is_paid_user()):
				exchangeId = ticker.get("exchange", {}).get("id")
				referrals = constants.REFERRALS.get(self.bot.user.id, constants.REFERRALS["default"])
				if exchangeId in referrals:
					actions = ReferralView(*referrals[exchangeId], user=ctx.author, command=ctx.command.mention)
			else:
				actions = ActionsView(user=ctx.author, command=ctx.command.mention)

		requestCheckpoint = time()
		request.set_delay("request", (requestCheckpoint - start) / (len(files) + len(embeds)))
		try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=actions)
		except NotFound: pass
		request.set_delay("response", time() - requestCheckpoint)

		await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(len(tasks))}}, merge=True)
		await self.log_request("charts", request, tasks, telemetry=request.telemetry)
		await self.cleanup(ctx, request, removeView=True)

	@slash_command(name="c", description="Pull charts from TradingView, TradingLite and more.")
	async def c(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			platforms = request.get_platform_order_for("c")
			parts = arguments.split(",")

			if len(parts) > 5:
				embed = Embed(title="Only up to five requests are allowed per command.", color=constants.colors["gray"])
				embed.set_author(name="Too many requests", icon_url=static_storage.error_icon)
				try: await ctx.respond(embed=embed)
				except NotFound: pass
				return

			prelightCheckpoint = time()
			request.set_delay("prelight", prelightCheckpoint - request.start)

			tasks = []
			for part in parts:
				partArguments = part.lower().split()
				if len(partArguments) == 0: continue
				tasks.append(process_chart_arguments(partArguments[1:], platforms, tickerId=partArguments[0], defaults=request.guildProperties["charting"]))
			[results, _] = await gather(
				gather(*tasks),
				ctx.defer()
			)

			tasks = []
			for (responseMessage, task) in results:
				if responseMessage is not None:
					description = "[Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting) unlocks additional assets, indicators, timeframes and more." if responseMessage.endswith("add-on.") else get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/charting")
					embed = Embed(title=responseMessage, description=description, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif autodelete is not None and (autodelete < 1 or autodelete > 10):
					embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				tasks.append(task)

			request.set_delay("parser", time() - prelightCheckpoint)
			await self.respond(ctx, request, tasks)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /c {arguments} autodelete:{autodelete}")
			await self.unknown_error(ctx)

class ReferralView(ActionsView):
	def __init__(self, label, url, user=None, command=None):
		super().__init__(user=user, command=command)
		self.add_item(Button(label=label, url=url, style=ButtonStyle.link))