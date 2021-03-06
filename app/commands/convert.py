from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed
from discord.commands import slash_command, Option

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand


class ConvertCommand(BaseCommand):
	@slash_command(name="convert", description="Convert between currencies, rates and assets.")
	async def convert(
		self,
		ctx,
		fromTicker: Option(str, "Ticker to convert from.", name="from"),
		toTicker: Option(str, "Ticker to convert to.", name="to"),
		amount: Option(float, "Amount to convert.", name="amount")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			defaultPlatforms = request.get_platform_order_for("convert")

			payload, quoteText = await Processor.process_conversion(request, fromTicker.upper(), toTicker.upper(), amount, defaultPlatforms)

			if payload is None:
				errorMessage = "Requested conversion is not available." if quoteText is None else quoteText
				embed = Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Conversion not available", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = Embed(title=f"{payload['quotePrice']} ≈ {payload['quoteConvertedPrice']}", color=constants.colors[payload["messageColor"]])
				embed.set_author(name="Conversion", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

			await self.database.document("discord/statistics").set({request.snapshot: {"convert": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /convert {fromTicker} {toTicker} {amount}")
			await self.unknown_error(ctx)
