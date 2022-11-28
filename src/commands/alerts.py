from os import environ
from time import time
from re import split
from uuid import uuid4
from orjson import dumps, OPT_SORT_KEYS
from aiohttp import ClientSession
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction, TextChannel, Role
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button
from discord.errors import NotFound

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import process_quote_arguments, process_task

from commands.base import BaseCommand


class AlertCommand(BaseCommand):
	alertGroup = SlashCommandGroup("alert", "Set stock and cryptocurrency price alerts.")

	@alertGroup.command(name="set", description="Set stock and cryptocurrency price alerts.")
	async def alert_set(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
		levels: Option(str, "Trigger price for the alert.", name="price"),
		venue: Option(str, "Venue to pull the data from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default=""),
		message: Option(str, "Public message to display on trigger.", name="message", required=False, default=None),
		channel: Option(TextChannel, "Channel to display the alert in.", name="channel", required=False, default=None),
		role: Option(Role, "Role to tag on trigger.", name="role", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if request.price_alerts_available():
				try:
					levels = [float(e) for e in split(", |,", levels)]
				except:
					embed = Embed(title="Invalid price level requested.", description="Make sure the requested level is a valid number. If you're requesting multiple levels, make sure they are all valid and separated with a comma.", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return

				platforms = request.get_platform_order_for("alert")

				responseMessage, task = await process_quote_arguments([venue], platforms, tickerId=tickerId.upper())

				if responseMessage is not None:
					embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alpha.bot/features/price-alerts).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return
				elif len(levels) > 10:
					embed = Embed(title="You can only set up to 10 alerts at a time.", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
					return


				currentPlatform = task.get("currentPlatform")
				currentTask = task.get(currentPlatform)

				response1, response2 = [], []
				if request.is_registered():
					response1 = await self.database.collection(f"details/marketAlerts/{request.accountId}").get()
				response2 = await self.database.collection(f"details/marketAlerts/{request.authorId}").get()
				priceAlerts = [e.to_dict() for e in response1] + [e.to_dict() for e in response2]

				if request.is_registered():
					if len(priceAlerts) >= 500:
						embed = Embed(title="You can only create up to 500 price alerts. Remove some before creating new ones by calling </alert list:928980578739568651>", color=constants.colors["gray"])
						embed.set_author(name="Maximum number of price alerts reached", icon_url=static_storage.icon_bw)
						try: await ctx.interaction.edit_original_response(embed=embed)
						except NotFound: pass
						return
				else:
					if len(priceAlerts) >= 50:
						embed = Embed(title="You can only create up to 50 price alerts. Remove some before creating new ones by calling </alert list:928980578739568651>", description="You can increase your limit to 1000 by signing up for a [free Alpha Account](https://www.alpha.bot/sign-up", color=constants.colors["gray"])
						embed.set_author(name="Maximum number of price alerts reached", icon_url=static_storage.icon_bw)
						try: await ctx.interaction.edit_original_response(embed=embed)
						except NotFound: pass
						return

				payload, responseMessage = await process_task(task, "candle")

				if payload is None or len(payload.get("candles", [])) == 0:
					errorMessage = f"Requested price alert for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
				elif channel is not None and not channel.permissions_for(ctx.author).send_messages:
					embed = Embed(title="You do not have the permission to send messages in the specified channel.", color=constants.colors["gray"])
					embed.set_author(name="Permission denied", icon_url=static_storage.icon_bw)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
				elif channel is None and role is not None:
					embed = Embed(title="You must provide a channel to send the alert to when a role argument is specified.", color=constants.colors["gray"])
					embed.set_author(name="Missing channel", icon_url=static_storage.icon_bw)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
				elif role is not None and not channel.permissions_for(ctx.author).manage_messages:
					embed = Embed(title="You do not have the sufficient permission to tag other server members.", description="To be able to tag other server members with an alert, you must have the `manage messages` permission.", color=constants.colors["gray"])
					embed.set_author(name="Permission denied", icon_url=static_storage.icon_bw)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass
				else:
					for platform in task.get("platforms"): task[platform]["ticker"].pop("tree")

					currentPlatform = payload.get("platform")
					currentTask = task.get(currentPlatform)
					ticker = currentTask.get("ticker")
					tickerDump = dumps(ticker, option=OPT_SORT_KEYS)
					exchange = ticker.get("exchange")
					exchangeName = f" ({exchange.get('name')})" if exchange else ""
					pairQuoteName = " " + ticker.get("quote") if ticker.get("quote") else ""

					newAlerts = []
					for level in levels:
						levelText = "{:,.10f}".format(level).rstrip('0').rstrip('.')

						for alert in priceAlerts:
							alertTicker = alert["request"].get("ticker")

							if dumps(alertTicker, option=OPT_SORT_KEYS) == tickerDump:
								if alert["level"] == level:
									embed = Embed(title=f"Price alert for {ticker.get('name')}{exchangeName} at {levelText}{pairQuoteName} already exists.", color=constants.colors["gray"])
									embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
									try: await ctx.interaction.edit_original_response(embed=embed)
									except NotFound: pass
									return
								elif alert["level"] * 0.999 < level < alert["level"] * 1.001:
									embed = Embed(title="Price alert within 0.1% already exists.", color=constants.colors["gray"])
									embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
									try: await ctx.interaction.edit_original_response(embed=embed)
									except NotFound: pass
									return

						currentLevel = payload["candles"][-1][4]
						currentLevelText = "{:,.10f}".format(currentLevel).rstrip('0').rstrip('.')
						if currentLevel * 0.2 > level or currentLevel * 5 < level:
							embed = Embed(title=f"Your desired alert trigger level at {levelText} {ticker.get('quote')} is too far from the current price of {currentLevelText} {ticker.get('quote')}.", color=constants.colors["gray"])
							embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
							embed.set_footer(text=payload.get("sourceText"))
							try: await ctx.interaction.edit_original_response(embed=embed)
							except NotFound: pass
							return

						newAlerts.append({
							"timestamp": time(),
							"channel": None if channel is None else channel.id,
							"backupChannel": ctx.channel.id,
							"service": "Discord",
							"request": currentTask,
							"currentPlatform": currentPlatform,
							"level": level,
							"levelText": levelText,
							"version": 4,
							"triggerMessage": message,
							"triggerTag": None if role is None else role.id,
							"placement": "above" if level > currentLevel else "below"
						})

					if currentPlatform == "CCXT":
						thumbnailUrl = ticker.get("image")
					else:
						async with ClientSession() as session:
							async with session.get(f"https://cloud.iexapis.com/stable/stock/{ticker.get('symbol')}/logo?token={environ['IEXC_KEY']}") as resp:
								response = await resp.json()
								thumbnailUrl = response["url"]

					if len(newAlerts) == 1:
						description = ""
						if channel is None:
							description += "No channel was specified, so the alert will be sent to your DMs. "
						else:
							description += f"The alert will be sent to the <#{channel.id}> channel. "
						if currentPlatform == "IEXC":
							description += "The alert might trigger with up to 15-minute delay due to data licensing requirements on different exchanges."
						if description == "":
							description = None
						embed = Embed(title=f"Price alert set for {ticker.get('name')}{exchangeName} at {newAlerts[0]['levelText']}{pairQuoteName}.", description=description, color=constants.colors["deep purple"])
						embed.set_author(name="Alert successfully set", icon_url=thumbnailUrl)
					else:
						description = ""
						if channel is None:
							description += "No channel was specified, so alerts will be sent to your DMs. "
						else:
							description += f"Alerts will be sent to the <#{channel.id}> channel. "
						if currentPlatform == "IEXC":
							description += "Alerts might trigger with up to 15-minute delay due to data licensing requirements on different exchanges."
						if description == "":
							description = None
						levelsText = ", ".join([e["levelText"] for e in newAlerts])
						embed = Embed(title=f"Price alerts set for {ticker.get('name')}{exchangeName} at {levelsText}{pairQuoteName}.", description=description, color=constants.colors["deep purple"])
						embed.set_author(name="Alerts successfully set", icon_url=thumbnailUrl)
					try: await ctx.interaction.edit_original_response(embed=embed)
					except NotFound: pass

					for newAlert in newAlerts:
						alertId = str(uuid4())
						if request.is_registered():
							await self.database.document(f"details/marketAlerts/{request.accountId}/{alertId}").set(newAlert)
						else:
							await self.database.document(f"details/marketAlerts/{request.authorId}/{alertId}").set(newAlert)

					await self.database.document("discord/statistics").set({request.snapshot: {"alert": Increment(len(levels))}}, merge=True)
					await self.cleanup(ctx, request)

			else:
				embed = Embed(title=":gem: Price Alerts are available as an add-on subscription for communities or individuals for only $2.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/price-alerts).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alpha.bot/files/uploads/pro-hero.jpg")
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /alert set {tickerId} {levels} {venue} {message} {channel.id}")
			await self.unknown_error(ctx)

	@alertGroup.command(name="list", description="List all price alerts.")
	async def alert_list(
		self,
		ctx
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			response1, response2 = [], []
			if request.is_registered():
				response1 = await self.database.collection(f"details/marketAlerts/{request.accountId}").get()
			response2 = await self.database.collection(f"details/marketAlerts/{request.authorId}").get()
			priceAlerts = [(e.id, e.to_dict(), request.accountId) for e in response1] + [(e.id, e.to_dict(), request.authorId) for e in response2]
			totalAlertCount = len(priceAlerts)

			if totalAlertCount == 0:
				embed = Embed(title="You haven't set any alerts yet.", color=constants.colors["gray"])
				embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

			else:
				embed = Embed(title=f"You've scheduled {totalAlertCount} price alert{'' if totalAlertCount == 1 else 's'}.", color=constants.colors["light blue"])
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass

				for key, alert, matchedId in priceAlerts:
					ticker = alert["request"].get("ticker")
					exchangeName = f" ({ticker.get('exchange').get('name')})" if ticker.get("exchange") else ""
					pairQuoteName = " " + ticker.get("quote") if ticker.get("quote") else ""

					embed = Embed(title=f"{ticker.get('name')}{exchangeName} price alert at {alert.get('levelText', alert['level'])}{pairQuoteName}.", color=constants.colors["deep purple"])
					await ctx.followup.send(embed=embed, view=DeleteView(database=self.database, pathId=f"details/marketAlerts/{matchedId}/{key}", userId=request.authorId), ephemeral=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /alert list")


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
		embed = Embed(title="Alert deleted", color=constants.colors["gray"])
		await interaction.response.edit_message(embed=embed, view=None)