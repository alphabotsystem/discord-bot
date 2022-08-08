from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed
from discord.embeds import EmptyEmbed
from discord.commands import slash_command, Option

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand


class VolumeCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		task
	):
		currentTask = task.get(task.get("currentPlatform"))
		payload, quoteText = await Processor.process_http_task("quote", request.authorId, task)

		if payload is None or "quoteVolume" not in payload:
			errorMessage = f"Requested volume for `{currentTask.get('ticker').get('name')}` is not available." if quoteText is None else quoteText
			embed = Embed(title=errorMessage, color=constants.colors["gray"])
			embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
			await ctx.interaction.edit_original_message(embed=embed)
		else:
			currentTask = task.get(payload.get("platform"))
			embed = Embed(title=payload["quoteVolume"], description=payload.get("quoteConvertedVolume", EmptyEmbed), color=constants.colors["orange"])
			embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
			embed.set_footer(text=payload["sourceText"])
			await ctx.interaction.edit_original_message(embed=embed)
		
		await self.database.document("discord/statistics").set({request.snapshot: {"v": Increment(1)}}, merge=True)

	@slash_command(name="volume", description="Fetch stock and crypto 24-hour volume.")
	async def volume(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default=""),
		venue: Option(str, "Venue to pull the volume from.", name="venue", autocomplete=BaseCommand.get_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			defaultPlatforms = request.get_platform_order_for("v", assetType=assetType)
			preferredPlatforms = BaseCommand.sources["v"].get(assetType)
			platforms = [e for e in defaultPlatforms if preferredPlatforms is None or e in preferredPlatforms]

			arguments = [venue]
			outputMessage, task = await Processor.process_quote_arguments(request, arguments, platforms, tickerId=tickerId.upper())

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/volume).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, task)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /volume {tickerId} type:{assetType} venue:{venue}")
			await self.unknown_error(ctx)