from os import environ
from time import time
from pytz import utc
from uuid import uuid4
from datetime import datetime, timedelta
from parsedatetime import Calendar
from random import randint
from asyncio import CancelledError, sleep
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, Role
from discord.embeds import EmptyEmbed
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Increment
from pycoingecko import CoinGeckoAPI

from helpers import constants
from assets import static_storage
from Processor import process_chart_arguments, process_heatmap_arguments, process_quote_arguments, process_task, autocomplete_timeframe, autocomplete_market, autocomplete_category, autocomplete_size, autocomplete_group
from commands.heatmaps import autocomplete_theme
from DatabaseConnector import DatabaseConnector

from commands.base import BaseCommand, RedirectView, Confirm, autocomplete_type, autocomplete_performers_categories


cal = Calendar()

PERIODS = ["5 minutes", "10 minutes", "15 minutes", "20 minutes", "30 minutes", "1 hour", "2 hours", "3 hours", "4 hours", "6 hours", "8 hours", "12 hours", "1 day"]
PERIOD_TO_TIME = {"5 minutes": 5, "10 minutes": 10, "15 minutes": 15, "20 minutes": 20, "30 minutes": 30, "1 hour": 60, "2 hours": 120, "3 hours": 180, "4 hours": 240, "6 hours": 360, "8 hours": 480, "12 hours": 720, "1 day": 1440}
TIME_TO_PERIOD = {value: key for key, value in PERIOD_TO_TIME.items()}

EXCLUDE = ["Weekends", "Outside US Market Hours"]


def autocomplete_period(ctx):
	period = " ".join(ctx.options.get("period", "").lower().split())
	options = []
	for option in PERIODS:
		if period == "" or period in option.replace(" ", ""):
			options.append(option)
	return options

def autocomplete_date(ctx):
	date = " ".join(ctx.options.get("start", "").lower().split())
	if date == "":
		options = [datetime.now().strftime("%b %d %Y %H:%M") + " UTC"]
		return options
	else:
		timeStructs, _ = cal.parse(date)
		parsed = datetime(*timeStructs[:5])
		if parsed < datetime.now(): parsed += timedelta(days=1)
		options = [parsed.strftime("%b %d %Y %H:%M") + " UTC"]
		return options

def autocomplete_exclude(ctx):
	exclude = " ".join(ctx.options.get("exclude", "").lower().split())
	options = []
	for option in EXCLUDE:
		if exclude == "" or exclude in option.replace(" ", ""):
			options.append(option)
	return options


class ScheduleCommand(BaseCommand):
	scheduleGroup = SlashCommandGroup("schedule", "Schedule bot commands to get automatically posted periodically.")

	@scheduleGroup.command(name="chart", description="Schedule a chart to get automatically posted periodically.")
	async def chart(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments"),
		period: Option(str, "Period of time every which the chart will be posted.", name="period", autocomplete=autocomplete_period),
		start: Option(str, "Time at which the first chart will be posted.", name="start", autocomplete=autocomplete_date, required=False, default=None),
		exclude: Option(str, "Times to exclude from posting.", name="skip", autocomplete=autocomplete_exclude, required=False, default=None),
		message: Option(str, "Message to post with the chart.", name="message", required=False, default=None),
		role: Option(Role, "Role to tag on trigger.", name="role", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			try: await ctx.defer(ephemeral=True)
			except: return

			totalPostCount = await self.database.collection(f"details/scheduledPosts/{request.guildId}").count().get()

			if request.guildId == -1:
				embed = Embed(title="You cannot schedule a post in DMs.", color=constants.colors["gray"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.author).manage_messages:
				embed = Embed(title="You do not have the sufficient permission to create a scheduled post.", description="To be able to create a scheduled post, you must have the `manage messages` permission.", color=constants.colors["red"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
				embed = Embed(title=f"{self.bot.user.name} doesn't have the permission to send messages via Webhooks.", description=f"Grant `view channel` and `manage webhooks` permissions to {self.bot.user.name} in this channel to be able to schedule a post.", color=constants.colors["red"])
				embed.set_author(name="Missing permissions", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif totalPostCount[0][0].value >= 100:
				embed = Embed(title="You can only create up to 100 scheduled posts per community. Remove some before creating new ones by calling </schedule list:1041362666872131675>", color=constants.colors["red"])
				embed.set_author(name="Maximum number of scheduled posts reached", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif request.scheduled_posting_available():
				period = period.lower()

				if len(arguments.split(",")) > 1:
					embed = Embed(title="Only one request is allowed to be scheduled at once.", color=constants.colors["gray"])
					embed.set_author(name="Too many requests", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif period not in PERIODS:
					embed = Embed(title="The provided period is not valid. Please pick one of the available periods.", color=constants.colors["gray"])
					embed.set_author(name="Invalid period", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif exclude is not None and exclude not in EXCLUDE:
					embed = Embed(title="The provided skip value is not valid. Please pick one of the available options.", color=constants.colors["gray"])
					embed.set_author(name="Invalid skip value", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				if start is None:
					start = datetime.now().strftime("%b %d %Y %H:%M") + " UTC"
				try:
					timestamp = datetime.strptime(start, "%b %d %Y %H:%M UTC").timestamp()
				except:
					embed = Embed(title="The provided start date is not valid. Please provide a valid date and time.", color=constants.colors["gray"])
					embed.set_author(name="Invalid start time", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				while timestamp < time():
					timestamp += PERIOD_TO_TIME[period] * 60

				platforms = request.get_platform_order_for("c")
				arguments = arguments.lower().split()
				responseMessage, task = await process_chart_arguments(arguments[1:], platforms, tickerId=arguments[0].upper())

				if responseMessage is not None:
					description = "[Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting) unlocks additional assets, indicators, timeframes and more." if responseMessage.endswith("add-on.") else "Detailed guide with examples is available on [our website](https://www.alpha.bot/features/charting)."
					embed = Embed(title=responseMessage, description=description, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif task.get("requestCount") > 1:
					embed = Embed(title="Only one timeframe is allowed per request when scheduling a post.", color=constants.colors["gray"])
					embed.set_author(name="Too many requests", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				currentTask = task.get(task.get("currentPlatform"))
				timeframes = task.pop("timeframes")
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[0]
				payload, responseMessage = await process_task(task, "chart", origin=request.origin)

				files, embeds = [], []
				if responseMessage == "requires pro":
					embed = Embed(title=f"The requested chart for `{currentTask.get('ticker').get('name')}` is only available on TradingView Premium.", description="All TradingView Premium charts are bundled with the [Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					embeds.append(embed)
				elif payload is None:
					errorMessage = f"Requested chart for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.error_icon)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

				confirmation = None if payload is None or payload.get("data") is None else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=confirmation)
				except NotFound: pass
				await confirmation.wait()

				if confirmation is None:
					return
				if confirmation.value is None or not confirmation.value:
					try: await ctx.interaction.delete_original_response()
					except NotFound: pass
					return

				webhooks = await ctx.channel.webhooks()
				webhook = next((w for w in webhooks if w.user.id == self.bot.user.id), None)
				if webhook is None:
					avatar = await self.bot.user.avatar.read()
					webhook = await ctx.channel.create_webhook(name=self.bot.user.name, avatar=avatar)

				await self.database.document(f"details/scheduledPosts/{request.guildId}/{str(uuid4())}").set({
					"arguments": arguments,
					"authorId": str(request.authorId),
					"botId": str(self.bot.user.id),
					"channelId": str(request.channelId),
					"command": "chart",
					"exclude": None if exclude is None else exclude.lower(),
					"message": message,
					"period": PERIOD_TO_TIME[period],
					"role": None if role is None else str(role.id),
					"start": timestamp,
					"url": webhook.url
				})

				try: await ctx.interaction.edit_original_response(view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled chart will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Chart scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule chart {arguments} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="heatmap", description="Schedule a heatmap to get automatically posted periodically.")
	async def heatmap(
		self,
		ctx,
		period: Option(str, "Period of time every which the heatmap will be posted.", name="period", autocomplete=autocomplete_period),
		assetType: Option(str, "Heatmap asset class.", name="type", autocomplete=autocomplete_type, required=False, default=""),
		timeframe: Option(str, "Timeframe and coloring method for the heatmap.", name="color", autocomplete=autocomplete_timeframe, required=False, default=""),
		market: Option(str, "Heatmap market.", name="market", autocomplete=autocomplete_market, required=False, default=""),
		category: Option(str, "Specific asset category.", name="category", autocomplete=autocomplete_category, required=False, default=""),
		size: Option(str, "Method used to determine heatmap's block sizes.", name="size", autocomplete=autocomplete_size, required=False, default=""),
		group: Option(str, "Asset grouping method.", name="group", autocomplete=autocomplete_group, required=False, default=""),
		theme: Option(str, "Heatmap color theme.", name="theme", autocomplete=autocomplete_theme, required=False, default=""),
		start: Option(str, "Time at which the first heatmap will be posted.", name="start", autocomplete=autocomplete_date, required=False, default=None),
		exclude: Option(str, "Times to exclude from posting.", name="skip", autocomplete=autocomplete_exclude, required=False, default=None),
		message: Option(str, "Message to post with the heatmap.", name="message", required=False, default=None),
		role: Option(Role, "Role to tag on trigger.", name="role", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			try: await ctx.defer(ephemeral=True)
			except: return

			totalPostCount = await self.database.collection(f"details/scheduledPosts/{request.guildId}").count().get()

			if request.guildId == -1:
				embed = Embed(title="You cannot schedule a post in DMs.", color=constants.colors["gray"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.author).manage_messages:
				embed = Embed(title="You do not have the sufficient permission to create a scheduled post.", description="To be able to create a scheduled post, you must have the `manage messages` permission.", color=constants.colors["red"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
				embed = Embed(title=f"{self.bot.user.name} doesn't have the permission to send messages via Webhooks.", description=f"Grant `view channel` and `manage webhooks` permissions to {self.bot.user.name} in this channel to be able to schedule a post.", color=constants.colors["red"])
				embed.set_author(name="Missing permissions", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif totalPostCount[0][0].value >= 100:
				embed = Embed(title="You can only create up to 100 scheduled posts per community. Remove some before creating new ones by calling </schedule list:1041362666872131675>", color=constants.colors["red"])
				embed.set_author(name="Maximum number of scheduled posts reached", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif request.scheduled_posting_available():
				period = period.lower()

				if period not in PERIODS:
					embed = Embed(title="The provided period is not valid. Please pick one of the available periods.", color=constants.colors["gray"])
					embed.set_author(name="Invalid period", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif exclude is not None and exclude not in EXCLUDE:
					embed = Embed(title="The provided skip value is not valid. Please pick one of the available options.", color=constants.colors["gray"])
					embed.set_author(name="Invalid skip value", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				if start is None:
					start = datetime.now().strftime("%b %d %Y %H:%M") + " UTC"
				try:
					timestamp = datetime.strptime(start, "%b %d %Y %H:%M UTC").timestamp()
				except:
					embed = Embed(title="The provided start date is not valid. Please provide a valid date and time.", color=constants.colors["gray"])
					embed.set_author(name="Invalid start time", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				while timestamp < time():
					timestamp += PERIOD_TO_TIME[period] * 60

				platforms = request.get_platform_order_for("hmap", assetType=assetType)
				arguments = [assetType, timeframe, market, category, size, group, theme]
				responseMessage, task = await process_heatmap_arguments(arguments, platforms)

				if responseMessage is not None:
					embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alpha.bot/features/heatmaps).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif task.get("requestCount") > 1:
					embed = Embed(title="Only one timeframe is allowed per request when scheduling a post.", color=constants.colors["gray"])
					embed.set_author(name="Too many requests", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				currentTask = task.get(task.get("currentPlatform"))
				timeframes = task.pop("timeframes")
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[0]
				payload, responseMessage = await process_task(task, "heatmap", origin=request.origin)

				files, embeds = [], []
				if payload is None:
					errorMessage = "Requested heatmap is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Heatmap not available", icon_url=static_storage.error_icon)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

				confirmation = None if payload is None or payload.get("data") is None else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=confirmation)
				except NotFound: pass
				await confirmation.wait()

				if confirmation is None:
					return
				if confirmation.value is None or not confirmation.value:
					try: await ctx.interaction.delete_original_response()
					except NotFound: pass
					return

				webhooks = await ctx.channel.webhooks()
				webhook = next((w for w in webhooks if w.user.id == self.bot.user.id), None)
				if webhook is None:
					avatar = await self.bot.user.avatar.read()
					webhook = await ctx.channel.create_webhook(name=self.bot.user.name, avatar=avatar)

				await self.database.document(f"details/scheduledPosts/{request.guildId}/{str(uuid4())}").set({
					"arguments": arguments,
					"authorId": str(request.authorId),
					"botId": str(self.bot.user.id),
					"channelId": str(request.channelId),
					"command": "heatmap",
					"exclude": None if exclude is None else exclude.lower(),
					"message": message,
					"period": PERIOD_TO_TIME[period],
					"role": None if role is None else str(role.id),
					"start": timestamp,
					"url": webhook.url
				})

				try: await ctx.interaction.edit_original_response(view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled heatmap will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Heatmap scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule heatmap assetType:{assetType} color:{timeframe} market:{market} category:{category} size:{size} group:{group} theme:{theme} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="price", description="Schedule a price to get automatically posted periodically.")
	async def price(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
		period: Option(str, "Period of time every which the chart will be posted.", name="period", autocomplete=autocomplete_period),
		venue: Option(str, "Venue to pull the price from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default=""),
		start: Option(str, "Time at which the first chart will be posted.", name="start", autocomplete=autocomplete_date, required=False, default=None),
		exclude: Option(str, "Times to exclude from posting.", name="skip", autocomplete=autocomplete_exclude, required=False, default=None),
		message: Option(str, "Message to post with the chart.", name="message", required=False, default=None),
		role: Option(Role, "Role to tag on trigger.", name="role", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			try: await ctx.defer(ephemeral=True)
			except: return

			totalPostCount = await self.database.collection(f"details/scheduledPosts/{request.guildId}").count().get()

			if request.guildId == -1:
				embed = Embed(title="You cannot schedule a post in DMs.", color=constants.colors["gray"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.author).manage_messages:
				embed = Embed(title="You do not have the sufficient permission to create a scheduled post.", description="To be able to create a scheduled post, you must have the `manage messages` permission.", color=constants.colors["red"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
				embed = Embed(title=f"{self.bot.user.name} doesn't have the permission to send messages via Webhooks.", description=f"Grant `view channel` and `manage webhooks` permissions to {self.bot.user.name} in this channel to be able to schedule a post.", color=constants.colors["red"])
				embed.set_author(name="Missing permissions", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif totalPostCount[0][0].value >= 100:
				embed = Embed(title="You can only create up to 100 scheduled posts per community. Remove some before creating new ones by calling </schedule list:1041362666872131675>", color=constants.colors["red"])
				embed.set_author(name="Maximum number of scheduled posts reached", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif request.scheduled_posting_available():
				period = period.lower()

				if period not in PERIODS:
					embed = Embed(title="The provided period is not valid. Please pick one of the available periods.", color=constants.colors["gray"])
					embed.set_author(name="Invalid period", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif exclude is not None and exclude not in EXCLUDE:
					embed = Embed(title="The provided skip value is not valid. Please pick one of the available options.", color=constants.colors["gray"])
					embed.set_author(name="Invalid skip value", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				if start is None:
					start = datetime.now().strftime("%b %d %Y %H:%M") + " UTC"
				try:
					timestamp = datetime.strptime(start, "%b %d %Y %H:%M UTC").timestamp()
				except:
					embed = Embed(title="The provided start date is not valid. Please provide a valid date and time.", color=constants.colors["gray"])
					embed.set_author(name="Invalid start time", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				while timestamp < time():
					timestamp += PERIOD_TO_TIME[period] * 60

				platforms = request.get_platform_order_for("p")
				responseMessage, task = await process_quote_arguments([venue], platforms, tickerId=tickerId.upper())

				if responseMessage is not None:
					embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alpha.bot/features/prices).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				currentTask = task.get(task.get("currentPlatform"))
				payload, responseMessage = await process_task(task, "quote")

				if payload is None or "quotePrice" not in payload:
					errorMessage = f"Requested quote for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
				else:
					currentTask = task.get(payload.get("platform"))
					if payload.get("platform") in ["Alternative.me", "CNN Business"]:
						embed = Embed(title=f"{payload['quotePrice']} *({payload['change']})*", description=payload.get("quoteConvertedPrice", EmptyEmbed), color=constants.colors[payload["messageColor"]])
						embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
						embed.set_footer(text=payload["sourceText"])
					else:
						embed = Embed(title="{}{}".format(payload["quotePrice"], f" *({payload['change']})*" if "change" in payload else ""), description=payload.get("quoteConvertedPrice", EmptyEmbed), color=constants.colors[payload["messageColor"]])
						embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
						embed.set_footer(text=payload["sourceText"])

				confirmation = None if payload is None or "quotePrice" not in payload else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embed=embed, view=confirmation)
				except NotFound: pass
				await confirmation.wait()

				if confirmation is None:
					return
				if confirmation.value is None or not confirmation.value:
					try: await ctx.interaction.delete_original_response()
					except NotFound: pass
					return

				webhooks = await ctx.channel.webhooks()
				webhook = next((w for w in webhooks if w.user.id == self.bot.user.id), None)
				if webhook is None:
					avatar = await self.bot.user.avatar.read()
					webhook = await ctx.channel.create_webhook(name=self.bot.user.name, avatar=avatar)

				await self.database.document(f"details/scheduledPosts/{request.guildId}/{str(uuid4())}").set({
					"arguments": [tickerId, venue],
					"authorId": str(request.authorId),
					"botId": str(self.bot.user.id),
					"channelId": str(request.channelId),
					"command": "price",
					"exclude": None if exclude is None else exclude.lower(),
					"message": message,
					"period": PERIOD_TO_TIME[period],
					"role": None if role is None else str(role.id),
					"start": timestamp,
					"url": webhook.url
				})

				try: await ctx.interaction.edit_original_response(view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled price will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Chart scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule chart {arguments} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="volume", description="Schedule 24-hour volume to get automatically posted periodically.")
	async def price(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
		period: Option(str, "Period of time every which the chart will be posted.", name="period", autocomplete=autocomplete_period),
		venue: Option(str, "Venue to pull the price from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default=""),
		start: Option(str, "Time at which the first chart will be posted.", name="start", autocomplete=autocomplete_date, required=False, default=None),
		exclude: Option(str, "Times to exclude from posting.", name="skip", autocomplete=autocomplete_exclude, required=False, default=None),
		message: Option(str, "Message to post with the chart.", name="message", required=False, default=None),
		role: Option(Role, "Role to tag on trigger.", name="role", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			try: await ctx.defer(ephemeral=True)
			except: return

			totalPostCount = await self.database.collection(f"details/scheduledPosts/{request.guildId}").count().get()

			if request.guildId == -1:
				embed = Embed(title="You cannot schedule a post in DMs.", color=constants.colors["gray"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.author).manage_messages:
				embed = Embed(title="You do not have the sufficient permission to create a scheduled post.", description="To be able to create a scheduled post, you must have the `manage messages` permission.", color=constants.colors["red"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
				embed = Embed(title=f"{self.bot.user.name} doesn't have the permission to send messages via Webhooks.", description=f"Grant `view channel` and `manage webhooks` permissions to {self.bot.user.name} in this channel to be able to schedule a post.", color=constants.colors["red"])
				embed.set_author(name="Missing permissions", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif totalPostCount[0][0].value >= 100:
				embed = Embed(title="You can only create up to 100 scheduled posts per community. Remove some before creating new ones by calling </schedule list:1041362666872131675>", color=constants.colors["red"])
				embed.set_author(name="Maximum number of scheduled posts reached", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif request.scheduled_posting_available():
				period = period.lower()

				if period not in PERIODS:
					embed = Embed(title="The provided period is not valid. Please pick one of the available periods.", color=constants.colors["gray"])
					embed.set_author(name="Invalid period", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif exclude is not None and exclude not in EXCLUDE:
					embed = Embed(title="The provided skip value is not valid. Please pick one of the available options.", color=constants.colors["gray"])
					embed.set_author(name="Invalid skip value", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				if start is None:
					start = datetime.now().strftime("%b %d %Y %H:%M") + " UTC"
				try:
					timestamp = datetime.strptime(start, "%b %d %Y %H:%M UTC").timestamp()
				except:
					embed = Embed(title="The provided start date is not valid. Please provide a valid date and time.", color=constants.colors["gray"])
					embed.set_author(name="Invalid start time", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				while timestamp < time():
					timestamp += PERIOD_TO_TIME[period] * 60

				platforms = request.get_platform_order_for("v")
				responseMessage, task = await process_quote_arguments([venue], platforms, tickerId=tickerId.upper())

				if responseMessage is not None:
					embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alpha.bot/features/volume).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				currentTask = task.get(task.get("currentPlatform"))
				payload, responseMessage = await process_task(task, "quote")

				if payload is None or "quoteVolume" not in payload:
					errorMessage = f"Requested volume for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
				else:
					currentTask = task.get(payload.get("platform"))
					embed = Embed(title=payload["quoteVolume"], description=payload.get("quoteConvertedVolume", EmptyEmbed), color=constants.colors["orange"])
					embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
					embed.set_footer(text=payload["sourceText"])

				confirmation = None if payload is None or "quoteVolume" not in payload else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embed=embed, view=confirmation)
				except NotFound: pass
				await confirmation.wait()

				if confirmation is None:
					return
				if confirmation.value is None or not confirmation.value:
					try: await ctx.interaction.delete_original_response()
					except NotFound: pass
					return

				webhooks = await ctx.channel.webhooks()
				webhook = next((w for w in webhooks if w.user.id == self.bot.user.id), None)
				if webhook is None:
					avatar = await self.bot.user.avatar.read()
					webhook = await ctx.channel.create_webhook(name=self.bot.user.name, avatar=avatar)

				await self.database.document(f"details/scheduledPosts/{request.guildId}/{str(uuid4())}").set({
					"arguments": [tickerId, venue],
					"authorId": str(request.authorId),
					"botId": str(self.bot.user.id),
					"channelId": str(request.channelId),
					"command": "volume",
					"exclude": None if exclude is None else exclude.lower(),
					"message": message,
					"period": PERIOD_TO_TIME[period],
					"role": None if role is None else str(role.id),
					"start": timestamp,
					"url": webhook.url
				})

				try: await ctx.interaction.edit_original_response(view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled 24-hour volume will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Chart scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule chart {arguments} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="top-performers", description="Schedule fear & greed index chart to get automatically posted periodically.")
	async def lookup_top(
		self,
		ctx,
		category: Option(str, "Ranking type.", name="category", autocomplete=autocomplete_performers_categories),
		period: Option(str, "Period of time every which the chart will be posted.", name="period", autocomplete=autocomplete_period),
		limit: Option(int, "Asset count limit. Defaults to top 250 by market cap, maximum is 1000.", name="limit", required=False, default=250),
		start: Option(str, "Time at which the first chart will be posted.", name="start", autocomplete=autocomplete_date, required=False, default=None),
		exclude: Option(str, "Times to exclude from posting.", name="skip", autocomplete=autocomplete_exclude, required=False, default=None),
		message: Option(str, "Message to post with the chart.", name="message", required=False, default=None),
		role: Option(Role, "Role to tag on trigger.", name="role", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			try: await ctx.defer(ephemeral=True)
			except: return

			totalPostCount = await self.database.collection(f"details/scheduledPosts/{request.guildId}").count().get()

			if request.guildId == -1:
				embed = Embed(title="You cannot schedule a post in DMs.", color=constants.colors["gray"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.author).manage_messages:
				embed = Embed(title="You do not have the sufficient permission to create a scheduled post.", description="To be able to create a scheduled post, you must have the `manage messages` permission.", color=constants.colors["red"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
				embed = Embed(title=f"{self.bot.user.name} doesn't have the permission to send messages via Webhooks.", description=f"Grant `view channel` and `manage webhooks` permissions to {self.bot.user.name} in this channel to be able to schedule a post.", color=constants.colors["red"])
				embed.set_author(name="Missing permissions", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif totalPostCount[0][0].value >= 100:
				embed = Embed(title="You can only create up to 100 scheduled posts per community. Remove some before creating new ones by calling </schedule list:1041362666872131675>", color=constants.colors["red"])
				embed.set_author(name="Maximum number of scheduled posts reached", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif request.scheduled_posting_available():
				period = period.lower()

				if period not in PERIODS:
					embed = Embed(title="The provided period is not valid. Please pick one of the available periods.", color=constants.colors["gray"])
					embed.set_author(name="Invalid period", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif exclude is not None and exclude not in EXCLUDE:
					embed = Embed(title="The provided skip value is not valid. Please pick one of the available options.", color=constants.colors["gray"])
					embed.set_author(name="Invalid skip value", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				if start is None:
					start = datetime.now().strftime("%b %d %Y %H:%M") + " UTC"
				try:
					timestamp = datetime.strptime(start, "%b %d %Y %H:%M UTC").timestamp()
				except:
					embed = Embed(title="The provided start date is not valid. Please provide a valid date and time.", color=constants.colors["gray"])
					embed.set_author(name="Invalid start time", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				while timestamp < time():
					timestamp += PERIOD_TO_TIME[period] * 60

				category = " ".join(category.lower().split())
				if category == "crypto gainers":
					rawData = []
					cg = CoinGeckoAPI()
					page = 1
					while True:
						try:
							rawData += cg.get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, page=page, price_change_percentage="24h")
							page += 1
							if page > 4: break
							await sleep(0.6)
						except: await sleep(5)

					response = []
					for e in rawData[:max(10, limit)]:
						if e.get("price_change_percentage_24h_in_currency", None) is not None:
							response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
					response = sorted(response, key=lambda k: k["change"], reverse=True)[:10]

					embed = Embed(title="Top gainers", color=constants.colors["deep purple"])
					for token in response:
						embed.add_field(name=token["symbol"], value="Gained {:,.2f} %".format(token["change"]), inline=True)

				elif category == "crypto losers":
					rawData = []
					cg = CoinGeckoAPI()
					page = 1
					while True:
						try:
							rawData += cg.get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, page=page, price_change_percentage="24h")
							page += 1
							if page > 4: break
							await sleep(0.6)
						except: await sleep(5)

					response = []
					for e in rawData[:max(10, limit)]:
						if e.get("price_change_percentage_24h_in_currency", None) is not None:
							response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
					response = sorted(response, key=lambda k: k["change"])[:10]

					embed = Embed(title="Top losers", color=constants.colors["deep purple"])
					for token in response:
						embed.add_field(name=token["symbol"], value="Lost {:,.2f} %".format(token["change"]), inline=True)

				else:
					embed = Embed(title="The specified category is invalid.", description="Detailed guide with examples is available on [our website](https://www.alpha.bot/features/lookup).", color=constants.colors["deep purple"])
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				confirmation = Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embed=embed, view=confirmation)
				except NotFound: pass
				await confirmation.wait()

				if confirmation is None:
					return
				if confirmation.value is None or not confirmation.value:
					try: await ctx.interaction.delete_original_response()
					except NotFound: pass
					return

				webhooks = await ctx.channel.webhooks()
				webhook = next((w for w in webhooks if w.user.id == self.bot.user.id), None)
				if webhook is None:
					avatar = await self.bot.user.avatar.read()
					webhook = await ctx.channel.create_webhook(name=self.bot.user.name, avatar=avatar)

				await self.database.document(f"details/scheduledPosts/{request.guildId}/{str(uuid4())}").set({
					"arguments": [category, str(limit)],
					"authorId": str(request.authorId),
					"botId": str(self.bot.user.id),
					"channelId": str(request.channelId),
					"command": "lookup top-performers",
					"exclude": None if exclude is None else exclude.lower(),
					"message": message,
					"period": PERIOD_TO_TIME[period],
					"role": None if role is None else str(role.id),
					"start": timestamp,
					"url": webhook.url
				})

				try: await ctx.interaction.edit_original_response(view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled chart will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Chart scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule chart {arguments} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="fgi", description="Schedule fear & greed index chart to get automatically posted periodically.")
	async def lookup_fgi(
		self,
		ctx,
		period: Option(str, "Period of time every which the chart will be posted.", name="period", autocomplete=autocomplete_period),
		assetType: Option(str, "Fear & greed market type", name="market", autocomplete=autocomplete_type, required=False, default=""),
		start: Option(str, "Time at which the first chart will be posted.", name="start", autocomplete=autocomplete_date, required=False, default=None),
		exclude: Option(str, "Times to exclude from posting.", name="skip", autocomplete=autocomplete_exclude, required=False, default=None),
		message: Option(str, "Message to post with the chart.", name="message", required=False, default=None),
		role: Option(Role, "Role to tag on trigger.", name="role", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			try: await ctx.defer(ephemeral=True)
			except: return

			totalPostCount = await self.database.collection(f"details/scheduledPosts/{request.guildId}").count().get()

			if request.guildId == -1:
				embed = Embed(title="You cannot schedule a post in DMs.", color=constants.colors["gray"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.author).manage_messages:
				embed = Embed(title="You do not have the sufficient permission to create a scheduled post.", description="To be able to create a scheduled post, you must have the `manage messages` permission.", color=constants.colors["red"])
				embed.set_author(name="Permission denied", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
				embed = Embed(title=f"{self.bot.user.name} doesn't have the permission to send messages via Webhooks.", description=f"Grant `view channel` and `manage webhooks` permissions to {self.bot.user.name} in this channel to be able to schedule a post.", color=constants.colors["red"])
				embed.set_author(name="Missing permissions", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif totalPostCount[0][0].value >= 100:
				embed = Embed(title="You can only create up to 100 scheduled posts per community. Remove some before creating new ones by calling </schedule list:1041362666872131675>", color=constants.colors["red"])
				embed.set_author(name="Maximum number of scheduled posts reached", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif request.scheduled_posting_available():
				period = period.lower()

				if assetType != "":
					if assetType.lower() == "crypto":
						assetType = "am"
					elif assetType.lower() == "stocks":
						assetType = "cnn"
					else:
						embed = Embed(title="Asset type is invalid. Only stocks and crypto markets are supported.", color=constants.colors["gray"])
						embed.set_author(name="Invalid market", icon_url=static_storage.error_icon)
						try: await ctx.interaction.edit_original_response(embed=embed)
						except NotFound: pass
						return
				elif period not in PERIODS:
					embed = Embed(title="The provided period is not valid. Please pick one of the available periods.", color=constants.colors["gray"])
					embed.set_author(name="Invalid period", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif exclude is not None and exclude not in EXCLUDE:
					embed = Embed(title="The provided skip value is not valid. Please pick one of the available options.", color=constants.colors["gray"])
					embed.set_author(name="Invalid skip value", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				if start is None:
					start = datetime.now().strftime("%b %d %Y %H:%M") + " UTC"
				try:
					timestamp = datetime.strptime(start, "%b %d %Y %H:%M UTC").timestamp()
				except:
					embed = Embed(title="The provided start date is not valid. Please provide a valid date and time.", color=constants.colors["gray"])
					embed.set_author(name="Invalid start time", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				while timestamp < time():
					timestamp += PERIOD_TO_TIME[period] * 60

				platforms = request.get_platform_order_for("c")
				responseMessage, task = await process_chart_arguments([assetType], platforms, tickerId="FGI")

				if responseMessage is not None:
					description = "[Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting) unlocks additional assets, indicators, timeframes and more." if responseMessage.endswith("add-on.") else "Detailed guide with examples is available on [our website](https://www.alpha.bot/features/charting)."
					embed = Embed(title=responseMessage, description=description, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif task.get("requestCount") > 1:
					embed = Embed(title="Only one timeframe is allowed per request when scheduling a post.", color=constants.colors["gray"])
					embed.set_author(name="Too many requests", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				currentTask = task.get(task.get("currentPlatform"))
				timeframes = task.pop("timeframes")
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[0]
				payload, responseMessage = await process_task(task, "chart", origin=request.origin)

				files, embeds = [], []
				if responseMessage == "requires pro":
					embed = Embed(title=f"The requested chart for `{currentTask.get('ticker').get('name')}` is only available on TradingView Premium.", description="All TradingView Premium charts are bundled with the [Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					embeds.append(embed)
				elif payload is None:
					errorMessage = f"Requested chart for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.error_icon)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))

				confirmation = None if payload is None or payload.get("data") is None else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=confirmation)
				except NotFound: pass
				await confirmation.wait()

				if confirmation is None:
					return
				if confirmation.value is None or not confirmation.value:
					try: await ctx.interaction.delete_original_response()
					except NotFound: pass
					return

				webhooks = await ctx.channel.webhooks()
				webhook = next((w for w in webhooks if w.user.id == self.bot.user.id), None)
				if webhook is None:
					avatar = await self.bot.user.avatar.read()
					webhook = await ctx.channel.create_webhook(name=self.bot.user.name, avatar=avatar)

				await self.database.document(f"details/scheduledPosts/{request.guildId}/{str(uuid4())}").set({
					"arguments": ["fgi", assetType],
					"authorId": str(request.authorId),
					"botId": str(self.bot.user.id),
					"channelId": str(request.channelId),
					"command": "chart",
					"exclude": None if exclude is None else exclude.lower(),
					"message": message,
					"period": PERIOD_TO_TIME[period],
					"role": None if role is None else str(role.id),
					"start": timestamp,
					"url": webhook.url
				})

				try: await ctx.interaction.edit_original_response(view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled chart will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Chart scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule chart {arguments} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="list", description="List all scheduled posts.")
	async def schedule_list(self, ctx):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			totalPostCount = await self.database.collection(f"details/scheduledPosts/{request.guildId}").count().get()

			if totalPostCount[0][0].value == 0:
				embed = Embed(title="You haven't set any scheduled posts yet.", color=constants.colors["gray"])
				embed.set_author(name="Scheduled Posts", icon_url=static_storage.error_icon)
				try: await ctx.respond(embed=embed, ephemeral=True)
				except NotFound: pass

			else:
				embed = Embed(title=f"You've created {totalPostCount[0][0].value} scheduled post{'' if totalPostCount[0][0].value == 1 else 's'} in this community. You can manage them on the community dashboard.", color=constants.colors["light blue"])
				try: await ctx.respond(embed=embed, view=RedirectView(f"https://www.alpha.bot/communities/{request.guildId}?tab=2"), ephemeral=True)
				except NotFound: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule list")
			await self.unknown_error(ctx)


class DeleteView(View):
	def __init__(self, database, pathId, userId=None):
		super().__init__(timeout=None)
		self.database = database
		self.pathId = pathId
		self.userId = userId

	@button(label="Delete", style=ButtonStyle.danger)
	async def delete(self, button: Button, interaction: Interaction):
		if self.userId != interaction.user.id: return
		await self.database.document(self.pathId).delete()
		embed = Embed(title="Scheduled post deleted", color=constants.colors["gray"])
		await interaction.response.edit_message(embed=embed, view=None)