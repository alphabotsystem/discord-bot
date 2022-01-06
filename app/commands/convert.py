from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed
from discord.commands import slash_command, SlashCommandGroup, Option

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
			request = await self.create_request(ctx, autodelete=-1)
			if request is None: return

			payload, quoteText = await Processor.process_conversion(request, fromTicker.upper(), toTicker.upper(), amount)

			if payload is None:
				errorMessage = "Requested conversion is not available." if quoteText is None else quoteText
				embed = Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Conversion not available", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
			else:
				embed = Embed(title="{} ≈ {}".format(payload["quotePrice"], payload["quoteConvertedPrice"]), color=constants.colors[payload["messageColor"]])
				embed.set_author(name="Conversion", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

			await self.database.document("discord/statistics").set({request.snapshot: {"convert": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /convert {} {} {}".format(ctx.author.id, fromTicker, toTicker, amount))
