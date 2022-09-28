from os import environ
from time import time
from random import randint
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select
from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import process_chart_arguments, process_task

from commands.base import BaseCommand, ActionsView
from commands.ichibot import Ichibot


class FlowCommand(BaseCommand):
	flowGroup = SlashCommandGroup("flow", "Pull aggregated stocks orderflow.")

	async def respond(
		self,
		ctx,
		request,
		task
	):
		if request.flow_available():
			currentTask = task.get(task.get("currentPlatform"))
			timeframes = task.pop("timeframes")
			for i in range(task.get("requestCount")):
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[i]
				payload, responseMessage = await process_task(task, "chart")

				if payload is None:
					errorMessage = f"Requested orderflow data for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
				else:
					currentTask = task.get(payload.get("platform"))
					actions = ActionsView(user=ctx.author)
					await ctx.interaction.edit_original_message(file=discord.File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))), view=actions)

			await self.database.document("discord/statistics").set({request.snapshot: {"flow": Increment(1)}}, merge=True)
			await self.cleanup(ctx, request, removeView=True)

		else:
			embed = discord.Embed(title=":gem: Options and crypto orderflow are available as an Alpha Pro Subscription for individuals or communities for only $15.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alphabotsystem.com/pro).", color=constants.colors["deep purple"])
			# embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
			await ctx.interaction.edit_original_message(embed=embed)

	async def flow_proxy(self, ctx, tickerId, autodelete):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			embed = Embed(title="Flow command is being updated, and is currently unavailable.", description="An updated flow command is coming after slash commands are stable, which is the priority. All Alpha Pro subscribers using Alpha Flow during August and September 2021 will receive reimbursment in form of credit, or a refund if requested. No charges were made since then. All trials will also be reset.", color=constants.colors["gray"])
			await ctx.interaction.edit_original_message(embed=embed)
			return

			responseMessage, task = await process_chart_arguments([], ["Alpha Flow"], tickerId=tickerId)

			if responseMessage is not None:
				embed = discord.Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/pro/flow).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return
			elif autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				await ctx.interaction.edit_original_message(embed=embed)
				return

			self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /flow {tickerId} autodelete:{autodelete}")
			await self.unknown_error(ctx)

	@flowGroup.command(name="overview", description="Pull aggregated stocks orderflow overview.")
	async def flow_overview(
		self,
		ctx,
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		await self.flow_proxy(ctx, "options", autodelete)

	@flowGroup.command(name="search", description="Pull aggregated orderflow of a single stock.")
	async def flow_search(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		await self.flow_proxy(ctx, tickerId.upper(), autodelete)