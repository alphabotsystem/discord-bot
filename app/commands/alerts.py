from os import environ
from time import time
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
		level: Option(float, "Trigger price for the alert.", name="price"),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default=""),
		venue: Option(str, "Venue to pull the data from.", name="venue", autocomplete=BaseCommand.get_venues, required=False, default=""),
		message: Option(str, "Public message to display on trigger.", name="message", required=False, default=None),
		channel: Option(TextChannel, "Channel to display the alert in.", name="channel", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			if request.price_alerts_available():
				arguments = [venue]
				outputMessage, task = await Processor.process_quote_arguments(request, arguments, tickerId=tickerId.upper(), excluded=["CoinGecko", "LLD"])

				if outputMessage is not None:
					embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/pro/price-alerts).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return

				currentPlatform = task.get("currentPlatform")
				currentTask = task.get(currentPlatform)

				response1, response2 = [], []
				if request.is_registered():
					response1 = await self.database.collection("details/marketAlerts/{}".format(request.accountId)).get()
				response2 = await self.database.collection("details/marketAlerts/{}".format(request.authorId)).get()
				marketAlerts = [e.to_dict() for e in response1] + [e.to_dict() for e in response2]

				if len(marketAlerts) >= 50:
					embed = Embed(title="You can only create up to 50 price alerts.", color=constants.colors["gray"])
					embed.set_author(name="Maximum number of price alerts reached", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)

				payload, quoteText = await Processor.process_task("candle", request.authorId, task)

				if payload is None or len(payload.get("candles", [])) == 0:
					errorMessage = "Requested price alert for `{}` is not available.".format(currentTask.get("ticker").get("name")) if quoteText is None else quoteText
					embed = Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
				else:
					currentPlatform = payload.get("platform")
					currentTask = task.get(currentPlatform)
					ticker = currentTask.get("ticker")
					exchange = ticker.get("exchange")

					levelText = "{:,.10f}".format(level).rstrip('0').rstrip('.')

					for platform in task.get("platforms"): task[platform]["ticker"].pop("tree")
					newAlert = {
						"timestamp": time(),
						"channel": None if channel is None else channel.id,
						"service": "Discord",
						"request": task,
						"level": level,
						"levelText": levelText,
						"version": 4,
						"triggerMessage": message
					}
					alertId = str(uuid4())
					hashName = hash(dumps(ticker, option=OPT_SORT_KEYS))

					for alert in marketAlerts:
						currentAlertPlatform = alert["request"].get("currentPlatform")
						currentAlertRequest = alert["request"].get(currentAlertPlatform)
						alertTicker = currentAlertRequest.get("ticker")

						if currentAlertPlatform == currentPlatform and hash(dumps(alertTicker, option=OPT_SORT_KEYS)) == hashName:
							if alert["level"] == newAlert["level"]:
								embed = Embed(title="Price alert for {}{} at {}{} already exists.".format(ticker.get("name"), "" if not bool(exchange) else " ({})".format(exchange.get("name")), levelText, "" if ticker.get("quote") is None else " " + ticker.get("quote")), color=constants.colors["gray"])
								embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
								await ctx.interaction.edit_original_message(embed=embed)
								return
							elif alert["level"] * 0.999 < newAlert["level"] < alert["level"] * 1.001:
								embed = Embed(title="Price alert within 0.1% already exists.", color=constants.colors["gray"])
								embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
								await ctx.interaction.edit_original_message(embed=embed)
								return

					currentLevel = payload["candles"][-1][4]
					currentLevelText = "{:,.10f}".format(currentLevel).rstrip('0').rstrip('.')
					if currentLevel * 0.5 > newAlert["level"] or currentLevel * 2 < newAlert["level"]:
						embed = Embed(title="Your desired alert trigger level at {} {} is too far from the current price of {} {}.".format(levelText, ticker.get("quote"), currentLevelText, ticker.get("quote")), color=constants.colors["gray"])
						embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
						embed.set_footer(text=payload.get("sourceText"))
						await ctx.interaction.edit_original_message(embed=embed)
						return

					newAlert["placement"] = "above" if newAlert["level"] > currentLevel else "below"

					embed = Embed(title="Price alert set for {}{} at {}{}.".format(ticker.get("name"), "" if not bool(exchange) else " ({})".format(exchange.get("name")), levelText, "" if ticker.get("quote") is None else " " + ticker.get("quote")), color=constants.colors["deep purple"])
					if currentPlatform in ["IEXC"]: embed.description = "The alert might trigger with up to 15-minute delay due to data licencing requirements on different exchanges."
					embed.set_author(name="Alert successfully set", icon_url=static_storage.icon)
					await ctx.interaction.edit_original_message(embed=embed)

					if not request.is_registered():
						await self.database.document("details/marketAlerts/{}/{}".format(request.authorId, alertId)).set(newAlert)
					elif request.serverwide_price_alerts_available():
						await self.database.document("details/marketAlerts/{}/{}".format(request.accountId, alertId)).set(newAlert)
					elif request.personal_price_alerts_available():
						await self.database.document("details/marketAlerts/{}/{}".format(request.accountId, alertId)).set(newAlert)
						await self.database.document("accounts/{}".format(request.accountId)).set({"customer": {"addons": {"marketAlerts": 1}}}, merge=True)

					await self.database.document("discord/statistics").set({request.snapshot: {"alert": Increment(1)}}, merge=True)
					await self.cleanup(ctx, request)

			elif request.is_pro():
				embed = Embed(title=":bell: Price Alerts are disabled.", description="You can enable Price Alerts feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(request.guildId), color=constants.colors["gray"])
				embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

			else:
				embed = Embed(title=":gem: Price Alerts are available to Alpha Pro users or communities for only $2.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
				embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /alert set {} {} {} {} {} {}".format(ctx.author.id, tickerId, level, assetType, venue, message, channel))

	@alertGroup.command(name="list", description="List all price alerts.")
	async def alert_list(
		self,
		ctx
	):
		try:
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			response1, response2 = [], []
			if request.is_registered():
				response1 = await self.database.collection("details/marketAlerts/{}".format(request.accountId)).get()
			response2 = await self.database.collection("details/marketAlerts/{}".format(request.authorId)).get()
			marketAlerts = [(e.id, e.to_dict(), request.accountId) for e in response1] + [(e.id, e.to_dict(), request.authorId) for e in response2]
			totalAlertCount = len(marketAlerts)

			if totalAlertCount == 0:
				embed = Embed(title="You haven't set any alerts yet.", color=constants.colors["gray"])
				embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

			else:
				embed = Embed(title="You've scheduled {} price alert{}.".format(totalAlertCount, "" if totalAlertCount == 1 else "s"), color=constants.colors["light blue"])
				await ctx.interaction.edit_original_message(embed=embed)

				for key, alert, matchedId in marketAlerts:
					currentPlatform = alert["request"].get("currentPlatform")
					currentTask = alert["request"].get(currentPlatform)
					ticker = currentTask.get("ticker")

					embed = Embed(title="{}{} price alert at {}{}.".format(ticker.get("name"), " ({})".format(ticker.get("exchange").get("name")) if ticker.get("exchange") else "", alert.get("levelText", alert["level"]), "" if ticker.get("quote") is None else " " + ticker.get("quote")), color=constants.colors["deep purple"])
					await ctx.channel.send(embed=embed, view=DeleteView(database=self.database, authorId=request.authorId, pathId=matchedId, alertId=key), ephemeral=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /alert list".format(ctx.author.id))


class DeleteView(View):
	def __init__(self, database, authorId, pathId, alertId):
		super().__init__(timeout=None)
		self.database = database
		self.authorId = authorId
		self.pathId = pathId
		self.alertId = alertId

	@button(label="Delete", style=ButtonStyle.danger)
	async def delete(self, button: Button, interaction: Interaction):
		if self.authorId != interaction.user.id: return
		await self.database.document("details/marketAlerts/{}/{}".format(self.pathId, self.alertId)).delete()
		embed = Embed(title="Alert deleted", color=constants.colors["gray"])
		await interaction.response.edit_message(embed=embed, view=None)