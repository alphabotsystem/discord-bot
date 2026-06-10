from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError
from traceback import format_exc

from discord import Embed, File
from discord.commands import slash_command, Option
from discord.errors import NotFound
from google.cloud.firestore import Increment

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage
from Processor import autocomplete_hmap_timeframe, autocomplete_market, autocomplete_category, autocomplete_size, autocomplete_group

from commands.base import BaseCommand, MediaActionsView, TryV2View, autocomplete_hmap_type, files_from_posts, content_from_posts


async def autocomplete_theme(ctx):
	options = ["light", "dark"]
	currentInput = " ".join(ctx.options.get("theme", "").lower().split())
	return [e for e in options if e.startswith(currentInput)]


class HeatmapCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		response
	):
		start = time()

		if not response.get("ok"):
			message = response.get("error") or "Requested heatmap is not available."
			description = get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/heatmaps")
			embed = Embed(title=message, description=description, color=constants.colors["gray"])
			embed.set_author(name="Invalid argument", icon_url=static_storage.error_icon)
			try: await ctx.interaction.edit_original_response(embed=embed)
			except NotFound: pass
			return

		posts = response.get("posts", [])
		meta = response.get("meta", {})
		files = files_from_posts(posts, request.authorId)
		content = content_from_posts(posts)

		isLicensed = self.bot.user.id not in constants.PRIMARY_BOTS
		actions = None
		if len(files) != 0:
			if self.bot.user.id not in DISABLE_DELETE_BUTTON:
				actions = MediaActionsView(user=ctx.author, command=ctx.command.mention, include_v2=not isLicensed)
			elif not isLicensed:
				actions = TryV2View()

		requestCheckpoint = time()
		request.set_delay("request", (requestCheckpoint - start) / max(1, len(files)))
		try: await ctx.interaction.edit_original_response(content=content, embeds=[], files=files, view=actions)
		except NotFound: pass
		request.set_delay("response", time() - requestCheckpoint)

		await self.database.document("discord/statistics").set({request.snapshot: {"hmap": Increment(meta.get("requestCount", 1))}}, merge=True)
		await self.log_request_v2("hmap", request, meta, telemetry=request.telemetry)
		await self.cleanup(ctx, request, removeView=True, persistView=TryV2View() if len(files) != 0 and not isLicensed else None)

	@slash_command(name="hmap", description="Pull market heatmaps from TradingView.")
	async def hmap(
		self,
		ctx,
		assetType: Option(str, "Heatmap asset class.", name="type", autocomplete=autocomplete_hmap_type, required=False, default=""),
		timeframe: Option(str, "Timeframe and coloring method for the heatmap.", name="color", autocomplete=autocomplete_hmap_timeframe, required=False, default=""),
		market: Option(str, "Heatmap market.", name="market", autocomplete=autocomplete_market, required=False, default=""),
		category: Option(str, "Specific asset category.", name="category", autocomplete=autocomplete_category, required=False, default=""),
		size: Option(str, "Method used to determine heatmap's block sizes.", name="size", autocomplete=autocomplete_size, required=False, default=""),
		group: Option(str, "Asset grouping method.", name="group", autocomplete=autocomplete_group, required=False, default=""),
		theme: Option(str, "Heatmap color theme.", name="theme", autocomplete=autocomplete_theme, required=False, default=""),
		autodelete: Option(float, "Bot response self destruct timer in minutes.", name="autodelete", required=False, default=None)
	):
		try:
			request = await self.create_request(ctx, autodelete=autodelete)
			if request is None: return

			if autodelete is not None and (autodelete < 1 or autodelete > 10):
				embed = Embed(title="Response autodelete duration must be between one and ten minutes.", color=constants.colors["gray"])
				try: await ctx.respond(embed=embed)
				except NotFound: pass
				return

			prelightCheckpoint = time()
			request.set_delay("prelight", prelightCheckpoint - request.start)

			# The class (assetType) leads as the v2 heatmap subject; everything else
			# trails as modifiers for the walker to resolve per-market.
			parts = ["hmap"] + [p for p in [assetType, market, timeframe, category, size, group, theme] if p]
			await ctx.defer()
			response = await self.render_via_v2(" ".join(parts), request)

			request.set_delay("parser", time() - prelightCheckpoint)
			await self.respond(ctx, request, response)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /hmap assetType:{assetType} color:{timeframe} market:{market} category:{category} size:{size} group:{group} theme:{theme} autodelete:{autodelete}")
			await self.unknown_error(ctx)


DISABLE_DELETE_BUTTON = []