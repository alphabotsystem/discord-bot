from os import environ
from time import time
from re import split
from uuid import uuid4
from orjson import dumps, OPT_SORT_KEYS
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction, TextChannel
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ui import View, button, Button

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand


class AlertCommand(BaseCommand):
	alertGroup = SlashCommandGroup("alert", "Set stock and cryptocurrency price alerts.")

	@alertGroup.command(name="set", description="Set stock and cryptocurrency price alerts.")
	async def alert_set(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		levels: Option(str, "Trigger price for the alert.", name="price"),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default=""),
		venue: Option(str, "Venue to pull the data from.", name="venue", autocomplete=BaseCommand.get_venues, required=False, default=""),
		message: Option(str, "Public message to display on trigger.", name="message", required=False, default=None),
		channel: Option(TextChannel, "Channel to display the alert in.", name="channel", required=False, default=None),
		role: Option(TextChannel, "Role to tag on trigger.", name="role", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			try:
				levels = [float(e) for e in split(", |,", levels)]
			except:
				embed = Embed(title="Invalid price level requested.", description="Make sure the requested level is a valid number. If you're requesting multiple levels, make sure they are all valid and separated with a comma.", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			defaultPlatforms = request.get_platform_order_for("alert", assetType=assetType)
			preferredPlatforms = BaseCommand.sources["alert"].get(assetType)
			platforms = [e for e in defaultPlatforms if preferredPlatforms is None or e in preferredPlatforms]

			if request.price_alerts_available():
				arguments = [venue]
				outputMessage, task = await Processor.process_quote_arguments(request, arguments, platforms, tickerId=tickerId.upper())

				if outputMessage is not None:
					embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/price-alerts).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return

				currentPlatform = task.get("currentPlatform")
				currentTask = task.get(currentPlatform)

				response1, response2 = [], []
				if request.is_registered():
					response1 = await self.database.collection(f"details/marketAlerts/{request.accountId}").get()
				response2 = await self.database.collection(f"details/marketAlerts/{request.authorId}").get()
				priceAlerts = [e.to_dict() for e in response1] + [e.to_dict() for e in response2]

				if len(priceAlerts) >= 50:
					embed = Embed(title="You can only create up to 50 price alerts.", color=constants.colors["gray"])
					embed.set_author(name="Maximum number of price alerts reached", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)

				payload, quoteText = await Processor.process_task("candle", request.authorId, task)

				if payload is None or len(payload.get("candles", [])) == 0:
					errorMessage = f"Requested price alert for `{currentTask.get('ticker').get('name')}` is not available." if quoteText is None else quoteText
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
				elif channel is not None and not channel.permissions_for(ctx.author).send_messages:
					embed = Embed(title="You do not have the permission to send messages in this channel.", color=constants.colors["gray"])
					embed.set_author(name="Permission denied", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
				elif channel is not None and not channel.permissions_for(ctx.guild.me).send_messages:
					embed = Embed(title="Alpha doesn't have the permission to send messages in this channel.", color=constants.colors["gray"])
					embed.set_author(name="Permission denied", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
				else:
					currentPlatform = payload.get("platform")
					currentTask = task.get(currentPlatform)
					ticker = currentTask.get("ticker")
					tickerHash = hash(dumps(ticker, option=OPT_SORT_KEYS))
					exchange = ticker.get("exchange")
					exchangeName = f" ({exchange.get('name')})" if exchange else ""
					pairQuoteName = " " + ticker.get("quote") if ticker.get("quote") else ""

					for platform in task.get("platforms"): task[platform]["ticker"].pop("tree")

					newAlerts = []
					for level in levels:
						levelText = "{:,.10f}".format(level).rstrip('0').rstrip('.')

						for alert in priceAlerts:
							currentAlertPlatform = alert["request"].get("currentPlatform")
							currentAlertRequest = alert["request"].get(currentAlertPlatform)
							alertTicker = currentAlertRequest.get("ticker")

							if currentAlertPlatform == currentPlatform and hash(dumps(alertTicker, option=OPT_SORT_KEYS)) == tickerHash:
								if alert["level"] == level:
									embed = Embed(title=f"Price alert for {ticker.get('name')}{exchangeName} at {levelText}{pairQuoteName} already exists.", color=constants.colors["gray"])
									embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
									await ctx.interaction.edit_original_message(embed=embed)
									return
								elif alert["level"] * 0.999 < level < alert["level"] * 1.001:
									embed = Embed(title="Price alert within 0.1% already exists.", color=constants.colors["gray"])
									embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
									await ctx.interaction.edit_original_message(embed=embed)
									return

						currentLevel = payload["candles"][-1][4]
						currentLevelText = "{:,.10f}".format(currentLevel).rstrip('0').rstrip('.')
						if currentLevel * 0.2 > level or currentLevel * 5 < level:
							embed = Embed(title=f"Your desired alert trigger level at {levelText} {ticker.get('quote')} is too far from the current price of {currentLevelText} {ticker.get('quote')}.", color=constants.colors["gray"])
							embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
							embed.set_footer(text=payload.get("sourceText"))
							await ctx.interaction.edit_original_message(embed=embed)
							return

						newAlerts.append({
							"timestamp": time(),
							"channel": None if channel is None else channel.id,
							"backupChannel": ctx.channel.id,
							"service": "Discord",
							"request": task,
							"level": level,
							"levelText": levelText,
							"version": 4,
							"triggerMessage": message,
							"triggerTag": None if role is None else role.id,
							"placement": "above" if level > currentLevel else "below"
						})

					if len(newAlerts) == 1:
						embed = Embed(title=f"Price alert set for {ticker.get('name')}{exchangeName} at {newAlerts[0]['levelText']}{pairQuoteName}.", color=constants.colors["deep purple"])
						if currentPlatform in ["IEXC"]: embed.description = "The alert might trigger with up to 15-minute delay due to data licencing requirements on different exchanges."
						embed.set_author(name="Alert successfully set", icon_url=static_storage.icon)
					else:
						levelsText = ", ".join([e["levelText"] for e in newAlerts])
						embed = Embed(title=f"Price alerts set for {ticker.get('name')}{exchangeName} at {levelsText}{pairQuoteName}.", color=constants.colors["deep purple"])
						if currentPlatform in ["IEXC"]: embed.description = "Alerts might trigger with up to 15-minute delay due to data licencing requirements on different exchanges."
						embed.set_author(name="Alerts successfully set", icon_url=static_storage.icon)
					await ctx.interaction.edit_original_message(embed=embed)

					for newAlert in newAlerts:
						alertId = str(uuid4())
						if request.is_registered():
							await self.database.document(f"details/marketAlerts/{request.accountId}/{alertId}").set(newAlert)
						else:
							await self.database.document(f"details/marketAlerts/{request.authorId}/{alertId}").set(newAlert)

					await self.database.document("discord/statistics").set({request.snapshot: {"alert": Increment(len(levels))}}, merge=True)
					await self.cleanup(ctx, request)

			else:
				embed = Embed(title=":gem: Price Alerts are available as an Alpha Pro Subscription for individuals or communities for only $2.00 per month.", description="If you'd like to start your 30-day free trial, visit your [subscription page](https://www.alphabotsystem.com/subscriptions).", color=constants.colors["deep purple"])
				# embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /alert set {tickerId} {levels} {assetType} {venue} {message} {channel}")
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
				await ctx.interaction.edit_original_message(embed=embed)

			else:
				embed = Embed(title=f"You've scheduled {totalAlertCount} price alert{'' if totalAlertCount == 1 else 's'}.", color=constants.colors["light blue"])
				await ctx.interaction.edit_original_message(embed=embed)

				for key, alert, matchedId in priceAlerts:
					currentPlatform = alert["request"].get("currentPlatform")
					currentTask = alert["request"].get(currentPlatform)
					ticker = currentTask.get("ticker")
					exchangeName = f" ({ticker.get('exchange').get('name')})" if ticker.get("exchange") else ""
					pairQuoteName = " " + ticker.get("quote") if ticker.get("quote") else ""

					embed = Embed(title=f"{ticker.get('name')}{exchangeName} price alert at {alert.get('levelText', alert['level'])}{pairQuoteName}.", color=constants.colors["deep purple"])
					await ctx.followup.send(embed=embed, view=DeleteView(database=self.database, pathId=matchedId, alertId=key, userId=request.authorId), ephemeral=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /alert list")


class DeleteView(View):
	def __init__(self, database, pathId, alertId, userId=None):
		super().__init__(timeout=None)
		self.database = database
		self.pathId = pathId
		self.alertId = alertId
		self.userId = userId

	@button(label="Delete", style=ButtonStyle.danger)
	async def delete(self, button: Button, interaction: Interaction):
		if self.userId != interaction.user.id: return
		await self.database.document(f"details/marketAlerts/{self.pathId}/{self.alertId}").delete()
		embed = Embed(title="Alert deleted", color=constants.colors["gray"])
		await interaction.response.edit_message(embed=embed, view=None)