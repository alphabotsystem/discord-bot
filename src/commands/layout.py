from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError, sleep
from traceback import format_exc

from discord import Embed, File, ButtonStyle, SelectOption, Interaction, PartialEmoji
from discord.commands import slash_command, Option
from discord.ui import View, button, Button, Select
from discord.errors import NotFound
from google.cloud.firestore import Increment
from google.cloud.firestore_v1.base_query import FieldFilter

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage
from Processor import autocomplete_layout_timeframe

from commands.base import BaseCommand, MediaActionsView, TryV2View, autocomplete_layouts, files_from_posts, content_from_posts


class LayoutCommand(BaseCommand):
	@slash_command(name="layout", description="Pull TradingView Layouts with custom indicators, drawings and strategies", guild_only=True)
	async def layout(
		self,
		ctx,
		name: Option(str, "Name of the layout to pull.", name="name", autocomplete=autocomplete_layouts),
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
		timeframe: Option(str, "Preferred chart timeframe to use.", name="timeframe", autocomplete=autocomplete_layout_timeframe, required=False, default=""),
		venue: Option(str, "Venue to pull the chart from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			prelightCheckpoint = time()
			request.set_delay("prelight", prelightCheckpoint - request.start)

			[layout, _] = await gather(
				self.database.collection(f"discord/properties/layouts").where(filter=FieldFilter("label", "==", name)).where(filter=FieldFilter("guildId", "==", str(request.guildId))).get(),
				ctx.defer()
			)

			if len(layout) == 0:
				embed = Embed(title="Layout not found", description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/layouts"), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			layout = layout[0].to_dict()

			if not request.tradingview_layouts_available():
				embed = Embed(title=":gem: TradingView Layouts are available for $10.00 per month.", description="If you'd like to start your 30-day free trial, visit [our website](https://www.alpha.bot/pro/tradingview-layouts).", color=constants.colors["deep purple"])
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			# Layout name leads as the v2 subject (greedy-matched against the imported
			# guild layout), then ticker, then modifiers carried by the saved layout.
			parts = ["layout", name, tickerId] + [p for p in [timeframe, venue] if p]
			if layout.get("isWide", False): parts.append("wide")
			if layout.get("theme"): parts.append(layout["theme"])

			response = await self.render_via_v2(" ".join(parts), request, layout={"label": name, "url": layout["url"]})

			request.set_delay("parser", time() - prelightCheckpoint)
			await self.respond(ctx, request, response)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /layout {layout['url']} {tickerId} timeframe:{timeframe} venue:{venue}")
			await self.unknown_error(ctx)

	async def respond(
		self,
		ctx,
		request,
		response
	):
		start = time()

		if not response.get("ok"):
			message = response.get("error") or "Requested chart is not available."
			description = get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/layouts")
			embed = Embed(title=message, description=description, color=constants.colors["gray"])
			embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
			try: await ctx.interaction.edit_original_response(embed=embed)
			except NotFound: pass
			return

		posts = response.get("posts", [])
		meta = response.get("meta", {})
		files = files_from_posts(posts, request.authorId)
		content = content_from_posts(posts)

		embeds = []
		symbols = meta.get("resolvedSymbols", [])
		# RwU79szBNJUFmrpQbgj3ZtnLmwA2
		if self.bot.user.id == 1229893549986811986 and len(files) != 0 and symbols:
			symbol = symbols[0]
			embed = Embed(title=f"Chart for {symbol} (`{symbol.split(':')[-1]}`)", color=constants.colors["deep purple"])
			embeds.append(embed)

		isLicensed = self.bot.user.id not in constants.PRIMARY_BOTS
		actions = None
		if len(files) != 0:
			actions = MediaActionsView(user=ctx.author, command=ctx.command.mention, include_v2=not isLicensed)

		requestCheckpoint = time()
		request.set_delay("request", (requestCheckpoint - start) / max(1, len(files) + len(embeds)))
		try: await ctx.interaction.edit_original_response(content=content, embeds=embeds, files=files, view=actions)
		except NotFound: pass
		request.set_delay("response", time() - requestCheckpoint)

		await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(1)}}, merge=True)
		await self.log_request_v2("layouts", request, meta, telemetry=request.telemetry)
		await self.cleanup(ctx, request, removeView=True, persistView=TryV2View() if len(files) != 0 and not isLicensed else None)

