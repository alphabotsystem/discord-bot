from os import environ
from time import time
from uuid import uuid4
from datetime import datetime, timedelta
from parsedatetime import Calendar
from random import randint
from asyncio import gather, CancelledError
from aiohttp import ClientSession
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, Role, Thread, Permissions
from discord.embeds import EmptyEmbed
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Increment
from google.cloud.firestore_v1.base_query import FieldFilter
from pycoingecko import CoinGeckoAPI

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage
from Processor import process_chart_arguments, process_heatmap_arguments, process_quote_arguments, process_task, autocomplete_hmap_timeframe, autocomplete_market, autocomplete_category, autocomplete_size, autocomplete_group, autocomplete_layout_timeframe
from commands.heatmaps import autocomplete_theme
from DatabaseConnector import DatabaseConnector

from commands.base import BaseCommand, RedirectView, Confirm, autocomplete_fgi_type, autocomplete_hmap_type, autocomplete_movers_categories, autocomplete_layouts, MARKET_MOVERS_OPTIONS


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
	scheduleGroup = SlashCommandGroup("schedule", "Schedule bot commands to get automatically posted periodically.", guild_only=True, default_member_permissions=Permissions(manage_messages=True))

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

			if isinstance(ctx.channel, Thread):
				embed = Embed(title="You cannot schedule a post in a thread.", color=constants.colors["gray"])
				embed.set_author(name="Invalid channel", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.interaction.app_permissions.manage_webhooks:
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
				responseMessage, task = await process_chart_arguments(arguments[1:], platforms, tickerId=arguments[0].upper(), defaults=request.guildProperties["charting"])

				if responseMessage is not None:
					description = "[Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting) unlocks additional assets, indicators, timeframes and more." if responseMessage.endswith("add-on.") else get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/charting")
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
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))
					embed = Embed(title="Are you sure you want to schedule this post?", color=constants.colors["pink"])
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)

				confirmation = None if payload is None or payload.get("data") is None else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=confirmation)
				except NotFound: pass

				if confirmation is None:
					return
				await confirmation.wait()

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

				try: await ctx.interaction.edit_original_response(embeds=embeds[:-1], view=None)
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
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule chart {' '.join(arguments)} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="layout", description="Schedule a TradingView Layout to get automatically posted periodically.")
	async def layout(
		self,
		ctx,
		name: Option(str, "Name of the layout to pull.", name="name", autocomplete=autocomplete_layouts),
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
		period: Option(str, "Period of time every which the chart will be posted.", name="period", autocomplete=autocomplete_period),
		timeframe: Option(str, "Preferred chart timeframe to use.", name="timeframe", autocomplete=autocomplete_layout_timeframe, required=False, default=""),
		venue: Option(str, "Venue to pull the chart from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default=""),
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

			if isinstance(ctx.channel, Thread):
				embed = Embed(title="You cannot schedule a post in a thread.", color=constants.colors["gray"])
				embed.set_author(name="Invalid channel", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.interaction.app_permissions.manage_webhooks:
				embed = Embed(title=f"{self.bot.user.name} doesn't have the permission to send messages via Webhooks.", description=f"Grant `view channel` and `manage webhooks` permissions to {self.bot.user.name} in this channel to be able to schedule a post.", color=constants.colors["red"])
				embed.set_author(name="Missing permissions", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif totalPostCount[0][0].value >= 100:
				embed = Embed(title="You can only create up to 100 scheduled posts per community. Remove some before creating new ones by calling </schedule list:1041362666872131675>", color=constants.colors["red"])
				embed.set_author(name="Maximum number of scheduled posts reached", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif request.scheduled_posting_available() and request.tradingview_layouts_available():
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

				arguments = [venue, timeframe]
				[(responseMessage, task), layout] = await gather(
					process_chart_arguments(arguments, ["TradingView Relay"], tickerId=tickerId.upper(), defaults=request.guildProperties["charting"]),
					self.database.collection(f"discord/properties/layouts").where(filter=FieldFilter("label", "==", name)).where(filter=FieldFilter("guildId", "==", str(request.guildId))).get()
				)

				if responseMessage is not None:
					embed = Embed(title=responseMessage, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/layouts"), color=constants.colors["gray"])
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

				url = layout[0].to_dict()["url"]
				task["TradingView Relay"]["url"] = url

				currentTask = task.get(task.get("currentPlatform"))
				timeframes = task.pop("timeframes")
				for p, t in timeframes.items(): task[p]["currentTimeframe"] = t[0]
				payload, responseMessage = await process_task(task, "chart", origin=request.origin)

				files, embeds = [], []
				if payload is None:
					errorMessage = f"Requested chart for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))
					embed = Embed(title="Are you sure you want to schedule this post?", color=constants.colors["pink"])
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)

				confirmation = None if payload is None or payload.get("data") is None else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=confirmation)
				except NotFound: pass

				if confirmation is None:
					return
				await confirmation.wait()

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
					"arguments": [url, tickerId, venue, timeframe],
					"authorId": str(request.authorId),
					"botId": str(self.bot.user.id),
					"channelId": str(request.channelId),
					"command": "layout",
					"exclude": None if exclude is None else exclude.lower(),
					"message": message,
					"period": PERIOD_TO_TIME[period],
					"role": None if role is None else str(role.id),
					"start": timestamp,
					"url": webhook.url
				})

				try: await ctx.interaction.edit_original_response(embeds=embeds[:-1], view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled TradingView Layout will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="TradingView Layout scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality and TradingView Layouts are available as add-on subscriptions for communities for a total of only $15.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule layout ticker:{tickerId} venue:{venue} timeframe:{timeframe} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="heatmap", description="Schedule a heatmap to get automatically posted periodically.")
	async def heatmap(
		self,
		ctx,
		period: Option(str, "Period of time every which the heatmap will be posted.", name="period", autocomplete=autocomplete_period),
		assetType: Option(str, "Heatmap asset class.", name="type", autocomplete=autocomplete_hmap_type, required=False, default=""),
		timeframe: Option(str, "Timeframe and coloring method for the heatmap.", name="color", autocomplete=autocomplete_hmap_timeframe, required=False, default=""),
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

			if isinstance(ctx.channel, Thread):
				embed = Embed(title="You cannot schedule a post in a thread.", color=constants.colors["gray"])
				embed.set_author(name="Invalid channel", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.interaction.app_permissions.manage_webhooks:
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
					embed = Embed(title=responseMessage, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/heatmaps"), color=constants.colors["gray"])
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
					embed = Embed(title="Are you sure you want to schedule this post?", color=constants.colors["pink"])
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)

				confirmation = None if payload is None or payload.get("data") is None else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=confirmation)
				except NotFound: pass

				if confirmation is None:
					return
				await confirmation.wait()

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

				try: await ctx.interaction.edit_original_response(embeds=embeds[:-1], view=None)
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
		except:
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

			if isinstance(ctx.channel, Thread):
				embed = Embed(title="You cannot schedule a post in a thread.", color=constants.colors["gray"])
				embed.set_author(name="Invalid channel", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.interaction.app_permissions.manage_webhooks:
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
					embed = Embed(title=responseMessage, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/prices"), color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				currentTask = task.get(task.get("currentPlatform"))
				payload, responseMessage = await process_task(task, "quote")

				embeds = []
				if payload is None or "quotePrice" not in payload:
					errorMessage = f"Requested quote for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
					embeds.append(embed)
				else:
					currentTask = task.get(payload.get("platform"))
					if payload.get("platform") in ["Alternative.me", "CNN Business"]:
						embed = Embed(title=f"{payload['quotePrice']} *({payload['change']})*", description=payload.get("quoteConvertedPrice", EmptyEmbed), color=constants.colors[payload["messageColor"]])
						embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
						embed.set_footer(text=payload["sourceText"])
						embeds.append(embed)
					else:
						embed = Embed(title="{}{}".format(payload["quotePrice"], f" *({payload['change']})*" if "change" in payload else ""), description=payload.get("quoteConvertedPrice", EmptyEmbed), color=constants.colors[payload["messageColor"]])
						embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
						embed.set_footer(text=payload["sourceText"])
						embeds.append(embed)
					embed = Embed(title="Are you sure you want to schedule this post?", color=constants.colors["pink"])
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)

				confirmation = None if payload is None or "quotePrice" not in payload else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, view=confirmation)
				except NotFound: pass

				if confirmation is None:
					return
				await confirmation.wait()

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

				try: await ctx.interaction.edit_original_response(embeds=embeds[:-1], view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled price will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Price scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule price ticker:{tickerId} venue:{venue} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="volume", description="Schedule 24-hour volume to get automatically posted periodically.")
	async def volume(
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

			if isinstance(ctx.channel, Thread):
				embed = Embed(title="You cannot schedule a post in a thread.", color=constants.colors["gray"])
				embed.set_author(name="Invalid channel", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.interaction.app_permissions.manage_webhooks:
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
					embed = Embed(title=responseMessage, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/volume"), color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				currentTask = task.get(task.get("currentPlatform"))
				payload, responseMessage = await process_task(task, "quote")

				embeds = []
				if payload is None or "quoteVolume" not in payload:
					errorMessage = f"Requested volume for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
					embeds.append(embed)
				else:
					currentTask = task.get(payload.get("platform"))
					embed = Embed(title=payload["quoteVolume"], description=payload.get("quoteConvertedVolume", EmptyEmbed), color=constants.colors["orange"])
					embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
					embed.set_footer(text=payload["sourceText"])
					embeds.append(embed)
					embed = Embed(title="Are you sure you want to schedule this post?", color=constants.colors["pink"])
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)

				confirmation = None if payload is None or "quoteVolume" not in payload else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, view=confirmation)
				except NotFound: pass

				if confirmation is None:
					return
				await confirmation.wait()

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

				try: await ctx.interaction.edit_original_response(embeds=embeds[:-1], view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled 24-hour volume will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Volume scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule volume ticker:{tickerId} venue:{venue} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="market-movers", description="Schedule fear & greed index chart to get automatically posted periodically.")
	async def lookup_top(
		self,
		ctx,
		category: Option(str, "Ranking type.", name="category", autocomplete=autocomplete_movers_categories),
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

			if isinstance(ctx.channel, Thread):
				embed = Embed(title="You cannot schedule a post in a thread.", color=constants.colors["gray"])
				embed.set_author(name="Invalid channel", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.interaction.app_permissions.manage_webhooks:
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
				if category not in MARKET_MOVERS_OPTIONS:
					embed = Embed(title="The specified category is invalid.", description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/lookup"), color=constants.colors["deep purple"])
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				parts = category.split(" ")
				direction = parts.pop()
				market = " ".join(parts)
				embed = Embed(title=f"Top {category}", color=constants.colors["deep purple"])

				if market == "crypto":
					rawData = []
					cg = CoinGeckoAPI(api_key=environ["COINGECKO_API_KEY"])
					page = 1
					while True:
						rawData += cg.get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, page=page, price_change_percentage="24h")
						page += 1
						if page > 4: break

					response = []
					for e in rawData[:max(10, limit)]:
						if e.get("price_change_percentage_24h_in_currency", None) is not None:
							response.append({"name": e["name"], "symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})

					if direction == "gainers":
						response = sorted(response, key=lambda k: k["change"], reverse=True)
					elif direction == "losers":
						response = sorted(response, key=lambda k: k["change"])

					for token in response[:9]:
						embed.add_field(name=f"{token['name']} (`{token['symbol']}`)", value="{:+,.2f}%".format(token["change"]), inline=True)

				else:
					async with ClientSession() as session:
						url = f"https://api.twelvedata.com/market_movers/{market.replace(' ', '_')}?apikey={environ['TWELVEDATA_KEY']}&direction={direction}&outputsize=50"
						async with session.get(url) as resp:
							response = await resp.json()
							assets = filter(
								lambda e: not e['name'].lower().startswith("test") and "testfund" not in e['name'].lower().replace(" ", ""),
								response["values"]
							)
							for asset in list(assets)[:9]:
								embed.add_field(name=f"{asset['name']} (`{asset['symbol']}`)", value="{:+,.2f}%".format(asset["percent_change"]), inline=True)

				embeds = [embed]

				embed = Embed(title="Are you sure you want to schedule this post?", color=constants.colors["pink"])
				embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
				embeds.append(embed)

				confirmation = Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, view=confirmation)
				except NotFound: pass

				if confirmation is None:
					return
				await confirmation.wait()

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
					"command": "lookup market-movers",
					"exclude": None if exclude is None else exclude.lower(),
					"message": message,
					"period": PERIOD_TO_TIME[period],
					"role": None if role is None else str(role.id),
					"start": timestamp,
					"url": webhook.url
				})

				try: await ctx.interaction.edit_original_response(embeds=embeds[:-1], view=None)
				except NotFound: pass

				embed = Embed(title="Scheduled post has been created.", description=f"The scheduled list will be posted publicly every {period.removeprefix('1 ')} in this channel, starting {start}.", color=constants.colors["purple"])
				embed.set_author(name="Market-movers scheduled", icon_url=self.bot.user.avatar.url)
				await ctx.followup.send(embed=embed, ephemeral=True)
			else:
				embed = Embed(title=":gem: Scheduled Posting functionality is available as an add-on subscription for communities for only $5.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/scheduled-posting).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule top category:{category} limit:{limit} period:{period} start:{start}")
			await self.unknown_error(ctx)

	@scheduleGroup.command(name="fgi", description="Schedule fear & greed index chart to get automatically posted periodically.")
	async def lookup_fgi(
		self,
		ctx,
		period: Option(str, "Period of time every which the chart will be posted.", name="period", autocomplete=autocomplete_period),
		assetType: Option(str, "Fear & greed market type", name="market", autocomplete=autocomplete_fgi_type, required=False, default=""),
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

			if isinstance(ctx.channel, Thread):
				embed = Embed(title="You cannot schedule a post in a thread.", color=constants.colors["gray"])
				embed.set_author(name="Invalid channel", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			elif not ctx.interaction.app_permissions.manage_webhooks:
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
					description = "[Advanced Charting add-on](https://www.alpha.bot/pro/advanced-charting) unlocks additional assets, indicators, timeframes and more." if responseMessage.endswith("add-on.") else get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/lookup")
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
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)
				else:
					files.append(File(payload.get("data"), filename="{:.0f}-{}-{}.png".format(time() * 1000, request.authorId, randint(1000, 9999))))
					embed = Embed(title="Are you sure you want to schedule this post?", color=constants.colors["pink"])
					embed.set_author(name="Schedule confirmation", icon_url=self.bot.user.avatar.url)
					embeds.append(embed)

				confirmation = None if payload is None or payload.get("data") is None else Confirm(user=ctx.author)
				try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=confirmation)
				except NotFound: pass

				if confirmation is None:
					return
				await confirmation.wait()

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

				try: await ctx.interaction.edit_original_response(embeds=embeds[:-1], view=None)
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
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /schedule fgi assetType:{assetType} period:{period} start:{start}")
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
		except:
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